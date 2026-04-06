"""RFQ AutoFill v1 service.

Computes suggested RFQ fields from a part_summary-like JSON input.
"""

from __future__ import annotations
import math
from decimal import Decimal, ROUND_CEILING
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pathlib import Path

from app.models.rfq_autofill import (
    RFQAutofillDebug,
    RFQAutofillEstimate,
    RFQAutofillFields,
    RFQAutofillResponse,
    RFQFieldValue,
)
from app.services.feature_detection_service import FeatureDetectionService
from app.services.currency_service import get_live_exchange_rate
import logging

logger = logging.getLogger(__name__)


def _extract_drawing_number_from_job(job_id: str) -> Optional[str]:
    """Extract drawing number from job's input PDF filename."""
    if not job_id:
        return None
    try:
        job_path = Path("data/jobs") / job_id / "inputs"
        if job_path.exists():
            for f in job_path.iterdir():
                if f.suffix.lower() == ".pdf" and f.name != "source.pdf":
                    # Remove extension and common suffixes
                    name = f.stem
                    # Remove revision suffix like _A, _B, _C
                    for rev in ["_A", "_B", "_C", "_D", "_E", "_F", "_G", "_H", "_J", "_K"]:
                        if name.upper().endswith(rev):
                            return name[:-2]  # Return without revision
                    return name
    except Exception:
        pass
    return None


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
    _KNOWN_METHODS = ("anchor_dimension", "estimated", "dpi_based", "calibrated_from_ocr")
    if scale_method not in _KNOWN_METHODS:
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
        from app.services.pdf_spec_extractor import PDFSpecExtractor
        from app.services.vendor_quote_extraction_service import VendorQuoteExtractionService
        self.pdf_extractor = PDFSpecExtractor()
        self.vendor_extractor = VendorQuoteExtractionService()
        self._last_band_debug: Optional[Dict[str, Any]] = None
    
    def _extract_ocr_id_diameter(
        self,
        part_summary: Dict[str, Any],
        job_id: Optional[str] = None
    ) -> Optional[float]:
        """
        Extract OCR ID diameter from part_summary or PDF.
        
        Looks for I.D., ID, BORE, INNER keywords.
        For tolerance ranges, picks MAX value (conservative).
        
        Args:
            part_summary: Part summary dictionary
            job_id: Optional job ID to load PDF from
            
        Returns:
            OCR ID diameter in inches, or None if not found
        """
        import re
        
        # Check inference_metadata first
        inference_meta = part_summary.get("inference_metadata") or {}
        if isinstance(inference_meta, dict):
            raw_dimensions = inference_meta.get("raw_dimensions") or []
            if isinstance(raw_dimensions, list):
                for dim in raw_dimensions:
                    if isinstance(dim, dict):
                        text = dim.get("text", "")
                        value = dim.get("value")
                        unit = dim.get("unit", "in")
                        
                        # Check for ID keywords
                        has_id_keyword = bool(
                            re.search(r'\bI\.?D\.?\b|\bID\b|\bBORE\b|\bINNER\b', text, re.IGNORECASE)
                        )
                        
                        if has_id_keyword and value is not None:
                            # Check for tolerance range (e.g., "0.723-0.727")
                            tolerance_match = re.search(r'(\d+\.\d+)\s*[-–]\s*(\d+\.\d+)', text)
                            if tolerance_match:
                                val1 = float(tolerance_match.group(1))
                                val2 = float(tolerance_match.group(2))
                                # Pick MAX value (conservative)
                                value = max(val1, val2)
                            
                            # Convert to inches if needed
                            if unit == "mm":
                                value_in = value / 25.4
                            else:
                                value_in = value
                            
                            # Skip bracketed metric values
                            is_bracketed = bool(re.search(r'\[[\d.]+\]', text))
                            if not is_bracketed and value_in > 0 and 0.01 <= value_in <= 10.0:
                                logger.info(f"[RFQ_ID_OVERRIDE] Found OCR ID in inference_metadata: {value_in:.4f} in (text: {text[:50]})")
                                return float(value_in)
        
        # Try PDF extraction
        if job_id:
            try:
                from app.storage.file_storage import FileStorage
                fs = FileStorage()
                job_path = fs.data_root / "jobs" / job_id / "inputs"
                
                if job_path.exists():
                    pdf_files = list(job_path.glob("*.pdf"))
                    if pdf_files:
                        pdf_path = pdf_files[0]
                        extract_result = self.pdf_extractor.extract_from_file(str(pdf_path))
                        if extract_result.get("success"):
                            specs = extract_result.get("extracted_specs", {})
                            finish_id = specs.get("finish_id_in")
                            if finish_id:
                                id_val = _as_float(finish_id)
                                if id_val and id_val > 0:
                                    # Check if it's a tolerance range
                                    id_text = str(finish_id)
                                    tolerance_match = re.search(r'(\d+\.\d+)\s*[-–]\s*(\d+\.\d+)', id_text)
                                    if tolerance_match:
                                        val1 = float(tolerance_match.group(1))
                                        val2 = float(tolerance_match.group(2))
                                        id_val = max(val1, val2)  # Pick MAX
                                    
                                    # Skip bracketed values
                                    is_bracketed = bool(re.search(r'\[', id_text))
                                    if not is_bracketed:
                                        logger.info(f"[RFQ_ID_OVERRIDE] Found OCR ID in PDF: {id_val:.4f} in")
                                        return float(id_val)
            except Exception as e:
                logger.debug(f"Failed to extract OCR ID from PDF: {e}")
        
        # Try vendor quote extraction
        if job_id:
            try:
                vendor_result = self.vendor_extractor.extract_from_job(job_id)
                if vendor_result.get("success"):
                    pdf_hint = vendor_result.get("pdf_hint", {})
                    if isinstance(pdf_hint, dict):
                        id_val = pdf_hint.get("finish_id_in")
                        if id_val:
                            if isinstance(id_val, str):
                                # Check for tolerance range
                                tolerance_match = re.search(r'(\d+\.\d+)\s*[-–]\s*(\d+\.\d+)', id_val)
                                if tolerance_match:
                                    val1 = float(tolerance_match.group(1))
                                    val2 = float(tolerance_match.group(2))
                                    id_val = max(val1, val2)
                                else:
                                    id_val = _as_float(id_val)
                            else:
                                id_val = _as_float(id_val)
                            
                            if id_val and id_val > 0:
                                # Skip bracketed values
                                id_text = str(id_val)
                                is_bracketed = bool(re.search(r'\[', id_text))
                                if not is_bracketed:
                                    logger.info(f"[RFQ_ID_OVERRIDE] Found OCR ID in vendor quote: {id_val:.4f} in")
                                    return float(id_val)
            except Exception as e:
                logger.debug(f"Failed to extract OCR ID from vendor quote: {e}")
        
        return None

    # ──────────────────────────────────────────────────────────────────────
    # Dominant-OD-Band helpers v2 (0.05" rounding bins, stock/flange penalty)
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def _compute_total_span(segments: List[Dict[str, Any]], unit_len: str = "in") -> float:
        z_lo, z_hi = float("inf"), float("-inf")
        for s in segments:
            if not isinstance(s, dict):
                continue
            zs = _as_float(s.get("z_start"))
            ze = _as_float(s.get("z_end"))
            if zs is not None:
                z_lo = min(z_lo, to_inches(zs, unit_len))
            if ze is not None:
                z_hi = max(z_hi, to_inches(ze, unit_len))
        return max(0.0, z_hi - z_lo) if z_lo < z_hi else 0.0

    # ── Manufacturing-Aware Band Classifier ─────────────────────────────

    @staticmethod
    def build_od_bands(
        segments: List[Dict[str, Any]],
        total_len: float,
        unit_len: str = "in",
        bin_step: float = 0.05,
    ) -> List[Dict[str, Any]]:
        """Build OD bands with manufacturing-aware metrics for feature classification.

        Each band includes: od_key, z_min, z_max, z_span, coverage_len,
        coverage_ratio, z_continuity_ratio, conf_wavg, z_center_ratio,
        seg_indices, seg_items.
        Returns bands sorted by coverage_len descending.
        """
        min_len_gate = 0.03 * total_len if total_len > 0 else 0.0
        buckets: Dict[float, Dict[str, Any]] = {}

        for idx, s in enumerate(segments):
            if not isinstance(s, dict):
                continue
            if "low_confidence" in (s.get("flags") or []):
                continue
            zs_raw = _as_float(s.get("z_start"))
            ze_raw = _as_float(s.get("z_end"))
            od_raw = _as_float(s.get("od_diameter"))
            if zs_raw is None or ze_raw is None or od_raw is None:
                continue
            zs = to_inches(zs_raw, unit_len)
            ze = to_inches(ze_raw, unit_len)
            seg_len = max(0.0, ze - zs)
            od_in = to_inches(od_raw, unit_len)
            conf = float(_as_float(s.get("confidence")) or 0.0)

            if seg_len <= 0 or od_in < 0.10 or seg_len < min_len_gate:
                continue

            key = round(od_in / bin_step) * bin_step

            if key not in buckets:
                buckets[key] = {
                    "od_key": round(key, 4),
                    "coverage_len": 0.0,
                    "_conf_sum": 0.0,
                    "_len_sum": 0.0,
                    "z_min": zs,
                    "z_max": ze,
                    "seg_indices": [],
                    "seg_items": [],
                }
            b = buckets[key]
            b["coverage_len"] += seg_len
            b["_conf_sum"] += conf * seg_len
            b["_len_sum"] += seg_len
            b["z_min"] = min(b["z_min"], zs)
            b["z_max"] = max(b["z_max"], ze)
            b["seg_indices"].append(idx)
            b["seg_items"].append((idx, s, od_in, seg_len, conf))

        bands: List[Dict[str, Any]] = []
        for b in buckets.values():
            b["conf_wavg"] = b["_conf_sum"] / b["_len_sum"] if b["_len_sum"] > 0 else 0.0
            b["z_span"] = b["z_max"] - b["z_min"]
            b["coverage_ratio"] = b["coverage_len"] / total_len if total_len > 0 else 0.0
            b["z_continuity_ratio"] = b["z_span"] / total_len if total_len > 0 else 0.0
            b["z_center_ratio"] = (
                ((b["z_min"] + b["z_max"]) / 2.0) / total_len if total_len > 0 else 0.5
            )
            del b["_conf_sum"]
            del b["_len_sum"]
            bands.append(b)

        bands.sort(key=lambda b: b["coverage_len"], reverse=True)
        return bands

    @staticmethod
    def classify_bands(
        bands: List[Dict[str, Any]],
        total_len: float,
        segments: List[Dict[str, Any]],
        unit_len: str = "in",
        bin_step: float = 0.05,
    ) -> None:
        """Classify each band as MAIN_BODY, FLANGE, or OTHER (mutates bands in-place).

        Flange detection uses neighbor step-up analysis and endpoint-spike rules.
        MAIN_BODY is the best non-flange interior band by z_continuity → coverage → conf.
        """
        if not bands or total_len <= 0:
            return

        max_od_key = max(b["od_key"] for b in bands)

        seg_data: List[Tuple[float, float, float]] = []
        for s in segments:
            if not isinstance(s, dict):
                continue
            zs = _as_float(s.get("z_start"))
            ze = _as_float(s.get("z_end"))
            od = _as_float(s.get("od_diameter"))
            if zs is None or ze is None or od is None:
                continue
            seg_data.append((
                to_inches(zs, unit_len),
                to_inches(ze, unit_len),
                to_inches(od, unit_len),
            ))
        seg_data.sort(key=lambda x: x[0])
        tol = 0.01 * total_len

        for b in bands:
            od = b["od_key"]
            z_center_ratio = b["z_center_ratio"]
            z_cont = b["z_continuity_ratio"]

            b["is_interior"] = 1 if 0.20 <= z_center_ratio <= 0.80 else 0

            left_neighbor_od: Optional[float] = None
            best_left_gap = float("inf")
            for sz, ez, sod in seg_data:
                seg_key = round(sod / bin_step) * bin_step
                if abs(seg_key - od) < bin_step * 0.5:
                    continue
                if ez <= b["z_min"] + tol:
                    gap = b["z_min"] - ez
                    if 0 <= gap < best_left_gap:
                        best_left_gap = gap
                        left_neighbor_od = sod

            right_neighbor_od: Optional[float] = None
            best_right_gap = float("inf")
            for sz, ez, sod in seg_data:
                seg_key = round(sod / bin_step) * bin_step
                if abs(seg_key - od) < bin_step * 0.5:
                    continue
                if sz >= b["z_max"] - tol:
                    gap = sz - b["z_max"]
                    if 0 <= gap < best_right_gap:
                        best_right_gap = gap
                        right_neighbor_od = sod

            b["left_neighbor_od"] = round(left_neighbor_od, 4) if left_neighbor_od is not None else None
            b["right_neighbor_od"] = round(right_neighbor_od, 4) if right_neighbor_od is not None else None

            step_up_left = (left_neighbor_od is not None and od > 1.15 * left_neighbor_od)
            step_up_right = (right_neighbor_od is not None and od > 1.15 * right_neighbor_od)
            b["step_up_left"] = step_up_left
            b["step_up_right"] = step_up_right

            is_flange_candidate = (step_up_left and step_up_right) and z_cont < 0.35
            b["is_flange_candidate"] = is_flange_candidate

            is_endpoint = (b["z_min"] < 0.10 * total_len) or (b["z_max"] > 0.90 * total_len)
            is_spike = (od >= 0.95 * max_od_key) and z_cont < 0.55
            b["is_endpoint"] = is_endpoint
            b["is_spike"] = is_spike

            if is_flange_candidate or (is_spike and is_endpoint):
                b["feature_type"] = "FLANGE"
            else:
                b["feature_type"] = "OTHER"

        non_flange = [b for b in bands if b["feature_type"] != "FLANGE"]
        interior = [b for b in non_flange if b["is_interior"]]
        candidates = interior if interior else non_flange

        if candidates:
            candidates.sort(
                key=lambda b: (b["z_continuity_ratio"], b["coverage_ratio"], b["conf_wavg"]),
                reverse=True,
            )
            candidates[0]["feature_type"] = "MAIN_BODY"

    @staticmethod
    def score_main_body_bands(
        bands: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Score all bands for main-body selection and return the highest-scored band.

        Score = 0.50*z_continuity_ratio + 0.20*coverage_ratio
              + 0.15*conf_wavg + 0.15*is_interior
        Penalties: -0.40 FLANGE, -0.30 endpoint.
        """
        if not bands:
            return None

        for b in bands:
            z_cont = b.get("z_continuity_ratio", 0)
            cov = b.get("coverage_ratio", 0)
            conf = b.get("conf_wavg", 0)
            interior = float(b.get("is_interior", 0))
            feature_type = b.get("feature_type", "OTHER")
            is_ep = b.get("is_endpoint", False)

            score = 0.50 * z_cont + 0.20 * cov + 0.15 * conf + 0.15 * interior
            if feature_type == "FLANGE":
                score -= 0.40
            if is_ep:
                score -= 0.30

            b["_mb_score"] = round(score, 4)

        scored = sorted(bands, key=lambda b: b["_mb_score"], reverse=True)
        return scored[0]

    @staticmethod
    def _flange_reasons(band: Dict[str, Any]) -> List[str]:
        """Build human-readable reasons why a band was classified as FLANGE."""
        reasons: List[str] = []
        if band.get("is_flange_candidate"):
            reasons.append("step_up_both_sides")
        if band.get("is_spike") and band.get("is_endpoint"):
            reasons.append("endpoint_spike")
        if band.get("step_up_left"):
            left = band.get("left_neighbor_od")
            reasons.append(f"step_up_left(neighbor={left})")
        if band.get("step_up_right"):
            right = band.get("right_neighbor_od")
            reasons.append(f"step_up_right(neighbor={right})")
        if band.get("z_continuity_ratio", 1.0) < 0.35:
            reasons.append(f"short_z_cont({band.get('z_continuity_ratio', 0):.3f})")
        return reasons

    # ── Legacy band helpers (used by OCR-path length estimation) ────────

    @staticmethod
    def _build_od_bands_by_rounding(
        segments: List[Dict[str, Any]],
        total_span: float,
        unit_len: str = "in",
        bin_size: float = 0.05,
    ) -> "List[Dict[str, Any]]":
        """Group segments into OD bands using fixed-width rounding bins (0.05").

        Returns list of band dicts sorted by coverage descending:
          { "od_key", "coverage", "conf_wavg", "z_min", "z_max", "seg_indices", "seg_items" }
        """
        min_len_gate = 0.03 * total_span if total_span > 0 else 0.0
        buckets: Dict[float, Dict[str, Any]] = {}

        for idx, s in enumerate(segments):
            if not isinstance(s, dict):
                continue
            zs_raw = _as_float(s.get("z_start"))
            ze_raw = _as_float(s.get("z_end"))
            od_raw = _as_float(s.get("od_diameter"))
            if zs_raw is None or ze_raw is None or od_raw is None:
                continue
            zs = to_inches(zs_raw, unit_len)
            ze = to_inches(ze_raw, unit_len)
            seg_len = max(0.0, ze - zs)
            od_in = to_inches(od_raw, unit_len)
            conf = float(_as_float(s.get("confidence")) or 0.0)

            if seg_len <= 0 or od_in < 0.10 or seg_len < min_len_gate:
                continue

            key = round(od_in / bin_size) * bin_size

            if key not in buckets:
                buckets[key] = {
                    "od_key": key,
                    "coverage": 0.0,
                    "conf_sum": 0.0,
                    "len_sum": 0.0,
                    "z_min": zs,
                    "z_max": ze,
                    "seg_indices": [],
                    "seg_items": [],
                }
            b = buckets[key]
            b["coverage"] += seg_len
            b["conf_sum"] += conf * seg_len
            b["len_sum"] += seg_len
            b["z_min"] = min(b["z_min"], zs)
            b["z_max"] = max(b["z_max"], ze)
            b["seg_indices"].append(idx)
            b["seg_items"].append((idx, s, od_in, seg_len, conf))

        bands = []
        for b in buckets.values():
            b["conf_wavg"] = b["conf_sum"] / b["len_sum"] if b["len_sum"] > 0 else 0.0
            del b["conf_sum"]
            del b["len_sum"]
            bands.append(b)

        bands.sort(key=lambda b: b["coverage"], reverse=True)
        return bands

    @staticmethod
    def _score_od_bands(
        bands: "List[Dict[str, Any]]",
        total_span: float,
    ) -> "Optional[Dict[str, Any]]":
        """Score bands and return the dominant one (highest score).

        Score = 0.70*coverage_ratio + 0.20*conf_wavg + mid_bonus - stock_penalty
        Then apply tiebreaker: if top band is near max-OD with low coverage,
        prefer a smaller-OD band with comparable coverage.
        """
        if not bands or total_span <= 0:
            return None

        max_band_od = max(b["od_key"] for b in bands)

        for b in bands:
            cov_ratio = b["coverage"] / total_span
            od = b["od_key"]
            near_ends = (b["z_min"] < 0.10 * total_span) or (b["z_max"] > 0.90 * total_span)
            stock_penalty = 0.35 if (od >= 0.90 * max_band_od and near_ends and cov_ratio < 0.45) else 0.0
            mid_bonus = 0.10 if (0.25 <= od <= 2.50) else 0.0

            score = 0.70 * cov_ratio + 0.20 * b["conf_wavg"] + mid_bonus - stock_penalty

            b["_score"] = round(score, 4)
            b["_cov_ratio"] = round(cov_ratio, 4)
            b["_near_ends"] = near_ends
            b["_stock_penalty"] = round(stock_penalty, 4)

        scored = sorted(bands, key=lambda b: b["_score"], reverse=True)
        top = scored[0]

        # Tiebreaker: if top band is within 5% of max OD AND coverage < 55%
        # AND another band has comparable coverage but smaller OD, prefer that.
        if (
            top["od_key"] >= 0.95 * max_band_od
            and top["_cov_ratio"] < 0.55
            and len(scored) > 1
        ):
            for alt in scored[1:]:
                if alt["od_key"] < top["od_key"] and alt["_cov_ratio"] >= top["_cov_ratio"] - 0.10:
                    logger.info(
                        f"[RFQ_OD_BANDS] tiebreaker: preferring od={alt['od_key']:.3f} "
                        f"(cov={alt['_cov_ratio']:.2f}) over od={top['od_key']:.3f} "
                        f"(cov={top['_cov_ratio']:.2f}, near max OD)"
                    )
                    top = alt
                    break

        return top

    def choose_main_segment(
        self,
        segments: List[Dict[str, Any]],
        total_length: Optional[float],
        unit_len: str = "in",
        min_length_threshold_pct: float = 0.05,
    ) -> Optional[Dict[str, Any]]:
        """Choose the main finished turning body segment via manufacturing-aware
        band classification (MAIN_BODY vs FLANGE vs OTHER).

        Uses build_od_bands → classify_bands → score_main_body_bands to detect
        the MAIN_BODY band, then returns the representative (longest) segment
        within that band.  Stores full debug info on self._last_band_debug and
        self._last_main_band.
        """
        if not segments:
            return None

        total_span = self._compute_total_span(segments, unit_len)
        if total_span <= 0:
            return None

        bands = self.build_od_bands(segments, total_span, unit_len)
        if not bands:
            return None

        self.classify_bands(bands, total_span, segments, unit_len)
        main_band = self.score_main_body_bands(bands)
        if main_band is None:
            return None

        global_max_od = max(b["od_key"] for b in bands)
        flange_bands = [b for b in bands if b.get("feature_type") == "FLANGE"]

        scored = sorted(bands, key=lambda b: b.get("_mb_score", 0), reverse=True)
        logger.info(
            f"[RFQ_OD_BANDS] total_span={total_span:.4f} bands={len(bands)} "
            f"envelope_max_od={global_max_od:.4f} flanges={len(flange_bands)}"
        )
        for i, b in enumerate(scored[:8]):
            logger.info(
                f"  band[{i}] od={b['od_key']:.3f} type={b.get('feature_type','?'):10s} "
                f"mb_score={b.get('_mb_score',0):+.4f} cov={b['coverage_ratio']:.3f} "
                f"z_span={b['z_span']:.4f} z_cont={b['z_continuity_ratio']:.3f} "
                f"interior={b.get('is_interior',0)} endpoint={b.get('is_endpoint',False)} "
                f"flange_cand={b.get('is_flange_candidate',False)} spike={b.get('is_spike',False)} "
                f"conf={b['conf_wavg']:.3f} segs={b['seg_indices']}"
            )

        self._last_main_band = main_band
        self._last_band_debug = {
            "total_span": round(total_span, 4),
            "envelope_max_od": round(global_max_od, 4),
            "selected_od": round(main_band["od_key"], 4),
            "selected_score": main_band.get("_mb_score", 0),
            "selected_feature_type": main_band.get("feature_type", "OTHER"),
            "selected_z_span": round(main_band["z_span"], 4),
            "selected_z_min": round(main_band["z_min"], 4),
            "selected_z_max": round(main_band["z_max"], 4),
            "selected_cov_ratio": round(main_band.get("coverage_ratio", 0), 4),
            "bands_top6": [
                {
                    "od": round(b["od_key"], 4),
                    "feature_type": b.get("feature_type", "OTHER"),
                    "mb_score": b.get("_mb_score", 0),
                    "cov_pct": round(b.get("coverage_ratio", 0) * 100, 1),
                    "zspan": round(b["z_span"], 4),
                    "z_cont": round(b.get("z_continuity_ratio", 0), 4),
                    "is_interior": b.get("is_interior", 0),
                    "is_endpoint": b.get("is_endpoint", False),
                    "is_flange_candidate": b.get("is_flange_candidate", False),
                    "is_spike": b.get("is_spike", False),
                    "near_ends": b.get("is_endpoint", False),
                    "score": b.get("_mb_score", 0),
                    "segs": b["seg_indices"],
                }
                for b in scored[:6]
            ],
            "flange_bands": [
                {
                    "od_key": round(b["od_key"], 4),
                    "z_span": round(b["z_span"], 4),
                    "reasons": RFQAutofillService._flange_reasons(b),
                }
                for b in flange_bands
            ],
        }

        band_segs = list(main_band["seg_items"])
        band_segs.sort(key=lambda t: (t[3], t[4]), reverse=True)
        rep_idx, rep_seg, rep_od, rep_len, rep_conf = band_segs[0]

        logger.info(
            f"[RFQ_MAIN_SEGMENT] selected_band_od={main_band['od_key']:.3f} "
            f"feature_type={main_band.get('feature_type','?')} "
            f"mb_score={main_band.get('_mb_score',0):.4f} "
            f"z_span={main_band['z_span']:.4f} "
            f"rep_idx={rep_idx} rep_od={rep_od:.4f} rep_len={rep_len:.4f} rep_conf={rep_conf:.3f}"
        )

        return rep_seg

    def _compute_turning_body_zspan(
        self,
        segments: List[Dict[str, Any]],
        unit_len: str = "in",
    ) -> Optional[Tuple[float, float, Dict[str, Any]]]:
        """Compute z-span of the dominant turned body section via OD-band scoring.

        Side-effect-free: does NOT touch self._last_band_debug.
        Returns (turning_zspan, band_od, debug_dict) or None.
        """
        total_span = self._compute_total_span(segments, unit_len)
        if total_span <= 0:
            return None

        bands = self._build_od_bands_by_rounding(segments, total_span, unit_len)
        if not bands:
            return None

        dominant = self._score_od_bands(bands, total_span)
        if dominant is None:
            return None

        z_span = dominant["z_max"] - dominant["z_min"]
        coverage = dominant["coverage"]

        dbg = {
            "band_od": round(dominant["od_key"], 4),
            "z_span": round(z_span, 4),
            "coverage": round(coverage, 4),
            "coverage_ratio": round(coverage / total_span, 4) if total_span > 0 else 0,
            "total_span": round(total_span, 4),
            "score": round(dominant.get("_score", 0), 4),
            "n_bands": len(bands),
        }

        logger.info(
            f"[RFQ_TURNING_LEN] dominant band od={dominant['od_key']:.3f} "
            f"z_span={z_span:.4f} coverage={coverage:.4f} total={total_span:.4f} "
            f"score={dominant.get('_score', 0):.4f}"
        )

        return z_span, dominant["od_key"], dbg

    def _compute_ocr_matched_body_span(
        self,
        segments: List[Dict[str, Any]],
        ocr_od_in: float,
        unit_len: str = "in",
        od_tolerance: float = 0.10,
    ) -> Optional[Tuple[float, float, Dict[str, Any]]]:
        """Compute finish length by finding geometry bands that match the OCR OD.

        When the 2D profile has anisotropic scaling (different X/Z pixel ratios),
        the single-band z-span won't correspond to the real length.  Instead we:
        1. Build OD bands from geometry.
        2. Derive a local OD scale: scale = ocr_od / dominant_band_od
        3. Find ALL bands whose calibrated OD falls within ±tolerance of ocr_od.
        4. Compute the multi-band z-extent (z_max − z_min) and scale it.

        Returns (scaled_body_span, od_scale, debug_dict) or None.
        """
        total_span = self._compute_total_span(segments, unit_len)
        if total_span <= 0 or ocr_od_in <= 0:
            return None

        bands = self._build_od_bands_by_rounding(segments, total_span, unit_len)
        if not bands:
            return None

        dominant = self._score_od_bands(bands, total_span)
        if dominant is None:
            return None

        dom_od = dominant["od_key"]
        if dom_od <= 0:
            return None

        od_scale = ocr_od_in / dom_od

        matched_bands = []
        for b in bands:
            calibrated_od = b["od_key"] * od_scale
            if abs(calibrated_od - ocr_od_in) / ocr_od_in <= od_tolerance:
                matched_bands.append(b)

        if not matched_bands:
            matched_bands = [dominant]

        body_z_min = min(b["z_min"] for b in matched_bands)
        body_z_max = max(b["z_max"] for b in matched_bands)
        body_raw_span = body_z_max - body_z_min
        body_scaled_span = body_raw_span * od_scale

        dbg = {
            "method": "ocr_matched_body_span",
            "ocr_od": round(ocr_od_in, 4),
            "dom_od": round(dom_od, 4),
            "od_scale": round(od_scale, 4),
            "matched_band_count": len(matched_bands),
            "matched_ods": [round(b["od_key"], 4) for b in matched_bands],
            "body_z_min": round(body_z_min, 4),
            "body_z_max": round(body_z_max, 4),
            "body_raw_span": round(body_raw_span, 4),
            "body_scaled_span": round(body_scaled_span, 4),
            "total_span": round(total_span, 4),
        }

        logger.info(
            f"[RFQ_TURNING_LEN] OCR-matched body span: "
            f"scale={od_scale:.4f} matched_bands={len(matched_bands)} "
            f"raw_span={body_raw_span:.4f} scaled={body_scaled_span:.4f} "
            f"(dom_od={dom_od:.3f} → OCR {ocr_od_in:.3f})"
        )

        return body_scaled_span, od_scale, dbg

    def compute_finish_dims_from_geometry(
        self,
        part_summary: Dict[str, Any],
        mode: str = "GEOMETRY",
        unit_len: str = "in"
    ) -> Dict[str, Optional[float]]:
        """Compute finish dimensions from geometry using dominant-OD-band selection.

        Returns dict with finish_od_in, finish_id_in, finish_len_in,
        main_segment, main_segment_idx, and extra debug keys.
        """
        segments = part_summary.get("segments") if isinstance(part_summary, dict) else None
        z_range = part_summary.get("z_range") if isinstance(part_summary, dict) else None
        totals = part_summary.get("totals") if isinstance(part_summary, dict) else None

        if not isinstance(segments, list):
            segments = []

        result: Dict[str, Any] = {
            "finish_od_in": None,
            "finish_id_in": None,
            "finish_len_in": None,
            "main_segment": None,
            "main_segment_idx": None,
            "geom_finish_od_method": None,
            "geom_envelope_od_in": None,
            "od_bands_debug": None,
            "main_turning_len_in": None,
        }

        def _seg_len(seg: Dict[str, Any]) -> float:
            zs = _as_float(seg.get("z_start")) or 0.0
            ze = _as_float(seg.get("z_end")) or 0.0
            return float(max(0.0, to_inches(ze, unit_len) - to_inches(zs, unit_len)))

        # ── 1. Overall length (for finish_len_in and band computation) ──
        finish_len_in: Optional[float] = None
        _finish_len_from_explicit_source = False
        if totals and isinstance(totals, dict):
            tl = _as_float(totals.get("total_length_in"))
            if tl is not None and tl > 0:
                finish_len_in = float(to_inches(tl, unit_len))
                _finish_len_from_explicit_source = True
        if finish_len_in is None and z_range:
            if isinstance(z_range, (list, tuple)) and len(z_range) >= 2:
                z0 = _as_float(z_range[0])
                z1 = _as_float(z_range[1])
                if z0 is not None and z1 is not None:
                    finish_len_in = to_inches(float(z1 - z0), unit_len)
                    _finish_len_from_explicit_source = True
        if finish_len_in is None and segments:
            span = self._compute_total_span(segments, unit_len)
            if span > 0:
                finish_len_in = span

        # ── 2. Envelope max OD (always computed, used for RM stock) ──
        envelope_max_od: Optional[float] = None
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            od_raw = _as_float(seg.get("od_diameter"))
            if od_raw is not None:
                od_in = to_inches(od_raw, unit_len)
                if od_in > 0:
                    if envelope_max_od is None or od_in > envelope_max_od:
                        envelope_max_od = od_in
        result["geom_envelope_od_in"] = envelope_max_od

        # ── 3. Band-classified MAIN_BODY finish OD ──
        self._last_band_debug = None
        self._last_main_band = None
        if segments and finish_len_in and finish_len_in > 0:
            main_segment = self.choose_main_segment(segments, finish_len_in, unit_len)
            main_band = getattr(self, "_last_main_band", None)

            if main_segment and main_band:
                main_segment_idx = None
                for idx, seg in enumerate(segments):
                    if seg is main_segment:
                        main_segment_idx = idx
                        break
                    if isinstance(seg, dict) and seg.get("z_start") == main_segment.get("z_start") and seg.get("z_end") == main_segment.get("z_end"):
                        main_segment_idx = idx
                        break

                result["main_segment"] = main_segment
                result["main_segment_idx"] = main_segment_idx

                # ── 3a. Finish OD = MAIN_BODY band od_key ──
                band_od = main_band["od_key"]
                if band_od > 0:
                    result["finish_od_in"] = float(band_od)
                    result["geom_finish_od_method"] = "band_classifier_main_body"
                    logger.info(
                        f"[RFQ Autofill] Finish OD={band_od:.4f} from MAIN_BODY band "
                        f"(feature_type={main_band.get('feature_type')}, "
                        f"mb_score={main_band.get('_mb_score', 0):.4f}, "
                        f"envelope_max={envelope_max_od})"
                    )

                # ── 3b. Finish length = MAIN_BODY band z_span ──
                main_z_span = main_band["z_span"]
                result["main_turning_len_in"] = round(main_z_span, 4)

                if main_z_span >= 0.10 and not _finish_len_from_explicit_source:
                    result["finish_len_in"] = round(main_z_span, 4)
                    result["finish_len_source"] = "geometry.main_body_zspan"
                    logger.info(
                        f"[RFQ_TURNING_LEN] Geometry fallback: using MAIN_BODY z-span "
                        f"{main_z_span:.4f} (total_len={finish_len_in:.4f})"
                    )
                else:
                    result["finish_len_in"] = finish_len_in
                    result["finish_len_source"] = "geometry.z_range" if _finish_len_from_explicit_source else "geometry.total_length"

                if hasattr(self, "_last_band_debug") and self._last_band_debug:
                    result["od_bands_debug"] = self._last_band_debug

                # ── 3c. Finish ID: bore segments aligned with MAIN_BODY z-range ──
                chosen_od = result["finish_od_in"]
                main_z_range = (main_band["z_min"], main_band["z_max"])

                id_from_band, band_clamped = self._pick_id_from_segments(
                    segments, None, chosen_od, unit_len,
                    z_range_filter=main_z_range,
                )
                if id_from_band is not None:
                    result["finish_id_in"] = id_from_band
                    result["_id_auto_clamped"] = band_clamped
                    logger.info(
                        f"[RFQ Autofill] Finish ID={id_from_band:.4f} "
                        f"from main-body z-range [{main_z_range[0]:.4f}, {main_z_range[1]:.4f}]"
                    )
                else:
                    id_global, global_clamped = self._pick_id_from_segments(
                        segments, None, chosen_od, unit_len,
                    )
                    if id_global is not None:
                        result["finish_id_in"] = id_global
                        result["_id_auto_clamped"] = global_clamped
                        logger.info(f"[RFQ Autofill] Finish ID={id_global:.4f} from global bore fallback")

                # Solid cylinder: ID remains None → treat as 0.0
                if result["finish_id_in"] is None:
                    result["finish_id_in"] = 0.0
                    result["_id_auto_clamped"] = False
            else:
                result["finish_len_in"] = finish_len_in
        else:
            result["finish_len_in"] = finish_len_in

        return result

    @staticmethod
    def _pick_id_from_segments(
        segments: List[Dict[str, Any]],
        restrict_indices: Optional[set],
        chosen_od: Optional[float],
        unit_len: str,
        z_range_filter: Optional[Tuple[float, float]] = None,
    ) -> Tuple[Optional[float], bool]:
        """Pick the best bore ID from segments.

        Filters:
          restrict_indices – only consider segments at these indices.
          z_range_filter   – only consider segments whose z-extent overlaps
                             [z_min, z_max] (inches, already converted).

        Returns (id_value_in, was_clamped): id_value_in is None if no valid bore
        found. was_clamped=True when the wall thickness was < 0.02" and the ID
        was clamped to od - 0.02.
        """
        MIN_WALL = 0.02
        valid_ids: List[float] = []
        any_clamped = False
        for idx, seg in enumerate(segments):
            if not isinstance(seg, dict):
                continue
            if restrict_indices is not None and idx not in restrict_indices:
                continue
            if z_range_filter is not None:
                zs = to_inches(_as_float(seg.get("z_start")) or 0.0, unit_len)
                ze = to_inches(_as_float(seg.get("z_end")) or 0.0, unit_len)
                filt_min, filt_max = z_range_filter
                if ze <= filt_min or zs >= filt_max:
                    continue
            seg_id = _as_float(seg.get("id_diameter"))
            seg_od = _as_float(seg.get("od_diameter"))
            if seg_id is None or seg_od is None:
                continue
            id_in = to_inches(seg_id, unit_len)
            od_in = to_inches(seg_od, unit_len)
            if id_in <= 0 or od_in <= 0:
                continue
            if id_in >= od_in:
                continue
            # Clamp near-OD IDs instead of skipping
            did_clamp = False
            if (od_in - id_in) < MIN_WALL:
                id_in = od_in - MIN_WALL
                did_clamp = True
                any_clamped = True
            tiny_gate = max(0.02, 0.01 * od_in)
            if id_in < tiny_gate:
                continue
            if chosen_od is not None and id_in >= chosen_od:
                continue
            valid_ids.append(id_in)

        if not valid_ids:
            return None, False
        return float(max(valid_ids)), any_clamped

    def compute_raw_dims(
        self,
        finish_od: Optional[float],
        finish_len: Optional[float],
        rm_od_allowance_in: float = 0.26,
        rm_len_allowance_in: float = 0.35,
        segments: Optional[List[Dict[str, Any]]] = None,
        mode: str = "GEOMETRY",
        unit_len: str = "in",
        vendor_quote_mode: bool = False
    ) -> Dict[str, Optional[float]]:
        """
        Compute raw material dimensions from finish dimensions.
        
        TASK 2 & 3: Fixed raw OD/Length calculation
        Rules:
        - Raw OD = finish_od + rm_od_allowance_in (NO bar-stock lookup unless mode=RAW_STOCK)
        - Raw Length = finish_length + rm_len_allowance_in
        - Rounding: 0.05" for OD, 0.10" for Length (or 0.01" in vendor_quote_mode)
        - Rounding applied ONLY at the end
        
        Args:
            finish_od: Finish OD in inches
            finish_len: Finish length in inches
            rm_od_allowance_in: OD allowance (default 0.26")
            rm_len_allowance_in: Length allowance (default 0.35")
            segments: Optional segments list (for RAW_STOCK mode only)
            mode: Mode string ("GEOMETRY", "ENVELOPE", "RAW_STOCK")
            unit_len: Length unit ("in" or "mm")
            vendor_quote_mode: Use fine rounding (0.01") if True
            
        Returns:
            Dictionary with rm_od_in, rm_len_in (before and after rounding)
        """
        result = {
            "rm_od_in": None,
            "rm_len_in": None,
            "rm_od_before_rounding": None,
            "rm_len_before_rounding": None
        }
        
        # TASK 3: Raw Length: finish_length + allowance, round at end
        if finish_len is not None:
            rm_len_raw = finish_len + rm_len_allowance_in
            result["rm_len_before_rounding"] = rm_len_raw
            
            # Round at end using step=0.10 (or 0.01 for vendor_quote_mode)
            if vendor_quote_mode:
                rm_len_in = round_up(rm_len_raw, step=0.01)
            else:
                rm_len_in = round_up(rm_len_raw, step=0.10)
            result["rm_len_in"] = float(rm_len_in)
            logger.info(f"[RFQ Autofill] Raw Length: finish_length {finish_len:.4f} + allowance {rm_len_allowance_in:.4f} = {rm_len_raw:.4f} in → {rm_len_in:.4f} in (step={0.01 if vendor_quote_mode else 0.10})")
        
        # TASK 2: Raw OD: finish_od + allowance, round at end
        # DO NOT use largest_leftmost_od unless explicitly mode=RAW_STOCK
        if finish_od is not None:
            # Simple calculation: finish_od + allowance
            rm_od_raw = finish_od + rm_od_allowance_in
            result["rm_od_before_rounding"] = rm_od_raw
            
            # Only check for largest leftmost OD if mode=RAW_STOCK (explicitly enabled)
            if mode.upper() == "RAW_STOCK" and segments:
                def seg_len_in(seg: Dict[str, Any]) -> float:
                    zs = _as_float(seg.get("z_start")) or 0.0
                    ze = _as_float(seg.get("z_end")) or 0.0
                    return float(max(0.0, to_inches(ze, unit_len) - to_inches(zs, unit_len)))
                
                # Find leftmost segment (minimum z_start)
                leftmost_seg = min(
                    [s for s in segments if isinstance(s, dict)],
                    key=lambda s: _as_float(s.get("z_start")) or float('inf')
                )
                largest_leftmost_od = _as_float(leftmost_seg.get("od_diameter"))
                
                if largest_leftmost_od is not None:
                    largest_leftmost_od_in = to_inches(largest_leftmost_od, unit_len)
                    rm_od_raw = max(rm_od_raw, largest_leftmost_od_in)
                    logger.info(f"[RFQ Autofill] Raw OD (RAW_STOCK mode): max({finish_od + rm_od_allowance_in:.4f}, {largest_leftmost_od_in:.4f}) = {rm_od_raw:.4f} in")
                    result["rm_od_before_rounding"] = rm_od_raw
            
            # Round at end using step=0.05 (or 0.01 for vendor_quote_mode)
            if vendor_quote_mode:
                rm_od_in = round_up(rm_od_raw, step=0.01)
            else:
                rm_od_in = round_up(rm_od_raw, step=0.05)
            result["rm_od_in"] = float(rm_od_in)
            logger.info(f"[RFQ Autofill] Raw OD: finish_od {finish_od:.4f} + allowance {rm_od_allowance_in:.4f} = {rm_od_raw:.4f} in → {rm_od_in:.4f} in (step={0.01 if vendor_quote_mode else 0.05})")
        
        return result

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

        # Segments / z_range / totals checks
        segments = part_summary.get("segments") if part_summary else None
        z_range = part_summary.get("z_range") if part_summary else None
        totals = part_summary.get("totals") if part_summary else None

        if not isinstance(segments, list):
            segments = []

        # ── RFQ_DIMENSION_TRACE: Geometry Segments ──────────────────────
        _T = "[RFQ_DIMENSION_TRACE]"
        _trace_max_od = 0.0
        _trace_min_od = float("inf")
        _trace_total_len = 0.0
        for _si, _seg in enumerate(segments):
            if not isinstance(_seg, dict):
                continue
            _seg_z_start = _as_float(_seg.get("z_start"))
            _seg_z_end = _as_float(_seg.get("z_end"))
            _seg_od = _as_float(_seg.get("od_diameter"))
            _seg_id = _as_float(_seg.get("id_diameter"))
            _seg_len = ((_seg_z_end or 0) - (_seg_z_start or 0))
            _seg_conf = _as_float(_seg.get("confidence"))
            _seg_flags = _seg.get("flags", "")
            logger.info(
                f"{_T}[GEOMETRY_SEGMENT] idx={_si} z_start={_seg_z_start} z_end={_seg_z_end} "
                f"length={_seg_len:.4f} od={_seg_od} id={_seg_id} confidence={_seg_conf} flags={_seg_flags}"
            )
            if _seg_od and _seg_od > _trace_max_od:
                _trace_max_od = _seg_od
            if _seg_od and _seg_od < _trace_min_od:
                _trace_min_od = _seg_od
            _trace_total_len += abs(_seg_len)
        if _trace_min_od == float("inf"):
            _trace_min_od = 0.0
        logger.info(
            f"{_T}[GEOMETRY_SUMMARY] total_segments={len(segments)} total_length={_trace_total_len:.4f} "
            f"max_od={_trace_max_od:.4f} min_od={_trace_min_od:.4f}"
        )

        # Check for sufficient geometry
        has_z_range = (
            isinstance(z_range, (list, tuple))
            and len(z_range) == 2
            and _as_float(z_range[0]) is not None
            and _as_float(z_range[1]) is not None
        )
        has_totals = totals and isinstance(totals, dict) and _as_float(totals.get("total_length_in")) is not None
        
        if (len(segments) == 0) and (not has_z_range) and (not has_totals):
            _add_reason("INSUFFICIENT_GEOMETRY")

        # If validation explicitly failed, record it (status set later)
        if validation_passed is False:
            _add_reason("VALIDATION_FAILED")

        # Early exit for insufficient geometry: deterministic REJECTED with explicit reason
        if "INSUFFICIENT_GEOMETRY" in reasons:
            fields = RFQAutofillFields(
                finish_od_in=_fv(None, 0.0, "part_summary.max_od"),
                finish_len_in=_fv(None, 0.0, "part_summary.z_range"),
                finish_id_in=_fv(None, 0.0, "part_summary.bore_heuristic_p85"),
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

        # ── Step B: Compute finish dimensions ──────────────────────────────
        # NEW PRIORITY:
        #   0) Intent labeler (OCR classification + geometry validation)
        #   1) OCR-selected  2) Geometry (dominant OD band)  3) Envelope
        from app.services.ocr_finish_selector import select_finish_dims_from_ocr, validate_ocr_dims_with_geometry
        from app.services.dimension_intent_labeler import label_and_validate_dimensions as _intent_label

        finish_od_in: Optional[float] = None
        finish_id_in: Optional[float] = None
        finish_len_in: Optional[float] = None
        main_segment: Optional[Dict[str, Any]] = None
        main_segment_idx: Optional[int] = None
        used_z_range: Optional[bool] = None
        used_len_fallback = False
        finish_source = "unknown"
        finish_len_via_turning_body = False  # set True when z-span is used
        global_max_od: Optional[float] = None
        ocr_select_debug: Optional[Dict[str, Any]] = None
        geom_val_debug: Optional[Dict[str, Any]] = None
        id_auto_clamped = False  # set True when an ID is clamped to od-0.02

        # ── Priority 0: Dimension Intent Labeler ──────────────────────────
        intent_labeler_used = False
        intent_labeler_status: Optional[str] = None
        intent_labeler_reasons: List[str] = []
        if part_summary:
            try:
                il_result = _intent_label(
                    part_summary,
                    mode=mode_norm,
                    vendor_quote_mode=vendor_quote_mode,
                    flags={},
                )
                intent_labeler_used = True
                intent_labeler_status = il_result.get("status")
                intent_labeler_reasons = (il_result.get("validation") or {}).get("reasons", [])

                if intent_labeler_status == "OK":
                    labeled = il_result.get("labeled") or {}
                    od_field = labeled.get("finish_od_in")
                    id_field = labeled.get("finish_id_in")
                    len_field = labeled.get("finish_len_in")

                    if od_field and od_field.get("value") is not None:
                        finish_od_in = od_field["value"]
                        finish_source = od_field.get("source", "intent_labeler:ocr_rule")
                    if id_field and id_field.get("value") is not None:
                        finish_id_in = id_field["value"]
                    if len_field and len_field.get("value") is not None:
                        finish_len_in = len_field["value"]
                        finish_len_via_turning_body = False

                    logger.info(
                        f"[RFQ_AUTOFILL] Intent labeler OK → od={finish_od_in} "
                        f"id={finish_id_in} len={finish_len_in}"
                    )
                else:
                    for r in intent_labeler_reasons:
                        _add_reason(r)
                    logger.info(
                        f"[RFQ_AUTOFILL] Intent labeler → {intent_labeler_status}, "
                        f"falling through to legacy path"
                    )
            except Exception as e:
                logger.warning(f"[RFQ_AUTOFILL] Intent labeler failed: {e}", exc_info=True)
                intent_labeler_used = True
                intent_labeler_status = "ERROR"

        # Calculate global max OD (always useful)
        for seg in (segments or []):
            if not isinstance(seg, dict):
                continue
            od_diam = _as_float(seg.get("od_diameter"))
            if od_diam is not None:
                od_in = to_inches(od_diam, unit_len)
                if od_in > 0:
                    if global_max_od is None or od_in > global_max_od:
                        global_max_od = od_in

        # --- Priority 1: OCR-selected finish dims (skipped if intent labeler already provided OD) ---
        ocr_result = None
        if finish_od_in is None:
            ocr_result = select_finish_dims_from_ocr(part_summary) if part_summary else None
        if ocr_result and ocr_result.get("finish_od_in") is not None:
            ocr_dims = {
                "finish_od_in": ocr_result["finish_od_in"],
                "finish_id_in": ocr_result.get("finish_id_in"),
                "finish_len_in": ocr_result.get("finish_len_in"),
            }
            validated, geom_val_debug = validate_ocr_dims_with_geometry(ocr_dims, part_summary, unit_len)
            ocr_select_debug = ocr_result

            # Only use OCR if OD survived validation
            if validated.get("finish_od_in") is not None:
                finish_od_in = validated["finish_od_in"]
                finish_id_in = validated.get("finish_id_in")
                finish_len_in = validated.get("finish_len_in")
                finish_source = "ocr"

                # If OCR didn't give length (or it was rejected):
                #  1) Try OCR-matched multi-band body span (best for anisotropic geometry)
                #  2) Fall back to single dominant band z-span
                #  3) Fall back to total geometry length
                if finish_len_in is None and segments and finish_od_in:
                    total_geom_len = _as_float((totals or {}).get("total_length_in"))
                    if total_geom_len is None and has_z_range:
                        z0 = to_inches(_as_float(z_range[0]) or 0.0, unit_len)
                        z1 = to_inches(_as_float(z_range[1]) or 0.0, unit_len)
                        total_geom_len = z1 - z0

                    # Strategy A: multi-band body span scaled by OCR OD ratio
                    mb = self._compute_ocr_matched_body_span(
                        segments, finish_od_in, unit_len
                    )
                    # Strategy B: single dominant band z-span (raw geometry)
                    tb = self._compute_turning_body_zspan(segments, unit_len)

                    chosen_len = None
                    chosen_method = None

                    if mb is not None:
                        body_span, od_scale, mb_debug = mb
                        if 0.10 <= body_span <= 20.0:
                            chosen_len = round(body_span, 4)
                            chosen_method = "ocr_matched_body_span"
                            logger.info(
                                f"[RFQ_TURNING_LEN] OCR path: using multi-band body span "
                                f"{body_span:.4f} (scale={od_scale:.4f})"
                            )

                    if chosen_len is None and tb is not None:
                        turning_zspan, band_od, tb_debug = tb
                        if (turning_zspan >= 0.10
                                and total_geom_len
                                and turning_zspan < total_geom_len * 0.85):
                            chosen_len = round(turning_zspan, 4)
                            chosen_method = "dominant_band_zspan"
                            logger.info(
                                f"[RFQ_TURNING_LEN] OCR path: using single-band z-span "
                                f"{turning_zspan:.4f} (band_od={band_od:.3f})"
                            )

                    if chosen_len is not None:
                        finish_len_in = chosen_len
                        finish_len_via_turning_body = True
                    else:
                        finish_len_in = total_geom_len
                        logger.info(
                            f"[RFQ_TURNING_LEN] OCR path: no band-based length, "
                            f"using total geometry {total_geom_len}"
                        )

                elif finish_len_in is None and segments:
                    tb = self._compute_turning_body_zspan(segments, unit_len)
                    total_geom_len = _as_float((totals or {}).get("total_length_in"))
                    if total_geom_len is None and has_z_range:
                        z0 = to_inches(_as_float(z_range[0]) or 0.0, unit_len)
                        z1 = to_inches(_as_float(z_range[1]) or 0.0, unit_len)
                        total_geom_len = z1 - z0
                    if tb is not None:
                        turning_zspan, band_od, tb_debug = tb
                        if (turning_zspan >= 0.10
                                and total_geom_len
                                and turning_zspan < total_geom_len * 0.85):
                            finish_len_in = round(turning_zspan, 4)
                            finish_len_via_turning_body = True
                        else:
                            finish_len_in = total_geom_len
                    else:
                        finish_len_in = total_geom_len

                if finish_len_in is None:
                    if totals and isinstance(totals, dict):
                        finish_len_in = _as_float(totals.get("total_length_in"))
                    if finish_len_in is None and has_z_range:
                        z0 = to_inches(_as_float(z_range[0]) or 0.0, unit_len)
                        z1 = to_inches(_as_float(z_range[1]) or 0.0, unit_len)
                        finish_len_in = z1 - z0
            else:
                logger.info("[RFQ_AUTOFILL] OCR OD rejected by geometry validation, will fall through to geometry")

            val_reasons = (geom_val_debug or {}).get("reasons", [])
            for r in val_reasons:
                _add_reason(r)

            logger.info(
                f"[RFQ_AUTOFILL] OCR-selected finish dims: "
                f"od={finish_od_in} id={finish_id_in} len={finish_len_in} "
                f"geom_validation={geom_val_debug}"
            )

        # --- Priority 2: Geometry fallback ---
        if finish_od_in is None and segments and len(segments) > 0:
            finish_source = "geometry"
            logger.info(f"[RFQ_AUTOFILL] No OCR OD found, falling back to geometry ({len(segments)} segments)")

            geometry_dims = self.compute_finish_dims_from_geometry(part_summary, mode_norm, unit_len)
            finish_od_in = geometry_dims.get("finish_od_in")
            finish_id_in = geometry_dims.get("finish_id_in")
            finish_len_in = geometry_dims.get("finish_len_in")
            main_segment = geometry_dims.get("main_segment")
            main_segment_idx = geometry_dims.get("main_segment_idx")
            if geometry_dims.get("finish_len_source") == "geometry.turning_body_zspan":
                finish_len_via_turning_body = True
            if geometry_dims.get("_id_auto_clamped"):
                id_auto_clamped = True
                _add_reason("ID_AUTO_CLAMPED")

        # --- Priority 3: Envelope fallback (no segments, no OCR) ---
        if finish_od_in is None:
            finish_source = "envelope"
            logger.warning("[RFQ_AUTOFILL] No OCR or geometry dims, using envelope fallback")

        # Length source tracking
        if totals and isinstance(totals, dict) and _as_float(totals.get("total_length_in")) is not None:
            used_z_range = False
        elif z_range and isinstance(z_range, (list, tuple)) and len(z_range) >= 2:
            used_z_range = True
        else:
            used_z_range = False
            used_len_fallback = True
        
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

        # Step B2: Finish OD/ID already computed from geometry above
        # If geometry didn't provide dimensions, they remain None (will be rejected)
        max_od_seg_conf = 0.0
        if main_segment and finish_od_in is not None:
            max_od_seg_conf = float(_as_float(main_segment.get("confidence")) or 0.0)
            od_pool_count = len(segments) if segments else 0
            od_pool_dropped_low_conf = False
        
        if finish_od_in is None or float(finish_od_in) <= 0:
            _add_reason("INVALID_FINISH_OD")

        # Step B3: Finish ID already computed from geometry above
        has_valid_bore = (finish_id_in is not None and finish_id_in > 0)
        # id_auto_clamped already initialized above; do NOT reset here (geometry may have set it)
        bore_coverage_pct = 0.0
        
        # Calculate bore coverage percentage
        if segments and finish_len_in and finish_id_in:
            bore_len_sum = 0.0
            for s in segments:
                if not isinstance(s, dict):
                    continue
                sl = seg_len_in(s)
                if sl <= 0:
                    continue
                idv = _as_float(s.get("id_diameter"))
                if idv is None:
                    continue
                id_in = float(to_inches(idv, unit_len))
                if id_in > 0 and abs(id_in - float(finish_id_in)) < 0.01:
                    bore_len_sum += float(sl)
            if finish_len_in > 0:
                bore_coverage_pct = max(0.0, min(100.0, 100.0 * bore_len_sum / float(finish_len_in)))

        # Step B4: Compute RAW required dimensions using new helper function
        rm_od_allowance_in = _as_float(tolerances.get("rm_od_allowance_in")) if isinstance(tolerances, dict) else None
        rm_len_allowance_in = _as_float(tolerances.get("rm_len_allowance_in")) if isinstance(tolerances, dict) else None
        if rm_od_allowance_in is None:
            rm_od_allowance_in = 0.26  # Updated default
        if rm_len_allowance_in is None:
            rm_len_allowance_in = 0.35

        # Compute RAW dimensions using helper function
        raw_dims = self.compute_raw_dims(
            finish_od=finish_od_in,
            finish_len=finish_len_in,
            rm_od_allowance_in=rm_od_allowance_in,
            rm_len_allowance_in=rm_len_allowance_in,
            segments=segments,
            mode=mode_norm,
            unit_len=unit_len,
            vendor_quote_mode=vendor_quote_mode
        )
        
        rm_od_in = raw_dims.get("rm_od_in")
        rm_len_in = raw_dims.get("rm_len_in")
        
        # ── Consolidated debug log ────────────────────────────────────────
        _ocr_cand = ocr_select_debug.get("candidates", {}) if ocr_select_debug else {}
        _fmt = lambda v: f"{v:.4f}" if v is not None else "None"
        logger.info(
            f"[RFQ_AUTOFILL] part_no={part_no} mode={mode_norm} vendor_quote_mode={vendor_quote_mode}\n"
            f"  ocr_candidates: od={_ocr_cand.get('od', 0)} id={_ocr_cand.get('id', 0)} len={_ocr_cand.get('len', 0)}\n"
            f"  ocr_selected: od={_fmt(ocr_select_debug.get('finish_od_in') if ocr_select_debug else None)}"
            f"  id={_fmt(ocr_select_debug.get('finish_id_in') if ocr_select_debug else None)}"
            f"  len={_fmt(ocr_select_debug.get('finish_len_in') if ocr_select_debug else None)}\n"
            f"  geom_validation: {geom_val_debug}\n"
            f"  final_finish: od={_fmt(finish_od_in)}(source={finish_source})"
            f"  id={_fmt(finish_id_in)}(source={finish_source})"
            f"  len={_fmt(finish_len_in)}(source={finish_source})\n"
            f"  rm: od={_fmt(rm_od_in)} len={_fmt(rm_len_in)}"
        )

        # Round outputs to max 3 decimals (deterministic formatting)
        def r3(x: Optional[float]) -> Optional[float]:
            if x is None:
                return None
            return float(round(float(x), 3))

        # OD spike suspect (optional detection)
        od_spike_suspect = False
        if finish_len_in is not None and float(finish_len_in) > 0 and segments and finish_od_in is not None:
            od_vals_pool: List[float] = []
            od_wts_pool: List[float] = []
            for s in segments:
                if not isinstance(s, dict):
                    continue
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
                for s in segments:
                    if not isinstance(s, dict):
                        continue
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

                # Also detect: a segment has a much larger OD than the selected finish_od_in
                # with tiny length support (e.g. chamfer segment inflating max OD).
                if not od_spike_suspect:
                    global_max_od = max(od_vals_pool)
                    if global_max_od > float(finish_od_in) * 1.20 + 0.10:
                        max_od_support = 0.0
                        for s in segments:
                            if not isinstance(s, dict):
                                continue
                            odv = _as_float(s.get("od_diameter"))
                            if odv is None:
                                continue
                            od_in_s = float(to_inches(odv, unit_len))
                            if abs(od_in_s - global_max_od) < 1e-6:
                                max_od_support += float(seg_len_in(s))
                        if float(finish_len_in) > 0 and max_od_support / float(finish_len_in) < 0.05:
                            od_spike_suspect = True
                            _add_reason("OD_SPIKE_SUSPECT")
                            # Only promote spike OD to finish OD when it has enough length
                            # to be a real feature (passes same 3% gate as build_od_bands).
                            _spike_gate = 0.03 * float(finish_len_in)
                            if max_od_support >= _spike_gate:
                                finish_od_in = global_max_od

        # Step 7 Reasons
        if used_len_fallback:
            _add_reason("Z_RANGE_MISSING_FALLBACK")
        if scale_method not in ("anchor_dimension", "calibrated_from_ocr", "dpi_based"):
            _add_reason("SCALE_ESTIMATED")

        # Step 8 Confidence rules per column
        # Scale confidence boost: anchor_dimension/calibrated_from_ocr = full,
        # dpi_based = partial, estimated/unknown = none.
        _GOOD_SCALE = scale_method in ("anchor_dimension", "calibrated_from_ocr")
        _OK_SCALE = scale_method == "dpi_based"

        # A) Finish OD confidence
        finish_od_conf = 0.6
        if finish_source == "ocr" or finish_source.startswith("intent_labeler"):
            finish_od_conf += 0.20
        if _GOOD_SCALE:
            finish_od_conf += 0.25
        elif _OK_SCALE:
            finish_od_conf += 0.15
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
        if finish_len_via_turning_body:
            finish_len_conf += 0.10
        if _GOOD_SCALE:
            finish_len_conf += 0.25
        elif _OK_SCALE:
            finish_len_conf += 0.15
        if validation_passed is True:
            finish_len_conf += 0.10
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
        if _GOOD_SCALE:
            finish_id_conf += 0.15
        elif _OK_SCALE:
            finish_id_conf += 0.10
        if id_auto_clamped:
            finish_id_conf -= 0.20
        finish_id_conf = _clamp01(finish_id_conf)

        # Conditional OCR override for finish_id_in
        finish_id_source = finish_source if finish_source == "ocr" else "geometry"
        geom_finish_id = finish_id_in
        
        # Check if override conditions are met
        should_override_id = (
            (finish_id_in is None or finish_id_in < 0.05) or
            bore_coverage_pct < 40.0 or
            finish_id_conf < 0.65
        )
        
        if should_override_id and finish_od_in is not None:
            # Try to extract OCR ID diameter
            ocr_id = self._extract_ocr_id_diameter(part_summary, job_id)
            
            if ocr_id is not None:
                # Validate OCR ID
                min_wall_thickness = 0.02  # Minimum wall thickness
                max_id = float(finish_od_in) - min_wall_thickness
                
                if ocr_id > 0 and ocr_id < max_id:
                    # Valid OCR ID found, override geometry ID
                    finish_id_in = float(ocr_id)
                    finish_id_source = "ocr.override.low_bore_coverage"
                    
                    reason_parts = []
                    if geom_finish_id is None or (geom_finish_id is not None and geom_finish_id < 0.05):
                        reason_parts.append("geom_id_too_small")
                    if bore_coverage_pct < 40.0:
                        reason_parts.append(f"bore_coverage_{bore_coverage_pct:.1f}%")
                    if finish_id_conf < 0.65:
                        reason_parts.append(f"low_conf_{finish_id_conf:.2f}")
                    
                    reason = "|".join(reason_parts) if reason_parts else "unknown"
                    
                    geom_id_str = f"{geom_finish_id:.4f}" if geom_finish_id is not None else "None"
                    logger.info(f"[RFQ_ID_OVERRIDE] applied=True geom_id={geom_id_str} ocr_id={ocr_id:.4f} selected={finish_id_in:.4f} reason={reason}")
                else:
                    geom_id_str = f"{geom_finish_id:.4f}" if geom_finish_id is not None else "None"
                    logger.info(f"[RFQ_ID_OVERRIDE] applied=False geom_id={geom_id_str} ocr_id={ocr_id:.4f} selected=None reason=ocr_id_invalid (ocr_id >= max_id={max_id:.4f} or ocr_id <= 0)")
            else:
                geom_id_str = f"{geom_finish_id:.4f}" if geom_finish_id is not None else "None"
                logger.info(f"[RFQ_ID_OVERRIDE] applied=False geom_id={geom_id_str} ocr_id=None selected=None reason=no_ocr_id_found")
        else:
            if finish_id_in is not None:
                logger.info(f"[RFQ_ID_OVERRIDE] applied=False geom_id={finish_id_in:.4f} ocr_id=None selected={finish_id_in:.4f} reason=geometry_id_valid (bore_coverage={bore_coverage_pct:.1f}%, conf={finish_id_conf:.2f})")

        # D) RM confidences
        rm_od_conf = min(finish_od_conf, 0.85)
        rm_len_conf = min(finish_len_conf, 0.85)

        # Additional reasons based on derived values/confidences
        if finish_id_in is not None and finish_id_in > 0 and bore_coverage_pct < 50.0:
            _add_reason("LOW_BORE_COVERAGE")
        if finish_id_in is not None and finish_id_in > 0 and finish_id_conf < 0.65:
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
                (not (_GOOD_SCALE or _OK_SCALE))
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
        finish_id_in = float(r3(finish_id_in)) if finish_id_in is not None else None
        rm_od_in = r3(rm_od_in)
        rm_len_in = r3(rm_len_in)

        # Extract features for time-based estimates
        features, used_feature_count_proxy = self._build_time_estimation_features(
            part_summary if isinstance(part_summary, dict) else {},
            finish_id_in,
            rm_len_in,
        )
        has_features = isinstance(features, dict) and features

        # Validate feature quality if we have a job_id
        if isinstance(job_id, str) and job_id.strip() and has_features and not used_feature_count_proxy:
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
                use_live_rate = cost_inputs.get("use_live_rate", True)
                currency = cost_inputs.get("currency", "USD")
                
                # Quantity inputs
                qty_moq = int(cost_inputs.get("qty_moq", 1) or 1)
                annual_potential_qty = int(cost_inputs.get("annual_potential_qty", 0) or 0)

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
                    oh_profit_pct = 0.20  # 20%
                if rejection_pct is None:
                    rejection_pct = 0.02  # 2%
                
                # Handle exchange rate - use live rate if enabled
                exchange_rate_source = "provided"
                exchange_rate_timestamp = None
                if use_live_rate or exchange_rate is None:
                    try:
                        result = get_live_exchange_rate(
                            from_currency=currency,
                            to_currency="INR",
                            fallback_rate=exchange_rate or 82.0,
                            include_timestamp=True,
                        )
                        live_rate, rate_source, rate_timestamp = result
                        exchange_rate = live_rate
                        exchange_rate_source = rate_source
                        exchange_rate_timestamp = rate_timestamp
                        print(f"[RFQ Autofill] Exchange rate {currency}/INR: {exchange_rate} (source: {exchange_rate_source}, fetched: {exchange_rate_timestamp})")
                    except Exception as e:
                        print(f"[RFQ Autofill] Warning: Failed to fetch live rate: {e}")
                        if exchange_rate is None:
                            exchange_rate = 82.0
                            exchange_rate_source = "default"

                if special_process_cost_in is None:
                    _add_reason("MISSING_SPECIAL_PROCESS")
                    special_process_cost_in = 0.0

                base_est_conf = min(float(finish_od_conf), float(finish_len_conf))
                weight_conf = base_est_conf - 0.10
                time_conf = base_est_conf - 0.20

                # If scale not anchored, reduce estimate confidence (v1)
                if not (_GOOD_SCALE or _OK_SCALE):
                    weight_conf -= 0.15
                    time_conf -= 0.15

                # Feature quality affects confidence
                feature_time_conf = time_conf
                if feature_quality:
                    if "FEATURES_TEXT_ONLY" in feature_quality.get("quality_issues", []):
                        # Text-only features with estimated scale get heavily reduced confidence
                        if not (_GOOD_SCALE or _OK_SCALE):
                            feature_time_conf = min(feature_time_conf, 0.3)  # Cap at 30% for text-only + estimated scale

                weight_conf = _clamp01(weight_conf)
                time_conf = _clamp01(time_conf)

                # RM Weight calculation using Excel template formula:
                # Weight = (π/4 × RM_OD² × RM_LEN - π/4 × RM_ID² × RM_LEN) × density
                # For solid bar stock: RM_ID = 0
                # Density: 7.86 g/cm³ = 7860 kg/m³ (steel)
                
                in3_to_m3 = 0.0254**3
                rm_od = float(rm_od_in or 0.0)
                rm_len = float(rm_len_in or 0.0)
                rm_id = 0.0  # Solid bar stock (most common case)
                if vendor_quote_mode:
                    _add_reason("VENDOR_QUOTE_SOLID_CYLINDER")
                else:
                    _add_reason("WEIGHT_SOLID_ASSUMPTION")
                
                # Calculate volumes using π × r² × L
                rm_od_r = rm_od / 2.0
                rm_id_r = rm_id / 2.0
                od_vol_in3 = math.pi * (rm_od_r**2) * rm_len
                id_vol_in3 = math.pi * (rm_id_r**2) * rm_len
                vol_in3 = od_vol_in3 - id_vol_in3
                
                vol_m3 = vol_in3 * in3_to_m3
                
                # Use 7860 kg/m³ to match Excel template (7.86 g/cm³)
                effective_density = float(density) if density != 7850.0 else 7860.0
                rm_weight_kg = float(vol_m3 * effective_density)
                
                used_bore_subtract = (rm_id > 0)

                material_cost = float(rm_weight_kg * float(rm_rate))

                # Calculate feature-based time estimates
                drilling_minutes = 0.0
                milling_minutes = 0.0
                vmc_minutes = 0.0

                if has_features:
                    if used_feature_count_proxy:
                        _add_reason("INTERNAL_BORE_TIME_PROXY")

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
                if not (_GOOD_SCALE or _OK_SCALE) and (drilling_minutes > 0 or milling_minutes > 0):
                    feature_time_conf = min(feature_time_conf, 0.4)

                # Turning time calculation matches template: Finish Length (MM) × 10 / 40
                finish_len_val = float(finish_len_in or 0.0)
                finish_len_mm_val = float(finish_len_val * 25.4)
                turning_minutes = float(finish_len_mm_val * 10.0 / 40.0)
                turning_cost = float(turning_minutes * float(turning_rate))

                # VMC cost (stored for reference; subtotal uses drilling_cost + milling_cost)
                vmc_cost = float(vmc_minutes * float(vmc_rate))

                # Feature-based costs (drilling/milling use turning rate for simplicity)
                drilling_cost = float(drilling_minutes * float(turning_rate)) if drilling_minutes > 0 else 0.0
                milling_cost = float(milling_minutes * float(turning_rate)) if milling_minutes > 0 else 0.0

                # Roughing cost formula from workbook: Finish Length (MM)
                if roughing_cost_in == 0.0:
                    roughing_cost_in = float(finish_len_mm_val)

                # Subtotal calculation (sum of all costs before markups).
                # Use rounded values to match the per-field precision so that
                # subtotal == sum_of_fields (no floating-point drift).
                subtotal_val = round(float(
                    (r3(material_cost) or 0.0)
                    + (r3(float(roughing_cost_in)) or 0.0)
                    + (r3(turning_cost) or 0.0)
                    + (r3(drilling_cost) or 0.0)
                    + (r3(milling_cost) or 0.0)
                    + (r3(float(special_process_cost_in)) or 0.0)
                    + (r3(float(others_cost_in)) or 0.0)
                    + (r3(float(inspection_cost_in)) or 0.0)
                ), 3)

                # Markup calculations based on subtotal
                pf_cost_val = float(subtotal_val * float(pf_pct))
                oh_profit_val = float(subtotal_val * float(oh_profit_pct))
                rejection_cost_val = float(subtotal_val * float(rejection_pct))

                # Final price calculations
                price_each_inr = float(subtotal_val + pf_cost_val + oh_profit_val + rejection_cost_val)
                price_each_currency = float(price_each_inr / float(exchange_rate)) if exchange_rate > 0 else 0.0

                # RM contribution percentage
                rm_contribution_pct_val = float((material_cost / price_each_inr) * 100.0) if price_each_inr > 0 else 0.0
                
                # Annual potential = Price/Each In Currency × Annual Potential Qty
                annual_potential_val = float(price_each_currency * annual_potential_qty) if annual_potential_qty > 0 else 0.0

                # Use feature-based confidence if features were used
                if has_features and (drilling_minutes > 0 or milling_minutes > 0):
                    subtotal_conf = _clamp01(min(weight_conf, feature_time_conf, base_est_conf))
                else:
                    subtotal_conf = _clamp01(min(weight_conf, time_conf, base_est_conf))

                # Create feature-based estimate fields
                zero_feature_conf = _clamp01(min(feature_time_conf, base_est_conf))
                drilling_minutes_field = _fv(0.0, zero_feature_conf, "rule.no_drilling_features")
                drilling_cost_field = _fv(0.0, zero_feature_conf, "rule.no_drilling_features")
                milling_minutes_field = _fv(0.0, zero_feature_conf, "rule.no_milling_features")
                milling_cost_field = _fv(0.0, zero_feature_conf, "rule.no_milling_features")
                vmc_minutes_field = _fv(0.0, zero_feature_conf, "rule.no_vmc_features")
                vmc_cost_field = _fv(0.0, zero_feature_conf, "rule.no_vmc_features")
                drilling_time_source = "feature_counts.internal_bores_time" if used_feature_count_proxy else "features.holes_time"
                drilling_cost_source = "feature_counts.time_x_rate" if used_feature_count_proxy else "features.time_x_rate"

                if drilling_minutes > 0:
                    drilling_minutes_field = _fv(r3(drilling_minutes), feature_time_conf, drilling_time_source)
                    drilling_cost_field = _fv(r3(drilling_cost), feature_time_conf, drilling_cost_source)

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
                    exchange_rate_used=_fv(r3(exchange_rate), 1.0, f"currency.{currency}_to_INR"),
                    exchange_rate_source=exchange_rate_source,
                    exchange_rate_timestamp=exchange_rate_timestamp,
                    annual_potential=_fv(r3(annual_potential_val), subtotal_conf, "rule.price_x_annual_qty") if annual_potential_val > 0 else None,
                )

        # Determine finish length source label
        finish_len_source = "unknown"
        if finish_source == "ocr" and ocr_select_debug and ocr_select_debug.get("finish_len_in") is not None:
            finish_len_source = "ocr.finish_len"
        elif finish_len_via_turning_body:
            finish_len_source = "geometry.turning_body_zspan"
        elif totals and isinstance(totals, dict) and _as_float(totals.get("total_length_in")) is not None:
            finish_len_source = "part_summary.totals.total_length_in"
        elif used_z_range:
            finish_len_source = "part_summary.z_range"
        elif used_len_fallback:
            finish_len_source = "part_summary.segments_fallback"
        else:
            finish_len_source = "part_summary.totals.total_length_in"

        finish_od_mm = r3(float(finish_od_in) * 25.4) if finish_od_in is not None else None
        finish_id_mm = r3(float(finish_id_in) * 25.4) if finish_id_in is not None and finish_id_in > 0 else None
        finish_len_mm = r3(float(finish_len_in) * 25.4) if finish_len_in is not None else None

        rm_id_in_val = 0.0

        if finish_source.startswith("intent_labeler"):
            _od_src = finish_source
            _id_src = finish_source if finish_id_in is not None else "geometry"
            _len_src = finish_source if finish_len_in is not None else finish_len_source
        elif finish_source == "ocr":
            _od_src = f"ocr.finish_od({ocr_select_debug.get('od_text','')[:30]})"
            _id_src = finish_id_source if finish_id_source != "geometry" else (f"ocr.finish_id({ocr_select_debug.get('id_text','')[:30]})" if ocr_select_debug and ocr_select_debug.get("finish_id_in") is not None else "geometry")
            _len_src = f"ocr.finish_len({ocr_select_debug.get('len_text','')[:30]})" if ocr_select_debug and ocr_select_debug.get("finish_len_in") is not None else finish_len_source
        else:
            _od_src = "part_summary.main_segment.od_diameter"
            _id_src = finish_id_source if finish_id_source != "geometry" else "geometry"
            _len_src = finish_len_source

        fields = RFQAutofillFields(
            finish_od_in=_fv(finish_od_in, finish_od_conf, _od_src),
            finish_len_in=_fv(finish_len_in, finish_len_conf, _len_src),
            finish_id_in=_fv(float(finish_id_in) if finish_id_in is not None else None, finish_id_conf, _id_src),
            finish_od_mm=_fv(finish_od_mm, finish_od_conf, "rule.inch_to_mm"),
            finish_id_mm=_fv(finish_id_mm, finish_id_conf, "rule.inch_to_mm"),
            finish_len_mm=_fv(finish_len_mm, finish_len_conf, "rule.inch_to_mm"),
            rm_od_in=_fv(rm_od_in, rm_od_conf, "rule.allowance_roundup"),
            rm_id_in=_fv(rm_id_in_val, rm_od_conf, "rule.solid_stock"),
            rm_len_in=_fv(rm_len_in, rm_len_conf, "rule.allowance_roundup"),
        )

        _ocr_texts: Optional[Dict[str, Any]] = None
        if ocr_select_debug:
            _ocr_texts = {
                "od": ocr_select_debug.get("od_text"),
                "id": ocr_select_debug.get("id_text"),
                "len": ocr_select_debug.get("len_text"),
            }

        # Collect geometry-band debug (classifier-aware)
        _geom_method = None
        _geom_env_od = None
        _od_bands_dbg = None
        _main_turning_len = None
        _main_band_od = None
        _main_band_z_span = None
        _main_band_score = None
        _flange_bands = None
        _band_env_max = None
        if finish_source == "geometry" and hasattr(self, "_last_band_debug") and self._last_band_debug:
            bd = self._last_band_debug
            _geom_method = bd.get("selected_feature_type", "band_classifier_main_body")
            _geom_env_od = bd.get("envelope_max_od")
            _od_bands_dbg = bd
            _main_band_od = bd.get("selected_od")
            _main_band_z_span = bd.get("selected_z_span")
            _main_band_score = bd.get("selected_score")
            _flange_bands = bd.get("flange_bands")
            _band_env_max = bd.get("envelope_max_od")
            if bd.get("bands_top6"):
                _main_turning_len = round(bd["bands_top6"][0].get("zspan", 0), 4)
        if finish_len_via_turning_body and finish_len_in is not None:
            _main_turning_len = float(finish_len_in)

        _overall_len = _as_float((totals or {}).get("total_length_in")) if isinstance(totals, dict) else None
        if _overall_len is None and has_z_range:
            _overall_len = to_inches((_as_float(z_range[1]) or 0.0), unit_len) - to_inches((_as_float(z_range[0]) or 0.0), unit_len)
        if _overall_len is None:
            _overall_len = float(finish_len_in or 0.0)

        debug = RFQAutofillDebug(
            max_od_in=float(global_max_od or finish_od_in or 0.0),
            overall_len_in=float(_overall_len),
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
            finish_source=finish_source,
            ocr_selected_texts=_ocr_texts,
            ocr_geom_validation=geom_val_debug,
            geom_finish_od_method=_geom_method,
            geom_envelope_od_in=_geom_env_od,
            od_bands_debug=_od_bands_dbg,
            main_turning_len_in=_main_turning_len,
            finish_len_source=finish_len_source,
            intent_labeler_used=intent_labeler_used,
            intent_labeler_status=intent_labeler_status,
            intent_labeler_reasons=intent_labeler_reasons if intent_labeler_reasons else None,
            main_band_od=_main_band_od,
            main_band_z_span=_main_band_z_span,
            main_band_score=_main_band_score,
            flange_band_candidates=_flange_bands,
            band_envelope_max_od=_band_env_max,
        )

        # ── RFQ_DIMENSION_TRACE: Final Selection ─────────────────────────
        _selection_source = "GEOMETRY"
        if finish_source == "ocr":
            _selection_source = "OCR"
        elif finish_source.startswith("intent_labeler"):
            _selection_source = "INTENT_LABELER"
        elif finish_source == "envelope":
            _selection_source = "FALLBACK"
        logger.info(
            f"{_T}[FINAL_SELECTION] "
            f"selected_finish_od={finish_od_in} "
            f"selected_finish_id={finish_id_in} "
            f"selected_finish_length={finish_len_in} "
            f"selection_source={_selection_source} "
            f"why_selected=source={finish_source} "
            f"intent_labeler_status={intent_labeler_status} "
            f"od_conf={finish_od_conf:.2f} len_conf={finish_len_conf:.2f} id_conf={finish_id_conf:.2f}"
        )

        _ocr_od_val = (ocr_select_debug.get("finish_od_in") if ocr_select_debug else None)
        _ocr_id_val = (ocr_select_debug.get("finish_id_in") if ocr_select_debug else None)
        _ocr_len_val = (ocr_select_debug.get("finish_len_in") if ocr_select_debug else None)
        _geom_od_val = global_max_od
        _geom_len_val = _as_float((totals or {}).get("total_length_in")) if isinstance(totals, dict) else None
        if _geom_len_val is None and has_z_range:
            _geom_len_val = to_inches((_as_float(z_range[1]) or 0.0), unit_len) - to_inches((_as_float(z_range[0]) or 0.0), unit_len)
        _geom_id_val = geom_finish_id
        logger.info(
            f"{_T}[OVERRIDES] "
            f"ocr_od={_ocr_od_val} geometry_od={_geom_od_val} final_od={finish_od_in} "
            f"ocr_id={_ocr_id_val} geometry_id={_geom_id_val} final_id={finish_id_in} "
            f"ocr_len={_ocr_len_val} geometry_len={_geom_len_val} final_len={finish_len_in}"
        )

        return RFQAutofillResponse(
            part_no=(part_no or "").strip(),
            fields=fields,
            status=status,  # type: ignore[arg-type]
            reasons=reasons,
            debug=debug,
            estimate=estimate,
        )


    def _build_time_estimation_features(
        self,
        part_summary: Dict[str, Any],
        finish_id_in: Optional[float],
        rm_len: float,
    ) -> Tuple[Optional[Dict[str, Any]], bool]:
        """Return detailed features when available, else synthesize a drilling proxy.

        STEP-backed jobs currently always provide `feature_counts`, but many do not yet
        persist detailed `features.holes` / `features.slots` arrays. When that happens,
        use the detected internal bore count as a conservative drilling proxy so RFQ
        export can still populate drilling time/cost columns.
        """
        if not isinstance(part_summary, dict):
            return None, False

        features = part_summary.get("features")
        existing_features = features if isinstance(features, dict) else None
        if existing_features:
            has_explicit_holes = isinstance(existing_features.get("holes"), list) and bool(existing_features.get("holes"))
            has_explicit_slots = isinstance(existing_features.get("slots"), list) and bool(existing_features.get("slots"))
            if has_explicit_holes or has_explicit_slots:
                return existing_features, False

        feature_counts = part_summary.get("feature_counts") or {}
        if not feature_counts:
            selected_body = part_summary.get("selected_body") or {}
            if isinstance(selected_body, dict):
                feature_counts = selected_body.get("feature_counts") or {}

        internal_bores = int(_as_float(feature_counts.get("internal_bores")) or 0)
        if internal_bores <= 0:
            return None, False

        positive_ids: List[float] = []
        bore_spans: List[float] = []
        for seg in part_summary.get("segments") or []:
            if not isinstance(seg, dict):
                continue
            diameter = _as_float(seg.get("id_diameter"))
            z_start = _as_float(seg.get("z_start"))
            z_end = _as_float(seg.get("z_end"))
            if diameter is None or diameter <= 0.0:
                continue
            positive_ids.append(float(diameter))
            if z_start is None or z_end is None:
                continue
            span = abs(float(z_end) - float(z_start))
            if span >= 0.05:
                bore_spans.append(span)

        representative_diameter = min(positive_ids) if positive_ids else (_as_float(finish_id_in) or 0.125)
        bore_spans.sort()
        if bore_spans:
            representative_depth = bore_spans[len(bore_spans) // 2]
        else:
            representative_depth = min(max(float(rm_len or 0.0) * 0.25, 0.25), float(rm_len or 0.25))

        rm_len_val = max(float(_as_float(rm_len) or 0.0), 0.0)
        if rm_len_val > 0.0:
            representative_depth = min(representative_depth, rm_len_val)
        representative_depth = max(representative_depth, 0.125)

        proxy_features = {
            "holes": [
                {
                    "diameter": float(representative_diameter),
                    "depth": float(representative_depth),
                    "kind": "axial",
                    "count": internal_bores,
                    "confidence": 0.8,
                }
            ],
            "slots": [],
            "chamfers": [],
            "fillets": [],
            "threads": [],
            "meta": {
                "source": "feature_counts.internal_bores",
                "internal_bore_count": internal_bores,
            },
        }

        if existing_features:
            merged_features = dict(existing_features)
            merged_features["holes"] = proxy_features["holes"]
            merged_meta = dict(existing_features.get("meta") or {})
            merged_meta.update(proxy_features["meta"])
            merged_features["meta"] = merged_meta
            for key in ("slots", "chamfers", "fillets", "threads"):
                if key not in merged_features:
                    merged_features[key] = proxy_features.get(key, [])
            return merged_features, True

        return proxy_features, True

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
            # Only filter on confidence when it is explicitly set to a low non-zero value;
            # default 0.0 (key absent) means "unknown" and should not be filtered out.
            if confidence is not None and float(confidence) > 0.0 and float(confidence) < min_confidence:
                continue

            # Base time per hole (positioning / tool touch-off)
            base_time_per_hole = 0.5
            
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

            total_time_per_hole = base_time_per_hole + drilling_time
            operation_minutes = total_time_per_hole * float(count)

            # Apply pattern setup once, not once per repeated hole count.
            if not setup_applied:
                operation_minutes += 1.0
                setup_applied = True

            total_minutes += operation_minutes

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

            total_time_per_slot = base_time_per_slot + milling_time
            operation_minutes = total_time_per_slot * float(count)

            # Apply pattern setup once, not once per repeated slot count.
            if not setup_applied:
                operation_minutes += 2.0
                setup_applied = True

            total_minutes += operation_minutes

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


def _debug_run_band_classifier(
    segments: List[Dict[str, Any]],
    total_len: float,
    unit_len: str = "in",
) -> None:
    """Debug harness: prints band classification for a given segment set.

    Usage from shell:
        from app.services.rfq_autofill_service import _debug_run_band_classifier
        _debug_run_band_classifier(segments_list, total_length)
    """
    svc = RFQAutofillService()
    bands = svc.build_od_bands(segments, total_len, unit_len)
    if not bands:
        print("[BAND_CLASSIFIER] No bands built from segments.")
        return

    svc.classify_bands(bands, total_len, segments, unit_len)
    main_band = svc.score_main_body_bands(bands)

    print(f"\n{'=' * 70}")
    print(f"  BAND CLASSIFIER DEBUG   total_len={total_len:.4f}  bands={len(bands)}")
    print(f"{'=' * 70}")

    scored = sorted(bands, key=lambda b: b.get("_mb_score", 0), reverse=True)
    for i, b in enumerate(scored):
        print(
            f"  [{i}] od={b['od_key']:.3f}  type={b.get('feature_type', '?'):10s}  "
            f"score={b.get('_mb_score', 0):+.4f}  "
            f"z_span={b['z_span']:.4f}  z_cont={b['z_continuity_ratio']:.3f}  "
            f"cov={b['coverage_ratio']:.3f}  interior={b.get('is_interior', 0)}  "
            f"endpoint={b.get('is_endpoint', False)}  "
            f"flange_cand={b.get('is_flange_candidate', False)}  "
            f"spike={b.get('is_spike', False)}  "
            f"left_nbr={b.get('left_neighbor_od')}  right_nbr={b.get('right_neighbor_od')}"
        )

    if main_band:
        print(
            f"\n  -> MAIN_BODY: od={main_band['od_key']:.3f}  "
            f"z_span={main_band['z_span']:.4f}  "
            f"score={main_band.get('_mb_score', 0):.4f}"
        )

    flange = [b for b in bands if b.get("feature_type") == "FLANGE"]
    if flange:
        print(f"  -> FLANGE bands ({len(flange)}):")
        for fb in flange:
            reasons = RFQAutofillService._flange_reasons(fb)
            print(f"     od={fb['od_key']:.3f}  z_span={fb['z_span']:.4f}  reasons={reasons}")
    else:
        print("  -> No FLANGE bands detected.")

    env_max = max(b["od_key"] for b in bands)
    print(f"  -> Envelope max OD: {env_max:.3f}")
    print(f"{'=' * 70}\n")


