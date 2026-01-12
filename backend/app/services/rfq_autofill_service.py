"""RFQ AutoFill v1 service.

Computes suggested RFQ fields from a part_summary-like JSON input.
"""

from __future__ import annotations
from decimal import Decimal, ROUND_CEILING
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.models.rfq_autofill import RFQAutofillDebug, RFQAutofillFields, RFQAutofillResponse, RFQFieldValue


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _clamp_range(x: float, lo: float, hi: float) -> float:
    return max(float(lo), min(float(hi), float(x)))


def _as_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _extract_scale_method_and_validation(part_summary: Dict[str, Any]) -> Tuple[str, bool]:
    scale_report = part_summary.get("scale_report") or {}
    if not isinstance(scale_report, dict):
        scale_report = {}

    raw_method = scale_report.get("method")
    scale_method = str(raw_method) if raw_method is not None else "unknown"
    if scale_method not in ("anchor_dimension", "estimated"):
        scale_method = "unknown"

    validation_passed = scale_report.get("validation_passed")
    if not isinstance(validation_passed, bool):
        # "explicitly False" gate only
        validation_passed = True

    return str(scale_method), bool(validation_passed)


def _extract_overall_confidence(part_summary: Dict[str, Any]) -> float:
    # v1: prefer inference_metadata.overall_confidence; fall back to top-level overall_confidence
    meta = part_summary.get("inference_metadata") or {}
    if not isinstance(meta, dict):
        meta = {}
    overall = meta.get("overall_confidence")
    overall_f = _as_float(overall)
    if overall_f is not None:
        return float(overall_f)
    overall2 = part_summary.get("overall_confidence")
    overall2_f = _as_float(overall2)
    return float(overall2_f) if overall2_f is not None else 0.0


def weighted_percentile(values: Sequence[float], weights: Sequence[float], p: float) -> float:
    """Weighted percentile (step CDF): smallest v where cumulative weight >= p * total."""
    if not values or not weights or len(values) != len(weights):
        return 0.0

    pairs: List[Tuple[float, float]] = []
    for v, w in zip(values, weights):
        fw = float(w)
        if fw <= 0:
            continue
        pairs.append((float(v), fw))

    if not pairs:
        return 0.0

    pairs.sort(key=lambda t: t[0])
    total = sum(w for _, w in pairs)
    if total <= 0:
        return 0.0

    pp = max(0.0, min(1.0, float(p)))
    target = pp * total
    cum = 0.0
    for v, w in pairs:
        cum += w
        if cum >= target:
            return float(v)

    return float(pairs[-1][0])


def to_inches(value: float, unit: str) -> float:
    """Convert a value to inches. Supported: in, mm."""
    if unit == "mm":
        return float(value) / 25.4
    return float(value)


def ceil_to_step(x: float, step: float) -> float:
    """Deterministically round up to the next multiple of step (including exact multiples)."""
    if step <= 0:
        return float(x)
    dx = Decimal(str(float(x)))
    ds = Decimal(str(float(step)))
    q = (dx / ds).to_integral_value(rounding=ROUND_CEILING)
    return float(q * ds)


def round_up(x: float, step: float) -> float:
    """Alias for deterministic round-up used by RFQ AutoFill v1."""
    return ceil_to_step(x, step)


def weighted_median(values: Sequence[float], weights: Sequence[float]) -> float:
    """Length-weighted median: smallest v where cumulative weight >= 50% total."""
    if not values or not weights or len(values) != len(weights):
        return 0.0

    pairs: List[Tuple[float, float]] = []
    for v, w in zip(values, weights):
        fw = float(w)
        if fw <= 0:
            continue
        pairs.append((float(v), fw))

    if not pairs:
        return 0.0

    pairs.sort(key=lambda t: t[0])
    total = sum(w for _, w in pairs)
    if total <= 0:
        return 0.0

    target = 0.5 * total
    cum = 0.0
    for v, w in pairs:
        cum += w
        if cum >= target:
            return float(v)

    return float(pairs[-1][0])


class RFQAutofillService:
    """Deterministic RFQ AutoFill v1 implementation."""

    def autofill(
        self,
        part_no: str,
        part_summary_dict: Optional[Dict[str, Any]],
        tolerances: Dict[str, Any],
        step_metrics: Optional[Dict[str, Any]] = None,
    ) -> RFQAutofillResponse:
        reasons: List[str] = []
        status: str = "NEEDS_REVIEW"

        # Extracted / derived debug values
        scale_method: str = "unknown"
        overall_confidence: float = 0.55
        validation_passed: bool = True
        min_len_gate_in: float = 0.02
        bore_coverage_pct: float = 0.0
        used_z_range: Optional[bool] = None
        od_pool_count: Optional[int] = None
        od_pool_dropped_low_conf: Optional[bool] = None

        def _add_reason(code: str) -> None:
            if code not in reasons:
                reasons.append(code)

        def _fv(value: Optional[float], conf: float, source: str) -> RFQFieldValue:
            if value is None:
                conf = 0.0
            return RFQFieldValue(value=value, confidence=_clamp01(conf), source=source)

        if not part_no or not part_no.strip():
            # Request validation should catch this, but keep it robust.
            _add_reason("INVALID_PART_NO")

        part_summary = part_summary_dict if isinstance(part_summary_dict, dict) else None

        # Units handling (in/mm only)
        unit_len = "in"
        force_needs_review = False
        if part_summary and isinstance(part_summary.get("units"), dict):
            unit_len = str(part_summary["units"].get("length") or "in")
        if unit_len not in ("in", "mm"):
            # Spec: unknown units should force NEEDS_REVIEW (not a 500)
            _add_reason("UNKNOWN_UNITS")
            force_needs_review = True
            unit_len = "in"

        # Extract scale/validation/overall_confidence
        if part_summary:
            scale_method, validation_passed = _extract_scale_method_and_validation(part_summary)
            overall_confidence = _extract_overall_confidence(part_summary)

        # v1: treat missing/invalid overall_confidence as 0.55
        if overall_confidence is None or float(overall_confidence) <= 0:
            overall_confidence = 0.55
        overall_confidence = _clamp01(float(overall_confidence))

        # Segments / z_range checks
        segments = part_summary.get("segments") if part_summary else None
        z_range = part_summary.get("z_range") if part_summary else None

        if not isinstance(segments, list):
            segments = []

        # Step B1 Finish Length
        finish_len_in: Optional[float] = None
        used_len_fallback = False
        has_z_range = (
            isinstance(z_range, (list, tuple))
            and len(z_range) == 2
            and _as_float(z_range[0]) is not None
            and _as_float(z_range[1]) is not None
        )
        if (len(segments) == 0) and (not has_z_range):
            _add_reason("INSUFFICIENT_GEOMETRY")

        # If validation explicitly failed, record it (status set later)
        if validation_passed is False:
            _add_reason("VALIDATION_FAILED")

        # Early exit for insufficient geometry: deterministic REJECTED with explicit reason
        if "INSUFFICIENT_GEOMETRY" in reasons:
            fields = RFQAutofillFields(
                finish_od_in=_fv(None, 0.0, "part_summary.max_od"),
                finish_len_in=_fv(None, 0.0, "part_summary.z_range"),
                finish_id_in=_fv(0.0, 0.0, "part_summary.bore_heuristic_p85"),
                rm_od_in=_fv(None, 0.0, "rule.allowance_roundup"),
                rm_len_in=_fv(None, 0.0, "rule.allowance_roundup"),
            )
            debug = RFQAutofillDebug(
                max_od_in=0.0,
                overall_len_in=0.0,
                scale_method=str(scale_method),
                overall_confidence=float(overall_confidence),
                min_len_gate_in=float(min_len_gate_in),
                bore_coverage_pct=0.0,
                max_od_seg_conf=None,
                used_z_range=None,
                od_pool_count=None,
                od_pool_dropped_low_conf=None,
                id_auto_clamped=False,
                od_spike_suspect=False,
            )
            return RFQAutofillResponse(
                part_no=(part_no or "").strip(),
                fields=fields,
                status="REJECTED",  # type: ignore[arg-type]
                reasons=reasons,
                debug=debug,
            )
        if has_z_range:
            z0 = _as_float(z_range[0])
            z1 = _as_float(z_range[1])
            if z0 is not None and z1 is not None:
                finish_len_in = to_inches(float(z1 - z0), unit_len)
                used_z_range = True

        if finish_len_in is None and segments:
            used_len_fallback = True
            used_z_range = False
            z_starts: List[float] = []
            z_ends: List[float] = []
            for seg in segments:
                if not isinstance(seg, dict):
                    continue
                zs = _as_float(seg.get("z_start"))
                ze = _as_float(seg.get("z_end"))
                if zs is not None:
                    z_starts.append(to_inches(zs, unit_len))
                if ze is not None:
                    z_ends.append(to_inches(ze, unit_len))
            if z_starts and z_ends:
                finish_len_in = float(max(z_ends) - min(z_starts))

        # Define gate
        if finish_len_in is None or float(finish_len_in) <= 0:
            _add_reason("INVALID_FINISH_LEN")
        else:
            min_len_gate_in = float(max(0.02, 0.01 * float(finish_len_in)))

        # Segment helpers
        def seg_len_in(seg: Dict[str, Any]) -> float:
            zs = _as_float(seg.get("z_start")) or 0.0
            ze = _as_float(seg.get("z_end")) or 0.0
            return float(max(0.0, to_inches(ze, unit_len) - to_inches(zs, unit_len)))

        def has_low_conf_flag(seg: Dict[str, Any]) -> bool:
            flags = seg.get("flags") or []
            return isinstance(flags, list) and "low_confidence" in flags

        # Step B2 Finish OD (robust)
        finish_od_in: Optional[float] = None
        max_od_seg_conf = 0.0
        pool_used: List[Dict[str, Any]] = []
        dropped_low_conf = False
        if segments:
            cand_len = [s for s in segments if isinstance(s, dict) and seg_len_in(s) >= min_len_gate_in]
            pool: List[Dict[str, Any]] = [s for s in cand_len if isinstance(s, dict)]
            if not pool:
                pool = [s for s in segments if isinstance(s, dict)]
            if len(pool) >= 2:
                no_low = [s for s in pool if not has_low_conf_flag(s)]
                if no_low and len(no_low) < len(pool):
                    pool = no_low
                    dropped_low_conf = True

            pool_used = pool
            od_pool_count = len(pool_used)
            od_pool_dropped_low_conf = dropped_low_conf

            max_od = None
            for s in pool:
                odv = _as_float(s.get("od_diameter"))
                if odv is None:
                    continue
                od_in = to_inches(odv, unit_len)
                if od_in <= 0:
                    continue
                if (max_od is None) or (od_in > max_od):
                    max_od = float(od_in)
            if max_od is not None:
                finish_od_in = float(max_od)

            # Confidence of the segment(s) that produced the max OD
            if finish_od_in is not None:
                for s in pool:
                    odv = _as_float(s.get("od_diameter"))
                    if odv is None:
                        continue
                    od_in = float(to_inches(odv, unit_len))
                    if abs(od_in - float(finish_od_in)) > 1e-6:
                        continue
                    sc = _as_float(s.get("confidence")) or 0.0
                    if sc > max_od_seg_conf:
                        max_od_seg_conf = float(sc)

        if finish_od_in is None or float(finish_od_in) <= 0:
            _add_reason("INVALID_FINISH_OD")

        # Step B3 Finish ID (dominant bore, conservative)
        finish_id_in: float = 0.0
        has_valid_bore = False
        id_auto_clamped = False
        if segments and finish_len_in is not None and float(finish_len_in) > 0:
            valid_ids: List[float] = []
            valid_wts: List[float] = []
            bore_len_sum = 0.0
            for s in segments:
                if not isinstance(s, dict):
                    continue
                sl = seg_len_in(s)
                if sl <= 0:
                    continue

                idv = _as_float(s.get("id_diameter"))
                odv = _as_float(s.get("od_diameter"))
                if idv is None or odv is None:
                    continue

                id_in = float(to_inches(idv, unit_len))
                od_in = float(to_inches(odv, unit_len))
                if id_in <= 0 or od_in <= 0:
                    continue
                if id_in >= od_in:
                    continue

                # Ignore tiny/noise IDs
                tiny_gate = max(0.02, 0.01 * od_in)
                if id_in < tiny_gate:
                    continue

                valid_ids.append(float(id_in))
                valid_wts.append(float(sl))
                bore_len_sum += float(sl)

            if valid_ids:
                has_valid_bore = True
                bore_coverage_pct = max(0.0, min(100.0, 100.0 * bore_len_sum / float(finish_len_in)))
                finish_id_in = float(weighted_percentile(valid_ids, valid_wts, p=0.85))

                # Clamp: finish_id_in <= finish_od_in - 0.02
                if finish_od_in is not None:
                    max_allowed = float(finish_od_in) - 0.02
                    if max_allowed < 0:
                        max_allowed = 0.0
                    if finish_id_in > max_allowed:
                        finish_id_in = float(max_allowed)
                        id_auto_clamped = True
                        _add_reason("ID_AUTO_CLAMPED")
            else:
                bore_coverage_pct = 0.0
                finish_id_in = 0.0

        # Step C RM values + deterministic rounding (defaults if missing)
        rm_od_allowance_in = _as_float(tolerances.get("rm_od_allowance_in")) if isinstance(tolerances, dict) else None
        rm_len_allowance_in = _as_float(tolerances.get("rm_len_allowance_in")) if isinstance(tolerances, dict) else None
        if rm_od_allowance_in is None:
            rm_od_allowance_in = 0.10
        if rm_len_allowance_in is None:
            rm_len_allowance_in = 0.35

        rm_od_in: Optional[float] = None
        rm_len_in: Optional[float] = None
        if finish_od_in is not None:
            rm_od_raw = float(finish_od_in + rm_od_allowance_in)
            rm_od_in = float(round_up(rm_od_raw, step=0.05))
        if finish_len_in is not None:
            rm_len_raw = float(finish_len_in + rm_len_allowance_in)
            rm_len_in = float(round_up(rm_len_raw, step=0.10))

        # Round outputs to max 3 decimals (deterministic formatting)
        def r3(x: Optional[float]) -> Optional[float]:
            if x is None:
                return None
            return float(round(float(x), 3))

        # OD spike suspect (optional detection)
        od_spike_suspect = False
        if finish_len_in is not None and float(finish_len_in) > 0 and pool_used and finish_od_in is not None:
            od_vals_pool: List[float] = []
            od_wts_pool: List[float] = []
            for s in pool_used:
                sl = seg_len_in(s)
                if sl <= 0:
                    continue
                odv = _as_float(s.get("od_diameter"))
                if odv is None:
                    continue
                od_in = float(to_inches(odv, unit_len))
                if od_in <= 0:
                    continue
                od_vals_pool.append(od_in)
                od_wts_pool.append(sl)

            if od_vals_pool:
                od_med = float(weighted_median(od_vals_pool, od_wts_pool))
                support_len = 0.0
                for s in pool_used:
                    odv = _as_float(s.get("od_diameter"))
                    if odv is None:
                        continue
                    od_in = float(to_inches(odv, unit_len))
                    if abs(od_in - float(finish_od_in)) > 1e-6:
                        continue
                    support_len += float(seg_len_in(s))
                share = support_len / float(finish_len_in)
                if float(finish_od_in) > (od_med + 0.20) and share < 0.05:
                    od_spike_suspect = True
                    _add_reason("OD_SPIKE_SUSPECT")

        # Step 7 Reasons
        if used_len_fallback:
            _add_reason("Z_RANGE_MISSING_FALLBACK")
        if scale_method != "anchor_dimension":
            _add_reason("SCALE_ESTIMATED")

        # Step 8 Confidence rules per column
        # A) Finish OD confidence
        finish_od_conf = 0.6
        if scale_method == "anchor_dimension":
            finish_od_conf += 0.25
        if validation_passed is True:
            finish_od_conf += 0.10
        if float(max_od_seg_conf) > 0.85:
            finish_od_conf += 0.05
        if float(overall_confidence) < 0.70:
            finish_od_conf -= 0.15
        if od_spike_suspect:
            finish_od_conf -= 0.10
        finish_od_conf = _clamp01(finish_od_conf)

        # B) Finish Length confidence
        finish_len_conf = 0.6
        if scale_method == "anchor_dimension":
            finish_len_conf += 0.25
        if validation_passed is True:
            finish_len_conf += 0.10
        # v1: only apply if explicitly detected by upstream (default False)
        partial_crop = False
        meta = part_summary.get("inference_metadata") if isinstance(part_summary, dict) else None
        if isinstance(meta, dict) and isinstance(meta.get("crop_partial"), bool):
            partial_crop = bool(meta.get("crop_partial"))
        if partial_crop:
            finish_len_conf -= 0.20
        finish_len_conf = _clamp01(finish_len_conf)

        # C) Finish ID confidence
        finish_id_conf = 0.35
        if has_valid_bore:
            finish_id_conf += 0.20
        if bore_coverage_pct > 50.0:
            finish_id_conf += 0.10
        if scale_method == "anchor_dimension":
            finish_id_conf += 0.15
        if id_auto_clamped:
            finish_id_conf -= 0.20
        finish_id_conf = _clamp01(finish_id_conf)

        # D) RM confidences
        rm_od_conf = min(finish_od_conf, 0.85)
        rm_len_conf = min(finish_len_conf, 0.85)

        # Additional reasons based on derived values/confidences
        if finish_id_in > 0 and bore_coverage_pct < 50.0:
            _add_reason("LOW_BORE_COVERAGE")
        if finish_id_in > 0 and finish_id_conf < 0.65:
            _add_reason("LOW_CONF_FINISH_ID")

        # Step 9 Status logic (exact)
        invalid_len = finish_len_in is None or float(finish_len_in) <= 0
        invalid_od = finish_od_in is None or float(finish_od_in) <= 0
        if validation_passed is False:
            status = "REJECTED"
        elif invalid_od or invalid_len:
            status = "REJECTED"
        elif (finish_od_conf < 0.65) or (finish_len_conf < 0.65):
            status = "REJECTED"
        else:
            needs_review_flags = ("SCALE_ESTIMATED" in reasons) or ("LOW_CONF_FINISH_ID" in reasons)
            if (finish_od_conf >= 0.85) and (finish_len_conf >= 0.85) and (not needs_review_flags):
                status = "AUTO_FILLED"
            else:
                status = "NEEDS_REVIEW"

        # Unknown units must force NEEDS_REVIEW (never 500)
        if ("UNKNOWN_UNITS" in reasons) and status == "AUTO_FILLED":
            status = "NEEDS_REVIEW"

        # Step 11 Output formatting (round to 3 decimals)
        finish_len_in = r3(finish_len_in)
        finish_od_in = r3(finish_od_in)
        finish_id_in = float(r3(finish_id_in) or 0.0)
        rm_od_in = r3(rm_od_in)
        rm_len_in = r3(rm_len_in)

        finish_len_source = "part_summary.z_range" if used_z_range else "part_summary.segments_fallback"

        fields = RFQAutofillFields(
            finish_od_in=_fv(finish_od_in, finish_od_conf, "part_summary.max_od"),
            finish_len_in=_fv(finish_len_in, finish_len_conf, finish_len_source),
            finish_id_in=_fv(float(finish_id_in), finish_id_conf, "part_summary.bore_heuristic_p85"),
            rm_od_in=_fv(rm_od_in, rm_od_conf, "rule.allowance_roundup"),
            rm_len_in=_fv(rm_len_in, rm_len_conf, "rule.allowance_roundup"),
        )

        debug = RFQAutofillDebug(
            max_od_in=float(finish_od_in or 0.0),
            overall_len_in=float(finish_len_in or 0.0),
            scale_method=str(scale_method),
            overall_confidence=float(overall_confidence),
            min_len_gate_in=float(min_len_gate_in),
            bore_coverage_pct=float(round(float(bore_coverage_pct), 3)),
            max_od_seg_conf=(float(max_od_seg_conf) if max_od_seg_conf > 0 else None),
            used_z_range=used_z_range,
            od_pool_count=od_pool_count,
            od_pool_dropped_low_conf=od_pool_dropped_low_conf,
            id_auto_clamped=(True if id_auto_clamped else False),
            od_spike_suspect=(True if od_spike_suspect else False),
        )

        return RFQAutofillResponse(
            part_no=(part_no or "").strip(),
            fields=fields,
            status=status,  # type: ignore[arg-type]
            reasons=reasons,
            debug=debug,
        )


# Backwards-compatible helper (used by existing router code paths)
def autofill_from_part_summary(part_summary: Optional[Dict[str, Any]], tolerances: Dict[str, Any]) -> RFQAutofillResponse:
    return RFQAutofillService().autofill(part_no="", part_summary_dict=part_summary, tolerances=tolerances, step_metrics=None)


