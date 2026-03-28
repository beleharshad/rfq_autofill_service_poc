"""Dimension Intent Labeler — OCR-intent classification + geometry validation.

Classifies raw OCR dimension candidates into finish OD/ID/Length using
token-based heuristics (and optionally an LLM call), then validates the
labeled values against the geometry segment pool.

Pure in-memory.  No disk / PDF I/O.  Deterministic when LLM flag disabled.
"""

from __future__ import annotations

import copy
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── feature flag ────────────────────────────────────────────────────────────
USE_LLM_INTENT_LABELER = os.getenv("USE_LLM_INTENT_LABELER", "0") == "1"

# ── text normalisation ──────────────────────────────────────────────────────
_RE_MULTI_SPACE = re.compile(r"\s{2,}")
_BRACKET_RE = re.compile(r"\[.*?\]")
_TOL_RE = re.compile(r"(\d*\.?\d+)\s*[-\u2013]\s*(\d*\.?\d+)")

_SKIP_TOKENS = (
    "SCALE", "THREAD", "UNC", "UN-", "TAP", "PITCH",
    "ANGLE", "°", "DEG", " R.", " R ",
    "RMS", "MICRO", "µ",
)

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

_OD_TOKENS = ("OD_SYM", "DIA", "DIAM", "OD", "O.D", "FINISH OD")
_ID_TOKENS = ("ID", "I.D", "BORE", "INNER", "FINISH ID")
_LEN_TOKENS = ("LEN", "LENGTH", "OAL", "OVERALL", "FINISH LEN")


def _norm(text: str) -> str:
    t = text.upper()
    for ch in ("\u2300", "\u2205", "\u00d8", "Ø"):
        t = t.replace(ch, "OD_SYM")
    return _RE_MULTI_SPACE.sub(" ", t).strip()


def _safe_float(x: Any) -> Optional[float]:
    try:
        return float(x) if x is not None else None
    except (ValueError, TypeError):
        return None


# ── STEP 2: OCR candidate extraction ───────────────────────────────────────

def _extract_ocr_candidates(
    part_summary: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Pull raw OCR dimension candidates from part_summary and classify."""

    meta = part_summary.get("inference_metadata") or {}
    raw_dims: List[Dict[str, Any]] = (
        meta.get("raw_dimensions")
        or meta.get("ocr_dimensions")
        or []
    )
    if not raw_dims:
        return []

    candidates: List[Dict[str, Any]] = []

    _T = "[RFQ_DIMENSION_TRACE]"

    for i, dim in enumerate(raw_dims):
        text = str(dim.get("text", ""))
        text_n = _norm(text)
        value_in = _safe_float(dim.get("value_in")) or _safe_float(dim.get("value"))
        conf = float(dim.get("confidence", 0.5))
        is_tol = bool(dim.get("is_tolerance", False))
        kind = str(dim.get("kind", "unknown"))
        source = str(dim.get("source", "inference_metadata"))

        logger.info(
            f"{_T}[OCR_RAW] index={i} text={text!r} normalized_text={text_n!r} "
            f"detected_kind={kind} value_in={value_in} confidence={conf} "
            f"is_tolerance={is_tol} source={source}"
        )

        # skip bracketed metric
        if _BRACKET_RE.search(text_n):
            logger.info(f"{_T}[OCR_DISCARDED] index={i} value_in={value_in} reason=bracketed_metric text={text!r}")
            continue

        # skip SCALE, threads, radii, angles
        if any(tok in text_n for tok in _SKIP_TOKENS):
            matched_tok = next((tok for tok in _SKIP_TOKENS if tok in text_n), "?")
            logger.info(f"{_T}[OCR_DISCARDED] index={i} value_in={value_in} reason=skip_token({matched_tok}) text={text!r}")
            continue

        # skip surface finish / roughness annotations
        if _SURFACE_FINISH_RE.search(text) or _SURFACE_FINISH_RE.search(text_n):
            logger.info(f"{_T}[OCR_DISCARDED] index={i} value_in={value_in} reason=surface_finish_annotation text={text!r}")
            continue

        if value_in is None or value_in <= 0.01:
            logger.info(f"{_T}[OCR_DISCARDED] index={i} value_in={value_in} reason=no_valid_value text={text!r}")
            continue

        # tolerance range parsing from text
        tol_match = _TOL_RE.search(text)
        if tol_match:
            a, b = float(tol_match.group(1)), float(tol_match.group(2))
            is_tol = True
            tol_lo, tol_hi = min(a, b), max(a, b)
        else:
            tol_lo = tol_hi = value_in

        # classify intent
        intent = _classify_intent(text_n)

        logger.info(
            f"{_T}[OCR_FILTERED] index={i} value_in={value_in} kind={intent} "
            f"confidence={conf} is_tolerance={is_tol} "
            f"reason_kept=passed_all_filters text={text!r}"
        )

        candidates.append({
            "value_in": value_in,
            "tol_lo": tol_lo,
            "tol_hi": tol_hi,
            "is_tolerance": is_tol,
            "confidence": conf,
            "intent": intent,
            "text": text,
        })

    logger.info(f"{_T}[OCR_SUMMARY] raw_count={len(raw_dims)} filtered_count={len(candidates)}")
    return candidates


def _classify_intent(text_n: str) -> str:
    """Classify a normalised OCR text into OD / ID / LEN / UNKNOWN."""
    for tok in _LEN_TOKENS:
        if tok in text_n:
            return "LEN"
    for tok in _ID_TOKENS:
        if tok in text_n:
            return "ID"
    for tok in _OD_TOKENS:
        if tok in text_n:
            return "OD"
    return "UNKNOWN"


def _classify_unknown_by_geometry(
    candidates: List[Dict[str, Any]],
    part_summary: Dict[str, Any],
) -> None:
    """Reclassify UNKNOWN-intent candidates using geometry proximity.

    Uses optimal scoring: for each UNKNOWN candidate, compute match quality
    against all possible intents (OD, ID, LEN), then assign best-error-first
    so that the tightest geometry match wins, regardless of list order.
    Mutates candidates in-place.
    """
    segments = part_summary.get("segments") or []
    if not segments:
        return

    totals = part_summary.get("totals") or {}
    z_range = part_summary.get("z_range")

    geom_ods: List[float] = []
    geom_ids: List[float] = []
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        od = _safe_float(seg.get("od_diameter"))
        if od and od > 0.01:
            geom_ods.append(od)
        id_val = _safe_float(seg.get("id_diameter"))
        if id_val and id_val > 0.02:
            geom_ids.append(id_val)

    if not geom_ods:
        return

    max_geom_od = max(geom_ods)
    total_len = _safe_float(totals.get("total_length_in"))
    if total_len is None and isinstance(z_range, (list, tuple)) and len(z_range) >= 2:
        z0 = _safe_float(z_range[0]) or 0.0
        z1 = _safe_float(z_range[1]) or 0.0
        total_len = abs(z1 - z0)

    taken_intents: set = set()
    if any(c["intent"] == "OD" for c in candidates):
        taken_intents.add("OD")
    if any(c["intent"] == "ID" for c in candidates):
        taken_intents.add("ID")
    if any(c["intent"] == "LEN" for c in candidates):
        taken_intents.add("LEN")

    unknowns = [c for c in candidates if c["intent"] == "UNKNOWN"]
    if not unknowns:
        return

    # Build (error, candidate_index, intent, conf_penalty) tuples for scoring
    _CONF_PENALTY_GEOM = 0.08
    _CONF_PENALTY_HEUR = 0.03  # light penalty for strong heuristic signals
    scores: List[Tuple[float, int, str, float]] = []

    for idx, c in enumerate(unknowns):
        val = c["value_in"]

        # OD: find the BEST geometry segment OD match (within 15%)
        best_od_err = min(
            (abs(val - od) / od for od in geom_ods if od > 0),
            default=float("inf"),
        )
        if best_od_err <= 0.15 and val <= max_geom_od * 1.15:
            scores.append((best_od_err, idx, "OD", _CONF_PENALTY_GEOM))

        # ID: match against geometry bore IDs (within 25%)
        if geom_ids:
            best_id_err = min(
                (abs(val - gid) / gid for gid in geom_ids if gid > 0),
                default=float("inf"),
            )
            if best_id_err <= 0.25 and val < max_geom_od:
                scores.append((best_id_err, idx, "ID", _CONF_PENALTY_GEOM))

        # LEN: match against geometry total length (within 25%)
        if total_len and total_len > 0:
            len_err = abs(val - total_len) / total_len
            if len_err <= 0.25:
                scores.append((len_err, idx, "LEN", _CONF_PENALTY_GEOM))

        # LEN heuristic: value clearly exceeds all geometry ODs → likely length.
        # Values that exceed the threshold by more get a lower (=better) score
        # since they're more confidently a length dimension.
        if val > max_geom_od * 1.3 and 0.1 <= val <= 20.0:
            excess_ratio = (val - max_geom_od * 1.3) / val
            heur_score = 0.30 - excess_ratio * 0.10
            scores.append((heur_score, idx, "LEN", _CONF_PENALTY_HEUR))

    # Sort by error ascending — best geometry match wins
    scores.sort(key=lambda t: t[0])

    # Track each candidate's best OD error to prevent diameter values
    # from being misassigned as LEN when the OD slot is already taken
    cand_best_od_err: Dict[int, float] = {}
    for s_err, s_idx, s_intent, _ in scores:
        if s_intent == "OD" and (s_idx not in cand_best_od_err or s_err < cand_best_od_err[s_idx]):
            cand_best_od_err[s_idx] = s_err

    assigned_cand_indices: set = set()

    for err, idx, intent, penalty in scores:
        if intent in taken_intents:
            continue
        if idx in assigned_cand_indices:
            continue
        # Don't assign LEN to a candidate whose OD match is tighter —
        # it's a diameter, not a length, even if the OD slot is taken
        if intent == "LEN" and idx in cand_best_od_err and cand_best_od_err[idx] < err:
            continue
        c = unknowns[idx]
        c["intent"] = intent
        c["confidence"] = max(c["confidence"] - penalty, 0.35)
        taken_intents.add(intent)
        assigned_cand_indices.add(idx)
        logger.info(
            f"[RFQ_INTENT] Geometry-guided: {c['value_in']:.4f} → {intent} "
            f"(err={err:.2%}, conf={c['confidence']:.2f})"
        )


def _pick_best(
    candidates: List[Dict[str, Any]],
    intent: str,
    use_max: bool = True,
) -> Optional[Dict[str, Any]]:
    """Pick the best candidate for a given intent.

    For OD/ID ``use_max=True`` selects the tolerance-range MAX.
    For LEN ``use_max=False`` selects the tolerance-range AVERAGE.
    """
    pool = [c for c in candidates if c["intent"] == intent]
    if not pool:
        return None
    pool.sort(key=lambda c: c["confidence"], reverse=True)
    best = pool[0]
    if best["is_tolerance"]:
        if use_max:
            best = {**best, "value_in": best["tol_hi"]}
        else:
            best = {**best, "value_in": round((best["tol_lo"] + best["tol_hi"]) / 2, 6)}
    return best


# ── STEP 3: LLM intent labeling (stub) ─────────────────────────────────────

def _llm_label_dimensions(
    candidates: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Call LLM to label dimensions.  Returns parsed JSON or None on failure.

    Currently a stub — returns None (NO_OCR) because no LLM client exists
    in the repo.  When USE_LLM_INTENT_LABELER is True and a client is wired
    in, this would format a prompt and parse the JSON response.
    """
    _T = "[RFQ_DIMENSION_TRACE]"
    if not USE_LLM_INTENT_LABELER:
        logger.info(f"{_T}[LLM_PROMPT] LLM intent labeler disabled (USE_LLM_INTENT_LABELER=0)")
        logger.info(f"{_T}[LLM_RESPONSE] null (stub — no LLM client configured)")
        logger.info(
            f"{_T}[LLM_INTERPRETED] finish_od_candidate=None finish_id_candidate=None "
            f"finish_length_candidate=None reasoning=llm_disabled"
        )
        return None

    logger.info("[RFQ_INTENT] LLM flag enabled but no client configured — stub returning None")
    logger.info(f"{_T}[LLM_PROMPT] LLM flag enabled but no client wired — stub prompt not sent")
    logger.info(f"{_T}[LLM_RESPONSE] null (stub — no LLM client configured)")
    logger.info(
        f"{_T}[LLM_INTERPRETED] finish_od_candidate=None finish_id_candidate=None "
        f"finish_length_candidate=None reasoning=no_llm_client_configured"
    )
    return None


# ── STEP 4: geometry validation + matching ──────────────────────────────────

def _build_geometry_pool(
    part_summary: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], float]:
    """Build a pool of geometry OD candidates and compute total length."""

    segments = part_summary.get("segments") or []
    totals = part_summary.get("totals") or {}
    z_range = part_summary.get("z_range")

    total_len = _safe_float(totals.get("total_length_in"))
    if total_len is None and isinstance(z_range, (list, tuple)) and len(z_range) >= 2:
        z0 = _safe_float(z_range[0]) or 0.0
        z1 = _safe_float(z_range[1]) or 0.0
        total_len = abs(z1 - z0)
    if total_len is None and segments:
        z_vals = []
        for s in segments:
            z0 = _safe_float(s.get("z_start"))
            z1 = _safe_float(s.get("z_end"))
            if z0 is not None:
                z_vals.append(z0)
            if z1 is not None:
                z_vals.append(z1)
        total_len = (max(z_vals) - min(z_vals)) if z_vals else 0.0
    total_len = total_len or 0.0

    min_seg_len = 0.05 * total_len if total_len > 0 else 0.0
    pool: List[Dict[str, Any]] = []
    for idx, seg in enumerate(segments):
        if not isinstance(seg, dict):
            continue
        od = _safe_float(seg.get("od_diameter"))
        seg_len = (_safe_float(seg.get("z_end")) or 0.0) - (_safe_float(seg.get("z_start")) or 0.0)
        conf = _safe_float(seg.get("confidence")) or 0.0
        if od and od > 0 and seg_len >= min_seg_len and conf >= 0.6:
            pool.append({
                "idx": idx,
                "od_in": od,
                "seg_len": seg_len,
                "confidence": conf,
                "z_start": _safe_float(seg.get("z_start")) or 0.0,
                "z_end": _safe_float(seg.get("z_end")) or 0.0,
            })

    return pool, total_len


def _match_od_to_geometry(
    labeled_od: float,
    pool: List[Dict[str, Any]],
    all_labeled_ods: List[float],
) -> Tuple[Optional[Dict[str, Any]], Optional[float], List[str]]:
    """Match labeled finish_od to the closest segment OD within 10%.

    Returns (matched_seg, xy_scale_factor | None, reasons).
    If direct match fails, tries an XY scale correction and validates it.
    """
    reasons: List[str] = []
    if not pool:
        reasons.append("OCR_INSUFFICIENT_CONTEXT")
        return None, None, reasons

    _T = "[RFQ_DIMENSION_TRACE]"

    # direct match: within 10%
    best_seg = None
    best_err = float("inf")
    for seg in pool:
        err = abs(seg["od_in"] - labeled_od) / labeled_od if labeled_od > 0 else float("inf")
        if err < best_err:
            best_err = err
            best_seg = seg

    _closest_geom_od = f"{best_seg['od_in']:.4f}" if best_seg else "None"
    _od_accepted = best_seg is not None and best_err <= 0.10
    _od_reject_reason = "none" if _od_accepted else "mismatch_ratio"
    logger.info(
        f"{_T}[VALIDATION] candidate_type=OD ocr_value={labeled_od:.4f} "
        f"closest_geometry_value={_closest_geom_od} "
        f"relative_error={best_err:.4f} accepted={_od_accepted} "
        f"rejection_reason={_od_reject_reason}"
    )

    if best_seg is not None and best_err <= 0.10:
        return best_seg, None, reasons

    # attempt XY scale
    if best_seg is None:
        reasons.append("OCR_GEOM_OD_MISMATCH")
        return None, None, reasons

    xy_scale = labeled_od / best_seg["od_in"] if best_seg["od_in"] > 0 else None
    if xy_scale is None:
        reasons.append("OCR_GEOM_OD_MISMATCH")
        return None, None, reasons

    # validate scale: at least 1 other OCR OD should also match better, OR
    # the matched segment is long+confident and post-scale error < 3%
    post_err = abs(best_seg["od_in"] * xy_scale - labeled_od) / labeled_od if labeled_od > 0 else 1.0
    other_ods_improve = 0
    for od_val in all_labeled_ods:
        if od_val == labeled_od:
            continue
        for seg in pool:
            raw_err = abs(seg["od_in"] - od_val) / od_val if od_val > 0 else 1.0
            scaled_err = abs(seg["od_in"] * xy_scale - od_val) / od_val if od_val > 0 else 1.0
            if scaled_err < raw_err and scaled_err < 0.10:
                other_ods_improve += 1
                break

    scale_valid = False
    if other_ods_improve >= 1:
        scale_valid = True
    elif best_seg["confidence"] >= 0.8 and best_seg["seg_len"] > 0.10 and post_err < 0.03:
        scale_valid = True

    if scale_valid:
        return best_seg, xy_scale, reasons
    else:
        reasons.append("OCR_GEOM_OD_MISMATCH")
        return None, None, reasons


def _validate_length(
    labeled_len: Optional[float],
    geom_total_len: float,
    len_candidate: Optional[Dict[str, Any]],
) -> Tuple[Optional[float], Optional[float], List[str]]:
    """Validate labeled length against geometry.

    Returns (validated_len, z_scale | None, reasons).
    Never uses OD ratio for Z scaling.
    """
    _T = "[RFQ_DIMENSION_TRACE]"
    reasons: List[str] = []
    if labeled_len is None or labeled_len <= 0:
        logger.info(
            f"{_T}[VALIDATION] candidate_type=LEN ocr_value=None "
            f"closest_geometry_value={geom_total_len:.4f} relative_error=N/A "
            f"accepted=false rejection_reason=no_ocr_length_candidate"
        )
        return None, None, reasons

    if geom_total_len <= 0:
        logger.info(
            f"{_T}[VALIDATION] candidate_type=LEN ocr_value={labeled_len:.4f} "
            f"closest_geometry_value=0.0 relative_error=N/A "
            f"accepted=true rejection_reason=no_geometry_length_to_compare"
        )
        return labeled_len, None, reasons

    mismatch = abs(labeled_len - geom_total_len) / geom_total_len

    if mismatch <= 0.15:
        logger.info(
            f"{_T}[VALIDATION] candidate_type=LEN ocr_value={labeled_len:.4f} "
            f"closest_geometry_value={geom_total_len:.4f} relative_error={mismatch:.4f} "
            f"accepted=true rejection_reason=none"
        )
        return labeled_len, None, reasons

    # allow Z scaling ONLY if OCR length has sufficient confidence and LEN intent
    is_high_conf = (len_candidate is not None and len_candidate.get("confidence", 0) >= 0.55)
    has_len_token = (len_candidate is not None and len_candidate.get("intent") == "LEN")

    if is_high_conf and has_len_token:
        z_scale = labeled_len / geom_total_len
        logger.info(
            f"{_T}[VALIDATION] candidate_type=LEN ocr_value={labeled_len:.4f} "
            f"closest_geometry_value={geom_total_len:.4f} relative_error={mismatch:.4f} "
            f"accepted=true rejection_reason=none z_scale_applied={z_scale:.4f} "
            f"len_conf={len_candidate.get('confidence', 0):.2f} has_len_token={has_len_token}"
        )
        return labeled_len, z_scale, reasons

    logger.info(
        f"{_T}[VALIDATION] candidate_type=LEN ocr_value={labeled_len:.4f} "
        f"closest_geometry_value={geom_total_len:.4f} relative_error={mismatch:.4f} "
        f"accepted=false rejection_reason=tolerance_rejected "
        f"is_high_conf={is_high_conf} has_len_token={has_len_token}"
    )
    reasons.append("OCR_LEN_IMPLAUSIBLE")
    return None, None, reasons


# ── ENTRYPOINT ──────────────────────────────────────────────────────────────

def label_and_validate_dimensions(
    part_summary: Dict[str, Any],
    mode: str = "ENVELOPE",
    vendor_quote_mode: bool = False,
    flags: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Label OCR dimension candidates and validate against geometry.

    Returns a dict with:
        labeled  – dict of finish_od_in/finish_id_in/finish_len_in, each
                   having keys {value, confidence, source}.
        validation – {reasons: [...], xy_scale, z_scale}
        status   – "OK" | "FALLBACK_GEOMETRY" | "NO_OCR"
    """
    flags = flags or {}
    use_llm = flags.get("use_llm_intent_labeler", USE_LLM_INTENT_LABELER)

    result: Dict[str, Any] = {
        "labeled": {
            "finish_od_in": None,
            "finish_id_in": None,
            "finish_len_in": None,
        },
        "validation": {"reasons": [], "xy_scale": None, "z_scale": None},
        "status": "NO_OCR",
    }

    # ── extract and classify ───────────────────────────────────────────
    candidates = _extract_ocr_candidates(part_summary)
    if not candidates:
        logger.info("[RFQ_INTENT] No OCR candidates found → NO_OCR")
        return result

    # ── geometry-guided reclassification of UNKNOWN candidates ─────────
    _classify_unknown_by_geometry(candidates, part_summary)

    # ── LLM labeling (if enabled) ──────────────────────────────────────
    llm_result = None
    if use_llm:
        llm_result = _llm_label_dimensions(candidates)
    else:
        _T_llm = "[RFQ_DIMENSION_TRACE]"
        logger.info(f"{_T_llm}[LLM_PROMPT] LLM intent labeler disabled (use_llm=False)")
        logger.info(f"{_T_llm}[LLM_RESPONSE] null (llm disabled, skipped)")
        logger.info(
            f"{_T_llm}[LLM_INTERPRETED] finish_od_candidate=None finish_id_candidate=None "
            f"finish_length_candidate=None reasoning=llm_disabled"
        )

    # ── rule-based labeling ────────────────────────────────────────────
    od_cand = _pick_best(candidates, "OD", use_max=True)
    id_cand = _pick_best(candidates, "ID", use_max=True)
    len_cand = _pick_best(candidates, "LEN", use_max=False)

    # if LLM provided overrides, prefer those
    if llm_result:
        for key, cand_slot in [("finish_od_in", "od"), ("finish_id_in", "id"), ("finish_len_in", "len")]:
            llm_val = _safe_float(llm_result.get(key))
            if llm_val and llm_val > 0:
                if cand_slot == "od":
                    od_cand = {"value_in": llm_val, "confidence": 0.85, "intent": "OD",
                               "text": "LLM", "is_tolerance": False, "tol_lo": llm_val, "tol_hi": llm_val}
                elif cand_slot == "id":
                    id_cand = {"value_in": llm_val, "confidence": 0.85, "intent": "ID",
                               "text": "LLM", "is_tolerance": False, "tol_lo": llm_val, "tol_hi": llm_val}
                elif cand_slot == "len":
                    len_cand = {"value_in": llm_val, "confidence": 0.85, "intent": "LEN",
                                "text": "LLM", "is_tolerance": False, "tol_lo": llm_val, "tol_hi": llm_val}

    labeled_od = od_cand["value_in"] if od_cand else None
    labeled_id = id_cand["value_in"] if id_cand else None
    labeled_len = len_cand["value_in"] if len_cand else None

    if labeled_od is None:
        logger.info("[RFQ_INTENT] No OD candidate labeled → FALLBACK_GEOMETRY")
        result["status"] = "FALLBACK_GEOMETRY"
        result["validation"]["reasons"].append("OCR_INSUFFICIENT_CONTEXT")
        return result

    # ── geometry validation ────────────────────────────────────────────
    pool, geom_total_len = _build_geometry_pool(part_summary)

    all_labeled_ods = [v for v in [labeled_od] if v]
    # collect extra OCR OD candidates for cross-validation
    for c in candidates:
        if c["intent"] == "OD" and c["value_in"] != labeled_od:
            all_labeled_ods.append(c["value_in"])

    matched_seg, xy_scale, od_reasons = _match_od_to_geometry(
        labeled_od, pool, all_labeled_ods
    )
    result["validation"]["reasons"].extend(od_reasons)

    if matched_seg is None and not xy_scale:
        result["status"] = "FALLBACK_GEOMETRY"
        _log_result(result, labeled_od, labeled_id, labeled_len, matched_seg, xy_scale, None)
        return result

    result["validation"]["xy_scale"] = xy_scale

    # validate length (never use OD ratio for Z)
    validated_len, z_scale, len_reasons = _validate_length(
        labeled_len, geom_total_len, len_cand
    )
    result["validation"]["reasons"].extend(len_reasons)
    result["validation"]["z_scale"] = z_scale

    _T = "[RFQ_DIMENSION_TRACE]"
    # validate ID: reject if < 5% of OD (tiny feature noise)
    validated_id = labeled_id
    if validated_id is not None and labeled_od > 0:
        if validated_id < 0.05 * labeled_od:
            logger.info(
                f"{_T}[VALIDATION] candidate_type=ID ocr_value={validated_id:.4f} "
                f"closest_geometry_value=N/A relative_error=N/A "
                f"accepted=false rejection_reason=small_feature "
                f"(id={validated_id:.4f} < 5%_of_od={labeled_od:.4f})"
            )
            result["validation"]["reasons"].append("OCR_ID_TINY_FEATURE")
            validated_id = None
        else:
            logger.info(
                f"{_T}[VALIDATION] candidate_type=ID ocr_value={validated_id:.4f} "
                f"closest_geometry_value=N/A relative_error=N/A "
                f"accepted=true rejection_reason=none"
            )
    elif validated_id is None:
        logger.info(
            f"{_T}[VALIDATION] candidate_type=ID ocr_value=None "
            f"closest_geometry_value=N/A relative_error=N/A "
            f"accepted=false rejection_reason=no_id_candidate"
        )

    # ── assemble labeled output ────────────────────────────────────────
    source_suffix = "ocr_llm" if (llm_result is not None) else "ocr_rule"

    def _field(val: Optional[float], cand: Optional[Dict], kind: str) -> Optional[Dict[str, Any]]:
        if val is None:
            return None
        return {
            "value": round(val, 4),
            "confidence": cand["confidence"] if cand else 0.5,
            "source": f"intent_labeler:{source_suffix}",
        }

    result["labeled"]["finish_od_in"] = _field(labeled_od, od_cand, "OD")
    result["labeled"]["finish_id_in"] = _field(validated_id, id_cand, "ID")
    result["labeled"]["finish_len_in"] = _field(validated_len, len_cand, "LEN")
    result["status"] = "OK"
    result["matched_segment"] = matched_seg

    _log_result(result, labeled_od, labeled_id, labeled_len, matched_seg, xy_scale, z_scale)
    return result


def _log_result(
    result: Dict[str, Any],
    od: Optional[float],
    id_val: Optional[float],
    length: Optional[float],
    matched_seg: Optional[Dict[str, Any]],
    xy_scale: Optional[float],
    z_scale: Optional[float],
) -> None:
    seg_idx = matched_seg["idx"] if matched_seg else None
    reasons = result.get("validation", {}).get("reasons", [])
    logger.info(
        f"[RFQ_INTENT] status={result['status']} "
        f"finish_od={od} finish_id={id_val} finish_len={length} "
        f"matched_seg={seg_idx} xy_scale={xy_scale} z_scale={z_scale} "
        f"reasons={reasons}"
    )
