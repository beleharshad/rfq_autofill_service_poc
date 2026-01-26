"""RFQ AutoFill v1 service.

Computes suggested RFQ fields from a part_summary-like JSON input.
"""

from __future__ import annotations
import math
from decimal import Decimal, ROUND_CEILING
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.models.rfq_autofill import (
    RFQAutofillDebug,
    RFQAutofillEstimate,
    RFQAutofillFields,
    RFQAutofillResponse,
    RFQFieldValue,
)
from app.services.feature_detection_service import FeatureDetectionService


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

    def __init__(self):
        """Initialize RFQ autofill service."""
        self.feature_detection_service = FeatureDetectionService()

    def autofill(
        self,
        part_no: str,
        part_summary_dict: Optional[Dict[str, Any]],
        tolerances: Dict[str, Any],
        job_id: Optional[str] = None,
        step_metrics: Optional[Dict[str, Any]] = None,
        mode: str = "ENVELOPE",
        cost_inputs: Optional[Dict[str, Any]] = None,
        vendor_quote_mode: bool = False,
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

        # Feature quality validation
        feature_quality: Optional[Dict[str, Any]] = None

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

        mode_norm = str(mode or "ENVELOPE").strip().upper()
        if mode_norm not in ("ENVELOPE", "GEOMETRY"):
            mode_norm = "ENVELOPE"

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

        # Step B2 Finish OD
        # - GEOMETRY: robust OD (min_len_gate + optional low_confidence drop)
        # - ENVELOPE: true max OD across all segments (safe upper bound)
        finish_od_in: Optional[float] = None
        max_od_seg_conf = 0.0
        pool_used: List[Dict[str, Any]] = []
        dropped_low_conf = False
        if segments:
            if mode_norm == "ENVELOPE":
                # Envelope mode should be an upper bound, so consider all segments regardless of length gate.
                pool = [s for s in segments if isinstance(s, dict)]
                dropped_low_conf = False
            else:
                cand_len = [s for s in segments if isinstance(s, dict) and seg_len_in(s) >= min_len_gate_in]
                pool = [s for s in cand_len if isinstance(s, dict)]
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

        # Step B4 RAW required dimensions (geometry envelope computation)
        # Compute stock/cut sizes deterministically from 3D geometry
        raw_max_od_in: Optional[float] = None
        raw_len_in: Optional[float] = None

        # Use same allowances as RM but compute RAW from finish dims
        rm_od_allowance_in = _as_float(tolerances.get("rm_od_allowance_in")) if isinstance(tolerances, dict) else None
        rm_len_allowance_in = _as_float(tolerances.get("rm_len_allowance_in")) if isinstance(tolerances, dict) else None
        if rm_od_allowance_in is None:
            rm_od_allowance_in = 0.10
        if rm_len_allowance_in is None:
            rm_len_allowance_in = 0.35

        # Compute RAW required stock dimensions
        if finish_od_in is not None:
            raw_od_raw = float(finish_od_in + rm_od_allowance_in)
            if vendor_quote_mode:
                # Vendor quote mode: use fine rounding (0.01") to match Excel
                raw_max_od_in = float(round_up(raw_od_raw, step=0.01))
            else:
                # Standard mode: round up to 0.05"
                raw_max_od_in = float(round_up(raw_od_raw, step=0.05))
        if finish_len_in is not None:
            raw_len_raw = float(finish_len_in + rm_len_allowance_in)
            if vendor_quote_mode:
                # Vendor quote mode: use fine rounding (0.01") to match Excel
                raw_len_in = float(round_up(raw_len_raw, step=0.01))
            else:
                # Standard mode: round up to 0.10"
                raw_len_in = float(round_up(raw_len_raw, step=0.10))

        # Step C RM values (now derived from RAW required dimensions)
        # RFQ uses stock/cut size, not finished size - align RM with RAW computed above
        rm_od_in = raw_max_od_in  # RM OD is now based on raw_max_od_in
        rm_len_in = raw_len_in    # RM length is now based on raw_len_in

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

        # Step 9 Status logic
        # Envelope mode is meant to be a safe, bounded suggestion: only REJECT on invalid geometry
        # or explicit validation failure; otherwise mark NEEDS_REVIEW when any review flag exists.
        invalid_len = finish_len_in is None or float(finish_len_in) <= 0
        invalid_od = finish_od_in is None or float(finish_od_in) <= 0
        if validation_passed is False:
            status = "REJECTED"
        elif invalid_od or invalid_len:
            status = "REJECTED"
        elif mode_norm == "ENVELOPE":
            needs_review_flags = (
                (scale_method != "anchor_dimension")
                or ("LOW_CONF_FINISH_ID" in reasons)
                or ("OD_SPIKE_SUSPECT" in reasons)
            )
            status = "NEEDS_REVIEW" if needs_review_flags else "AUTO_FILLED"
        else:
            # GEOMETRY mode: keep stricter confidence gating
            if (finish_od_conf < 0.65) or (finish_len_conf < 0.65):
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

        # Feature quality issues can force NEEDS_REVIEW
        if feature_quality and feature_quality.get("status") == "NEEDS_REVIEW":
            status = "NEEDS_REVIEW"

        # Step 11 Output formatting (round to 3 decimals)
        finish_len_in = r3(finish_len_in)
        finish_od_in = r3(finish_od_in)
        finish_id_in = float(r3(finish_id_in) or 0.0)
        rm_od_in = r3(rm_od_in)
        rm_len_in = r3(rm_len_in)

        # Extract features for time-based estimates
        features = part_summary.get("features") if isinstance(part_summary, dict) else None
        has_features = isinstance(features, dict) and features

        # Validate feature quality if we have a job_id
        if isinstance(job_id, str) and job_id.strip() and has_features:
            try:
                feature_quality = self.feature_detection_service.validate_feature_quality(job_id.strip())
                # Add quality issues as reasons
                for issue in feature_quality.get("quality_issues", []):
                    _add_reason(issue)
            except Exception as e:
                print(f"Warning: Feature quality validation failed: {e}")
                feature_quality = None

        # Step 2: Quick Quote (Envelope) estimate block
        estimate: Optional[RFQAutofillEstimate] = None
        if mode_norm == "ENVELOPE":
            _add_reason("ENVELOPE_MODE")
            if vendor_quote_mode:
                _add_reason("VENDOR_QUOTE_MODE")

            if isinstance(cost_inputs, dict):
                _add_reason("PROXY_TIME_MODEL")

                rm_rate = _as_float(cost_inputs.get("rm_rate_per_kg"))
                turning_rate = _as_float(cost_inputs.get("turning_rate_per_min"))
                vmc_rate = _as_float(cost_inputs.get("vmc_rate_per_min"))
                roughing_cost_in = _as_float(cost_inputs.get("roughing_cost"))
                inspection_cost_in = _as_float(cost_inputs.get("inspection_cost"))
                special_process_cost_in = _as_float(cost_inputs.get("special_process_cost"))
                others_cost_in = _as_float(cost_inputs.get("others_cost"))
                density = _as_float(cost_inputs.get("material_density_kg_m3"))
                
                # Markup percentages
                pf_pct = _as_float(cost_inputs.get("pf_pct"))
                oh_profit_pct = _as_float(cost_inputs.get("oh_profit_pct"))
                rejection_pct = _as_float(cost_inputs.get("rejection_pct"))
                exchange_rate = _as_float(cost_inputs.get("exchange_rate"))

                if rm_rate is None:
                    rm_rate = 0.0
                if turning_rate is None:
                    turning_rate = 0.0
                if vmc_rate is None:
                    vmc_rate = 7.5  # Default VMC rate
                if roughing_cost_in is None:
                    roughing_cost_in = 0.0
                if inspection_cost_in is None:
                    inspection_cost_in = 0.0
                if others_cost_in is None:
                    others_cost_in = 0.0
                if density is None:
                    density = 7850.0
                if pf_pct is None:
                    pf_pct = 0.03  # 3%
                if oh_profit_pct is None:
                    oh_profit_pct = 0.15  # 15%
                if rejection_pct is None:
                    rejection_pct = 0.02  # 2%
                if exchange_rate is None:
                    exchange_rate = 82.0

                if special_process_cost_in is None:
                    _add_reason("MISSING_SPECIAL_PROCESS")
                    special_process_cost_in = 0.0

                base_est_conf = min(float(finish_od_conf), float(finish_len_conf))
                weight_conf = base_est_conf - 0.10
                time_conf = base_est_conf - 0.20

                # If scale not anchored, reduce estimate confidence (v1)
                if scale_method != "anchor_dimension":
                    weight_conf -= 0.15
                    time_conf -= 0.15

                # Feature quality affects confidence
                feature_time_conf = time_conf
                if feature_quality:
                    if "FEATURES_TEXT_ONLY" in feature_quality.get("quality_issues", []):
                        # Text-only features with estimated scale get heavily reduced confidence
                        if scale_method != "anchor_dimension":
                            feature_time_conf = min(feature_time_conf, 0.3)  # Cap at 30% for text-only + estimated scale

                weight_conf = _clamp01(weight_conf)
                time_conf = _clamp01(time_conf)

                # Solid cylinder weight based on RM OD/LEN (optionally subtract bore if confident)
                in3_to_m3 = 0.0254**3
                rm_od = float(rm_od_in or 0.0)
                rm_len = float(rm_len_in or 0.0)
                rm_r = rm_od / 2.0
                vol_in3 = math.pi * (rm_r**2) * rm_len

                used_bore_subtract = False
                # Vendor quote mode: always use solid cylinder (no bore subtraction)
                # Standard mode: subtract bore if confident
                if not vendor_quote_mode and float(finish_id_in) > 0.0 and float(finish_id_conf) >= 0.70:
                    bore_d = float(finish_id_in)
                    bore_r = bore_d / 2.0
                    bore_vol_in3 = math.pi * (bore_r**2) * rm_len
                    vol_in3 = max(0.0, vol_in3 - bore_vol_in3)
                    used_bore_subtract = True

                if not used_bore_subtract:
                    if vendor_quote_mode:
                        _add_reason("VENDOR_QUOTE_SOLID_CYLINDER")
                    else:
                        _add_reason("WEIGHT_SOLID_ASSUMPTION")

                vol_m3 = vol_in3 * in3_to_m3
                rm_weight_kg = float(vol_m3 * float(density))

                material_cost = float(rm_weight_kg * float(rm_rate))

                # Calculate feature-based time estimates
                drilling_minutes = 0.0
                milling_minutes = 0.0
                vmc_minutes = 0.0

                if has_features:
                    # Drilling time calculation
                    drilling_minutes = self._calculate_drilling_time(features, rm_len)
                    if drilling_minutes > 0:
                        _add_reason("FEATURE_TIME_MODEL")
                        feature_time_conf = min(feature_time_conf, 0.8)

                    # Milling time calculation
                    milling_minutes = self._calculate_milling_time(features, rm_len)
                    if milling_minutes > 0:
                        _add_reason("FEATURE_TIME_MODEL")
                        feature_time_conf = min(feature_time_conf, 0.8)
                    
                    # VMC time = drilling + milling (these operations typically done on VMC)
                    vmc_minutes = drilling_minutes + milling_minutes

                # Adjust confidence for scale method
                if scale_method != "anchor_dimension" and (drilling_minutes > 0 or milling_minutes > 0):
                    feature_time_conf = min(feature_time_conf, 0.4)

                # Turning time calculation based on finish length and surface area
                # Formula from template: (finish_length * factor / 40)
                # Factor varies: 10-15 based on complexity
                finish_len_val = float(finish_len_in or 0.0)
                turning_factor = 15.0 if rm_od > 2.0 else 10.0
                turning_minutes = float((finish_len_val * turning_factor) / 40.0)
                turning_minutes = max(turning_minutes, 5.0)  # Minimum 5 minutes
                turning_cost = float(turning_minutes * float(turning_rate))

                # VMC cost
                vmc_cost = float(vmc_minutes * float(vmc_rate))

                # Feature-based costs (drilling/milling use turning rate for simplicity)
                drilling_cost = float(drilling_minutes * float(turning_rate)) if drilling_minutes > 0 else 0.0
                milling_cost = float(milling_minutes * float(turning_rate)) if milling_minutes > 0 else 0.0

                # Roughing cost formula from template: finish_length * 1.5
                if roughing_cost_in == 0.0:
                    roughing_cost_in = float(finish_len_val * 1.5)

                # Subtotal calculation (sum of all costs before markups)
                subtotal_val = float(
                    material_cost
                    + roughing_cost_in
                    + turning_cost
                    + vmc_cost
                    + float(special_process_cost_in)
                    + float(others_cost_in)
                    + float(inspection_cost_in)
                )

                # Markup calculations based on subtotal
                pf_cost_val = float(subtotal_val * float(pf_pct))
                oh_profit_val = float(subtotal_val * float(oh_profit_pct))
                rejection_cost_val = float(subtotal_val * float(rejection_pct))

                # Final price calculations
                price_each_inr = float(subtotal_val + pf_cost_val + oh_profit_val + rejection_cost_val)
                price_each_currency = float(price_each_inr / float(exchange_rate)) if exchange_rate > 0 else 0.0

                # RM contribution percentage
                rm_contribution_pct_val = float((material_cost / price_each_inr) * 100.0) if price_each_inr > 0 else 0.0

                # Use feature-based confidence if features were used
                if has_features and (drilling_minutes > 0 or milling_minutes > 0):
                    subtotal_conf = _clamp01(min(weight_conf, feature_time_conf, base_est_conf))
                else:
                    subtotal_conf = _clamp01(min(weight_conf, time_conf, base_est_conf))

                # Create feature-based estimate fields
                drilling_minutes_field = None
                drilling_cost_field = None
                milling_minutes_field = None
                milling_cost_field = None
                vmc_minutes_field = None
                vmc_cost_field = None

                if drilling_minutes > 0:
                    drilling_minutes_field = _fv(r3(drilling_minutes), feature_time_conf, "features.holes_time")
                    drilling_cost_field = _fv(r3(drilling_cost), feature_time_conf, "features.time_x_rate")

                if milling_minutes > 0:
                    milling_minutes_field = _fv(r3(milling_minutes), feature_time_conf, "features.slots_time")
                    milling_cost_field = _fv(r3(milling_cost), feature_time_conf, "features.time_x_rate")

                if vmc_minutes > 0:
                    vmc_minutes_field = _fv(r3(vmc_minutes), feature_time_conf, "rule.vmc_time")
                    vmc_cost_field = _fv(r3(vmc_cost), feature_time_conf, "rule.vmc_time_x_rate")

                estimate = RFQAutofillEstimate(
                    rm_weight_kg=_fv(r3(rm_weight_kg), weight_conf, "rule.cylinder_weight" if not used_bore_subtract else "rule.cylinder_weight_minus_bore"),
                    material_cost=_fv(r3(material_cost), weight_conf, "rule.weight_x_rate"),
                    roughing_cost=_fv(r3(float(roughing_cost_in)), _clamp01(base_est_conf), "rule.length_x_1.5"),
                    inspection_cost=_fv(r3(float(inspection_cost_in)), _clamp01(base_est_conf), "input.cost_inputs"),
                    special_process_cost=_fv(r3(float(special_process_cost_in)), _clamp01(base_est_conf), "input.cost_inputs"),
                    turning_minutes=_fv(r3(turning_minutes), time_conf, "rule.length_x_factor_div_40"),
                    turning_cost=_fv(r3(turning_cost), time_conf, "rule.time_x_rate"),
                    vmc_minutes=vmc_minutes_field,
                    vmc_cost=vmc_cost_field,
                    drilling_minutes=drilling_minutes_field,
                    drilling_cost=drilling_cost_field,
                    milling_minutes=milling_minutes_field,
                    milling_cost=milling_cost_field,
                    others_cost=_fv(r3(float(others_cost_in)), _clamp01(base_est_conf), "input.cost_inputs"),
                    subtotal=_fv(r3(subtotal_val), subtotal_conf, "rule.sum"),
                    pf_cost=_fv(r3(pf_cost_val), subtotal_conf, "rule.subtotal_x_3pct"),
                    oh_profit=_fv(r3(oh_profit_val), subtotal_conf, "rule.subtotal_x_15pct"),
                    rejection_cost=_fv(r3(rejection_cost_val), subtotal_conf, "rule.subtotal_x_2pct"),
                    price_each_inr=_fv(r3(price_each_inr), subtotal_conf, "rule.subtotal_plus_markups"),
                    price_each_currency=_fv(r3(price_each_currency), subtotal_conf, "rule.inr_div_exchange_rate"),
                    rm_contribution_pct=_fv(r3(rm_contribution_pct_val), subtotal_conf, "rule.material_div_price"),
                    total_estimate=_fv(r3(price_each_inr), subtotal_conf, "rule.final_price"),
                )

        finish_len_source = "part_summary.z_range" if used_z_range else "part_summary.segments_fallback"

        # Calculate mm values from inches (1 inch = 25.4 mm)
        finish_od_mm = r3(float(finish_od_in) * 25.4) if finish_od_in is not None else None
        finish_id_mm = r3(float(finish_id_in) * 25.4) if finish_id_in is not None else None
        finish_len_mm = r3(float(finish_len_in) * 25.4) if finish_len_in is not None else None
        
        # RM ID is typically 0 for solid stock (no tube)
        rm_id_in_val = 0.0

        fields = RFQAutofillFields(
            finish_od_in=_fv(finish_od_in, finish_od_conf, "part_summary.max_od"),
            finish_len_in=_fv(finish_len_in, finish_len_conf, finish_len_source),
            finish_id_in=_fv(float(finish_id_in), finish_id_conf, "part_summary.bore_heuristic_p85"),
            finish_od_mm=_fv(finish_od_mm, finish_od_conf, "rule.inch_to_mm"),
            finish_id_mm=_fv(finish_id_mm, finish_id_conf, "rule.inch_to_mm"),
            finish_len_mm=_fv(finish_len_mm, finish_len_conf, "rule.inch_to_mm"),
            rm_od_in=_fv(rm_od_in, rm_od_conf, "rule.allowance_roundup"),
            rm_id_in=_fv(rm_id_in_val, rm_od_conf, "rule.solid_stock"),
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
            estimate=estimate,
        )


    def _calculate_drilling_time(self, features: Dict[str, Any], rm_len: float) -> float:
        """
        Calculate drilling time based on detected holes.

        Args:
            features: Features dictionary from part_summary
            rm_len: Raw material length

        Returns:
            Total drilling minutes
        """
        holes = features.get("holes", [])
        if not holes:
            return 0.0

        total_minutes = 0.0
        setup_applied = False
        min_confidence = 0.75
        min_diameter_in = 0.04

        for hole in holes:
            if not isinstance(hole, dict):
                continue

            # Get hole properties
            diameter = hole.get("diameter", 0.0)
            depth = hole.get("depth")  # None for through holes
            count = hole.get("count") or 1
            kind = hole.get("kind", "cross")
            confidence = hole.get("confidence", 0.0)

            if diameter <= 0 or diameter < min_diameter_in:
                continue
            if confidence is not None and float(confidence) < min_confidence:
                continue

            # Base time per hole (setup + positioning)
            base_time_per_hole = 0.5  # 0.5 minutes setup per hole
            
            # Estimate hole depth if not provided
            # For through holes, assume depth = RM length (worst case)
            # Typical drilling depth = 1-2 inches for most parts
            estimated_depth = depth if (depth is not None and depth > 0) else min(rm_len, 2.0)
            
            # Drilling time based on diameter and depth
            # Feed rate varies by drill size: smaller drills = slower
            # Typical feed rates for steel: 2-6 inches per minute
            if diameter < 0.25:
                drill_feed_rate = 2.0  # Small drill, slower feed
            elif diameter < 0.5:
                drill_feed_rate = 3.0  # Medium drill
            else:
                drill_feed_rate = 4.0  # Larger drill, faster feed
            
            drilling_time = estimated_depth / drill_feed_rate

            # Adjust for hole type
            if kind == "axial":
                drilling_time *= 1.3  # Axial holes need more setup/positioning
            elif kind == "cross":
                drilling_time *= 1.0  # Standard cross hole

            # Apply setup time once per pattern (not per hole)
            if not setup_applied:
                base_time_per_hole += 1.0  # Additional setup for first hole
                setup_applied = True

            total_time_per_hole = base_time_per_hole + drilling_time
            total_minutes += total_time_per_hole * float(count)

        return total_minutes

    def _calculate_milling_time(self, features: Dict[str, Any], rm_len: float) -> float:
        """
        Calculate milling time based on detected slots.

        Args:
            features: Features dictionary from part_summary
            rm_len: Raw material length

        Returns:
            Total milling minutes
        """
        slots = features.get("slots", [])
        if not slots:
            return 0.0

        total_minutes = 0.0
        setup_applied = False

        for slot in slots:
            if not isinstance(slot, dict):
                continue

            # Get slot properties - handle explicit None values
            length = slot.get("length") or 0.0
            width = slot.get("width") or 0.0
            
            if length <= 0 or width <= 0:
                continue
            
            # Default depth = half width if not specified or None
            depth = slot.get("depth")
            if depth is None or depth <= 0:
                depth = width * 0.5
            
            count = slot.get("count") or 1
            orientation = slot.get("orientation", "axial")

            # Base time per slot (setup + milling)
            base_time_per_slot = 3.0  # 3 minutes setup per slot

            # Milling time calculation
            # Volume to remove = length × width × depth
            volume_to_remove = length * width * depth

            # Milling feed rate (conservative for steel)
            mill_feed_rate = 0.5  # cubic inches per minute

            milling_time = volume_to_remove / mill_feed_rate

            # Adjust for orientation
            if orientation == "radial":
                milling_time *= 1.3  # Radial slots are harder to mill
            elif orientation == "circumferential":
                milling_time *= 1.5  # Circumferential slots are most complex

            # Minimum time per slot
            milling_time = max(milling_time, 1.0)

            # Apply setup time once per pattern
            if not setup_applied:
                base_time_per_slot += 2.0  # Additional setup for first slot
                setup_applied = True

            total_time_per_slot = base_time_per_slot + milling_time
            total_minutes += total_time_per_slot * float(count)

        return total_minutes


# Backwards-compatible helper (used by existing router code paths)
def autofill_from_part_summary(part_summary: Optional[Dict[str, Any]], tolerances: Dict[str, Any]) -> RFQAutofillResponse:
    return RFQAutofillService().autofill(
        part_no="",
        part_summary_dict=part_summary,
        tolerances=tolerances,
        step_metrics=None,
        mode="ENVELOPE",
        cost_inputs=None,
    )


