"""OCR-driven functional finish dimension selector.

Selects FINISH OD / ID / LENGTH from part_summary.inference_metadata.raw_dimensions
using manufacturing-intent heuristics.  Pure in-memory — no PDF or disk I/O.

Priority: OCR-selected dims (this module) > geometry fallback > envelope.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Text normalisation
# ---------------------------------------------------------------------------

_RE_MULTI_SPACE = re.compile(r"\s{2,}")

def _norm(text: str) -> str:
    t = text.upper()
    t = t.replace("\u2300", "OD_SYM").replace("\u2205", "OD_SYM").replace("Ø", "OD_SYM")
    t = _RE_MULTI_SPACE.sub(" ", t).strip()
    return t


# ---------------------------------------------------------------------------
# Rejection / classification tokens
# ---------------------------------------------------------------------------

_RAW_TOKENS = ("RAW", "STOCK", "BAR", "BLANK", "RM ", "ENVELOPE")
_THREAD_TOKENS = ("THREAD", "UNC", "UN-", "TAP", "PITCH")
_SUPERSEDED_TOKENS = ("WAS D", "WAS ", "PREVIOUS", "SUPERSEDED", "OLD ")

_OD_POS = ("FINISH OD", "OD_SYM", "OD", "O.D", "DIA", "DIAMETER")
_ID_POS = ("FINISH ID", "ID", "I.D", "BORE", "INNER")
_LEN_POS = ("FINISH LENGTH", "FINISH LEN", "LENGTH", "LEN", "OAL", "OVERALL")

_BRACKET_RE = re.compile(r"\[.*?\]")
_TOL_RE = re.compile(r"(\d*\.?\d+)\s*[-\u2013]\s*(\d*\.?\d+)")

# Surface finish / roughness annotations (√, ∨, /, Ra, etc.)
_SURFACE_FINISH_RE = re.compile(
    r"(?:"
    r"[/\\][/@]"        # \/@ or //@ — garbled OCR of √ symbol
    r"|RA\s*\d"         # Ra followed by digit
    r"|\bRMS\b"         # RMS roughness
    r"|\d+\s*X\s*\d+"   # "N X M" roughness grid (e.g. ".34 X 82")
    r"|µ\s*(?:IN|M)"   # µin or µm
    r")",
    re.IGNORECASE,
)


def _safe_float(x: Any) -> Optional[float]:
    try:
        return float(x) if x is not None else None
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Candidate extraction
# ---------------------------------------------------------------------------

def _parse_tolerance(text: str) -> Optional[Tuple[float, float]]:
    m = _TOL_RE.search(text)
    if m:
        a, b = float(m.group(1)), float(m.group(2))
        if a > 0 and b > 0:
            return (a, b)
    return None


def _classify(text_norm: str) -> Optional[str]:
    """Return 'OD', 'ID', 'LEN', or None."""
    if _BRACKET_RE.search(text_norm):
        return None
    has_raw = any(t in text_norm for t in _RAW_TOKENS)
    if has_raw:
        return None
    if any(t in text_norm for t in _SUPERSEDED_TOKENS):
        return None
    has_thread = any(t in text_norm for t in _THREAD_TOKENS)

    if any(t in text_norm for t in _ID_POS) and not has_thread:
        return "ID"
    if any(t in text_norm for t in _OD_POS) and not has_thread:
        return "OD"
    if any(t in text_norm for t in _LEN_POS) and "SCALE" not in text_norm:
        return "LEN"
    return None


def _extract_candidates(raw_dims: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Classify raw_dimensions into OD / ID / LEN candidate lists."""
    buckets: Dict[str, List[Dict[str, Any]]] = {"OD": [], "ID": [], "LEN": []}

    for d in raw_dims:
        text_raw = str(d.get("text") or d.get("raw_text") or "")
        text_n = _norm(text_raw)
        v = _safe_float(d.get("value_in") or d.get("value") or d.get("inch"))
        if v is None or v <= 0:
            continue
        conf = float(d.get("confidence") or 0.7)
        is_tol = bool(d.get("is_tolerance") or False)

        # Reject superseded revision-history dimensions before classification
        if any(t in text_n for t in _SUPERSEDED_TOKENS):
            continue

        # Reject surface finish / roughness annotations
        if _SURFACE_FINISH_RE.search(text_raw) or _SURFACE_FINISH_RE.search(text_n):
            logger.debug(f"[OCR_FINISH_SELECT] Skipping surface finish annotation: {text_raw!r}")
            continue

        kind_hint = str(d.get("kind") or "").upper().strip()
        kind = _classify(text_n)
        if kind is None and kind_hint in ("OD", "ID", "LEN"):
            kind = kind_hint
        if kind is None:
            continue

        # Only apply tolerance parsing if the upstream scraper hasn't already
        # resolved the value (avoids grabbing a tolerance from a different
        # dimension on the same OCR line).
        if not d.get("value_in"):
            tol = _parse_tolerance(text_n)
            if tol:
                is_tol = True
                if kind in ("OD", "ID"):
                    v = max(tol)
                else:
                    v = (tol[0] + tol[1]) / 2.0
                conf = max(0.0, conf - 0.05)

        buckets[kind].append({
            "value": v,
            "conf": conf,
            "text": text_raw.strip(),
            "is_tolerance": is_tol,
        })

    return buckets


# ---------------------------------------------------------------------------
# Selection scoring
# ---------------------------------------------------------------------------

def _score_od(c: Dict[str, Any]) -> float:
    v = c["value"]
    s = c["conf"]
    tn = _norm(c["text"])
    if "FINISH" in tn:
        s += 0.15
    if 0.25 <= v <= 2.5:
        s += 0.05
    elif v > 2.5:
        s -= 0.10
    # Prefer non-tolerance standalone callout dimensions
    if not c.get("is_tolerance"):
        s += 0.03
    # Penalize values that look like bore sizes (small values with ID-adjacent context)
    if v < 0.8 and c.get("is_tolerance"):
        s -= 0.05
    return s


def _score_id(c: Dict[str, Any], selected_od: Optional[float]) -> float:
    v = c["value"]
    s = c["conf"]
    tn = _norm(c["text"])
    if "FINISH" in tn:
        s += 0.15
    if not (0.05 <= v <= 5.0):
        s -= 0.30
    if selected_od is not None and v >= selected_od - 0.02:
        s -= 1.0
    return s


def _score_len(c: Dict[str, Any]) -> float:
    v = c["value"]
    s = c["conf"]
    tn = _norm(c["text"])
    if "FINISH" in tn:
        s += 0.15
    if not (0.1 <= v <= 20.0):
        s -= 0.20
    return s


def _pick_best(candidates: List[Dict[str, Any]], scorer, **kw) -> Optional[Dict[str, Any]]:
    if not candidates:
        return None
    scored = [(scorer(c, **kw) if kw else scorer(c), c) for c in candidates]
    scored.sort(key=lambda t: t[0], reverse=True)
    return scored[0][1]


# ---------------------------------------------------------------------------
# Geometry validation
# ---------------------------------------------------------------------------

def _abs_rel_err(a: float, b: float) -> float:
    denom = max(abs(a), abs(b), 1e-9)
    return abs(a - b) / denom


def _to_inches(val: float, unit: str) -> float:
    return val / 25.4 if unit == "mm" else val


def validate_ocr_dims_with_geometry(
    ocr: Dict[str, Optional[float]],
    part_summary: Dict[str, Any],
    unit_len: str = "in",
) -> Tuple[Dict[str, Optional[float]], Dict[str, Any]]:
    """Sanity-check OCR dims against geometry segments. Returns (dims, debug_info)."""
    segments = part_summary.get("segments") or []
    totals = part_summary.get("totals") or {}
    z_range = part_summary.get("z_range")

    total_len = _safe_float(totals.get("total_length_in"))
    if total_len is None and isinstance(z_range, (list, tuple)) and len(z_range) >= 2:
        z0, z1 = _safe_float(z_range[0]), _safe_float(z_range[1])
        if z0 is not None and z1 is not None:
            total_len = _to_inches(z1, unit_len) - _to_inches(z0, unit_len)

    dbg: Dict[str, Any] = {"reasons": []}
    out = dict(ocr)

    ocr_od = ocr.get("finish_od_in")
    ocr_id = ocr.get("finish_id_in")
    ocr_len = ocr.get("finish_len_in")

    # --- OD validation ---
    if ocr_od is not None and segments:
        best_idx, best_err = None, 999.0
        for i, seg in enumerate(segments):
            if not isinstance(seg, dict):
                continue
            od_raw = _safe_float(seg.get("od_diameter"))
            if od_raw is None:
                continue
            od_in = _to_inches(od_raw, unit_len)
            err = _abs_rel_err(ocr_od, od_in)
            if err < best_err:
                best_err = err
                best_idx = i
        dbg["od_match_idx"] = best_idx
        dbg["od_match_rel_err"] = round(best_err, 4) if best_idx is not None else None
        if best_err <= 0.08 or (best_idx is not None and abs(ocr_od - _to_inches(_safe_float(segments[best_idx].get("od_diameter")) or 0, unit_len)) <= 0.02):
            dbg["od_snapped"] = True
        else:
            dbg["od_snapped"] = False
            if best_err > 0.25:
                dbg["reasons"].append("OCR_GEOM_OD_MISMATCH")
                out["finish_od_in"] = None
                logger.warning(f"[OCR_VALIDATE] Rejecting OCR OD={ocr_od:.4f} — no geometry match (best_err={best_err:.2f})")

    # --- LEN validation ---
    if ocr_len is not None and total_len is not None and total_len > 0:
        ratio = ocr_len / total_len
        dbg["len_vs_total_ratio"] = round(ratio, 3)
        if ratio > 3.0:
            dbg["reasons"].append("OCR_LEN_IMPLAUSIBLE")
            out["finish_len_in"] = None
            logger.warning(f"[OCR_VALIDATE] Rejecting OCR LEN={ocr_len:.4f} — {ratio:.1f}x total geometry length")
        elif ratio <= 0.60:
            dbg["len_is_turned_length"] = True
        else:
            dbg["len_is_turned_length"] = False

    # --- ID validation ---
    if ocr_id is not None:
        sel_od = out.get("finish_od_in")
        if sel_od is not None and ocr_id >= sel_od - 0.02:
            dbg["reasons"].append("OCR_ID_INVALID")
            out["finish_id_in"] = None

    return out, dbg


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def select_finish_dims_from_ocr(
    part_summary: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Select functional finish OD/ID/LEN from OCR raw_dimensions.

    Returns dict with finish_od_in, finish_id_in, finish_len_in, plus metadata,
    or None if no OCR candidates found.
    """
    meta = part_summary.get("inference_metadata") or {}
    raw = meta.get("raw_dimensions") or meta.get("ocr_dimensions") or []
    if not raw:
        return None

    buckets = _extract_candidates(raw)
    od_count = len(buckets["OD"])
    id_count = len(buckets["ID"])
    len_count = len(buckets["LEN"])

    if od_count == 0 and id_count == 0 and len_count == 0:
        return None

    best_od = _pick_best(buckets["OD"], _score_od)
    sel_od = best_od["value"] if best_od else None

    best_id = _pick_best(buckets["ID"], _score_id, selected_od=sel_od)
    sel_id = best_id["value"] if best_id else None

    best_len = _pick_best(buckets["LEN"], _score_len)
    sel_len = best_len["value"] if best_len else None

    result = {
        "finish_od_in": sel_od,
        "finish_id_in": sel_id,
        "finish_len_in": sel_len,
        "od_conf": best_od["conf"] if best_od else None,
        "id_conf": best_id["conf"] if best_id else None,
        "len_conf": best_len["conf"] if best_len else None,
        "od_text": best_od["text"] if best_od else None,
        "id_text": best_id["text"] if best_id else None,
        "len_text": best_len["text"] if best_len else None,
        "candidates": {"od": od_count, "id": id_count, "len": len_count},
    }

    logger.info(
        f"[OCR_FINISH_SELECT] od={sel_od}({best_od['text'][:40] if best_od else '-'}) "
        f"id={sel_id}({best_id['text'][:40] if best_id else '-'}) "
        f"len={sel_len}({best_len['text'][:40] if best_len else '-'}) "
        f"candidates=od:{od_count}/id:{id_count}/len:{len_count}"
    )
    return result
