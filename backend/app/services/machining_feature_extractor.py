"""
Hybrid Machining Feature Extractor

Combines OCR + geometry segments to extract main turning features:
- main_turning_od_in: Main turning OD (not raw stock)
- main_bore_id_in: Main bore ID
- main_turning_len_in: Turning length

This is DIFFERENT from envelope/overall dimensions.

IMPORTANT: This module is pure in-memory. It MUST NOT call any PDF/OCR/disk I/O.
It relies on part_summary.segments and part_summary.inference_metadata.raw_dimensions
being populated by the caller before invocation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _safe_float(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None


def _abs_rel_err(a: float, b: float) -> float:
    if a == 0 and b == 0:
        return 0.0
    denom = max(abs(a), abs(b), 1e-9)
    return abs(a - b) / denom


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


@dataclass
class OCRDim:
    kind: str  # "OD" | "ID" | "LEN"
    value_in: float
    conf: float
    text: str
    is_tolerance: bool = False


@dataclass
class Segment:
    idx: int
    z_start: float
    z_end: float
    od: float
    id: float
    conf: float

    @property
    def length(self) -> float:
        return max(0.0, self.z_end - self.z_start)


_DIAM_TOKENS = ("Ø", "DIA", "DIAM", "O.D", "OD", "I.D", "ID", "BORE", "INNER")
_LEN_TOKENS = ("LEN", "LENGTH", "OAL", "OVERALL")


def extract_ocr_dims(part_summary: Dict[str, Any]) -> List[OCRDim]:
    """Extract OCR dimensions from part_summary.inference_metadata.raw_dimensions.

    Pure in-memory — no PDF or disk I/O.
    """
    meta = (part_summary.get("inference_metadata") or {})
    raw = meta.get("raw_dimensions") or meta.get("ocr_dimensions") or []
    out: List[OCRDim] = []

    for d in raw:
        txt = str(d.get("text") or d.get("raw_text") or "").upper().replace("\u2300", "Ø")
        v = _safe_float(d.get("value_in") or d.get("value") or d.get("inch"))
        if v is None:
            continue
        conf = float(d.get("confidence") or 0.7)
        is_tol = bool(d.get("is_tolerance") or False)

        kind = str(d.get("kind") or "").upper().strip()
        if kind not in ("OD", "ID", "LEN"):
            if any(k in txt for k in ("ID", "I.D", "BORE", "INNER")):
                kind = "ID"
            elif any(k in txt for k in ("OD", "O.D", "Ø", "DIA", "DIAM")):
                kind = "OD"
            elif any(k in txt for k in _LEN_TOKENS):
                kind = "LEN"
            else:
                kind = "OD"

        out.append(OCRDim(kind=kind, value_in=v, conf=conf, text=txt, is_tolerance=is_tol))

    out = [d for d in out if "[" not in d.text and "]" not in d.text]
    return out


def extract_segments(part_summary: Dict[str, Any]) -> List[Segment]:
    segs = part_summary.get("segments") or []
    out: List[Segment] = []
    for i, s in enumerate(segs):
        z0 = _safe_float(s.get("z_start"))
        z1 = _safe_float(s.get("z_end"))
        od = _safe_float(s.get("od_diameter"))
        id_ = _safe_float(s.get("id_diameter") or 0.0)
        conf = float(s.get("confidence") or 0.0)
        if None in (z0, z1, od):
            continue
        out.append(Segment(idx=i, z_start=z0, z_end=z1, od=od, id=float(id_ or 0.0), conf=conf))
    return out


def pick_main_turning_od_candidate(ocr: List[OCRDim]) -> Optional[OCRDim]:
    ods = [d for d in ocr if d.kind == "OD" and 0.25 <= d.value_in <= 5.0]
    if not ods:
        return None
    ods.sort(key=lambda d: (d.value_in, d.conf), reverse=True)
    return ods[0]


def match_od_to_segment(od_in: float, segments: List[Segment], total_len: float) -> Optional[Tuple[Segment, float]]:
    if not segments:
        return None

    best: Optional[Tuple[Segment, float]] = None
    for seg in segments:
        if seg.length < 0.05 * max(total_len, 1e-6):
            continue

        rel = _abs_rel_err(seg.od, od_in)
        od_score = 1.0 - _clamp01(rel / 0.08)
        len_score = _clamp01(seg.length / max(total_len, 1e-6))
        conf_score = _clamp01(seg.conf)
        score = 0.55 * od_score + 0.30 * len_score + 0.15 * conf_score

        if best is None or score > best[1]:
            best = (seg, score)

    return best


def pick_main_bore_id(ocr: List[OCRDim], seg: Segment) -> Optional[OCRDim]:
    ids = [d for d in ocr if d.kind == "ID" and 0.05 <= d.value_in <= 5.0]
    if not ids:
        return None
    if seg.id and seg.id > 0.02:
        ids.sort(key=lambda d: (_abs_rel_err(d.value_in, seg.id), -d.conf))
        return ids[0]
    ids.sort(key=lambda d: (d.value_in, d.conf), reverse=True)
    return ids[0]


def pick_turning_length(ocr: List[OCRDim], seg: Segment) -> float:
    lens = [d for d in ocr if d.kind == "LEN" and 0.10 <= d.value_in <= 20.0]
    if not lens:
        return seg.length
    lens.sort(key=lambda d: (_abs_rel_err(d.value_in, seg.length), -d.conf))
    return lens[0].value_in


def extract_machining_features(part_summary: Dict[str, Any]) -> Dict[str, Any]:
    """Pure in-memory machining feature extraction. O(n) on segments + OCR dims.

    Caller MUST populate part_summary.inference_metadata.raw_dimensions before calling.
    """
    ocr = extract_ocr_dims(part_summary)
    segs = extract_segments(part_summary)

    totals = part_summary.get("totals") or {}
    total_len = _safe_float(totals.get("total_length_in"))
    if total_len is None:
        zr = part_summary.get("z_range") or [0.0, 0.0]
        total_len = float((_safe_float(zr[1]) or 0.0) - (_safe_float(zr[0]) or 0.0))
    total_len = max(float(total_len), 1e-6)

    od_dim = pick_main_turning_od_candidate(ocr)
    if od_dim is None:
        return {"status": "NO_OCR_OD", "reason": "No OCR OD candidates found", "ocr_candidates_count": len(ocr), "geom_segments_count": len(segs)}

    match = match_od_to_segment(od_dim.value_in, segs, total_len)
    if match is None:
        return {"status": "NO_GEOM_MATCH", "reason": "No geometry segment matched main OD", "ocr_main_od_in": od_dim.value_in, "ocr_candidates_count": len(ocr), "geom_segments_count": len(segs)}

    seg, score = match
    id_dim = pick_main_bore_id(ocr, seg)
    turning_len = pick_turning_length(ocr, seg)

    main_id_val = id_dim.value_in if id_dim else None
    if main_id_val is not None and main_id_val >= (od_dim.value_in - 0.02):
        main_id_val = None
        id_dim = None

    return {
        "status": "OK",
        "main_turning_od_in": round(od_dim.value_in, 4),
        "main_turning_od_conf": od_dim.conf,
        "main_turning_od_source": f"ocr:{od_dim.text}",
        "main_bore_id_in": None if main_id_val is None else round(main_id_val, 4),
        "main_bore_id_conf": None if id_dim is None else id_dim.conf,
        "main_bore_id_source": None if id_dim is None else f"ocr:{id_dim.text}",
        "main_turning_len_in": round(float(turning_len), 4),
        "main_turning_len_source": "ocr_len_near_seg" if any(d.kind == "LEN" for d in ocr) else "geometry_segment_len",
        "matched_segment_idx": seg.idx,
        "matched_segment_od_in": round(seg.od, 4),
        "matched_segment_id_in": round(seg.id, 4),
        "matched_segment_len_in": round(seg.length, 4),
        "match_score": round(score, 4),
        "total_len_in": round(total_len, 4),
        "ocr_candidates_count": len(ocr),
        "geom_segments_count": len(segs),
    }
