"""Geometry Envelope Service v1.

Deterministic computation of finish and raw material dimensions from 3D geometry.
This service is the source-of-truth for OD/Length envelope calculations.
"""

import math
from typing import Any, Dict, List, Optional, Tuple

from ..models.rfq_envelope import EnvelopeFields, EnvelopeRequest, EnvelopeResponse
from ..services.rfq_autofill_service import _clamp_range, ceil_to_step


class GeometryEnvelopeService:
    """Service for computing deterministic geometry envelopes from part_summary data."""

    def compute_envelope(self, request: EnvelopeRequest) -> EnvelopeResponse:
        """Compute geometry envelope from request."""
        # Load part summary
        part_summary = self._load_part_summary(request.source)

        # Extract inputs
        units = part_summary.get("units", {})
        length_unit = units.get("length", "in")
        segments = part_summary.get("segments", [])
        z_range = part_summary.get("z_range")
        scale_report = part_summary.get("scale_report", {})
        inference_metadata = part_summary.get("inference_metadata", {})

        # Validate units
        if length_unit not in ["in", "mm"]:
            return self._create_error_response(
                request.part_no,
                ["UNKNOWN_UNITS"],
                f"Unsupported length unit: {length_unit}"
            )

        # Compute finish dimensions
        finish_len_in = self._compute_finish_len_in(z_range, segments, length_unit)
        finish_max_od_in = self._compute_finish_max_od_in(segments, finish_len_in, length_unit)

        # Validate required dimensions
        if finish_len_in is None or finish_max_od_in is None:
            return self._create_error_response(
                request.part_no,
                ["MISSING_DIMENSIONS"],
                "Could not compute finish length or OD from geometry"
            )

        # Get allowances and rounding
        allowance_od_in = request.allowances.get("od_in", 0.125)  # Default 1/8"
        allowance_len_in = request.allowances.get("len_in", 0.25)  # Default 1/4"
        od_step = request.rounding.get("od_step", 0.05)
        len_step = request.rounding.get("len_step", 0.10)

        # Compute raw required dimensions
        raw_max_od_in = ceil_to_step(finish_max_od_in + allowance_od_in, od_step)
        raw_len_in = ceil_to_step(finish_len_in + allowance_len_in, len_step)

        # Compute confidence and status
        base_conf = _clamp_range(
            inference_metadata.get("overall_confidence", 0.5),
            0.35, 0.90
        )

        scale_method = scale_report.get("method", "unknown")
        validation_passed = scale_report.get("validation_passed")

        # Determine status and reasons
        status, reasons = self._compute_status_and_reasons(
            scale_method, validation_passed, base_conf,
            finish_max_od_in, finish_len_in
        )

        # Create field values
        fields = EnvelopeFields(
            finish_max_od_in=self._create_field_value(finish_max_od_in, base_conf, "part_summary.max_od"),
            finish_len_in=self._create_field_value(finish_len_in, base_conf, "part_summary.z_range"),
            raw_max_od_in=self._create_field_value(raw_max_od_in, base_conf, "rule.allowance+rounding"),
            raw_len_in=self._create_field_value(raw_len_in, base_conf, "rule.allowance+rounding")
        )

        # Create debug info
        debug = {
            "max_od_in": finish_max_od_in,
            "overall_len_in": finish_len_in,
            "min_len_gate_in": max(0.02, 0.01 * finish_len_in),
            "scale_method": scale_method,
            "overall_confidence": base_conf,
            "validation_passed": validation_passed,
            "notes": []
        }

        return EnvelopeResponse(
            part_no=request.part_no,
            fields=fields,
            status=status,
            reasons=reasons,
            debug=debug
        )

    def _load_part_summary(self, source: Dict[str, Any]) -> Dict[str, Any]:
        """Load part summary from source (job_id or direct part_summary)."""
        if "part_summary" in source:
            return source["part_summary"]
        elif "job_id" in source:
            # Load from job storage (reuse logic from rfq.py)
            from ..storage.file_storage import FileStorage
            import json
            from pathlib import Path

            job_id = str(source["job_id"]).strip()
            if (".." in job_id) or ("/" in job_id) or ("\\" in job_id):
                raise ValueError("Invalid job_id")

            fs = FileStorage()
            job_path = fs.get_job_path(job_id)
            outputs_path = fs.get_outputs_path(job_id)
            summary_file = outputs_path / "part_summary.json"

            try:
                summary_file.resolve().relative_to(job_path.resolve())
            except Exception:
                raise ValueError("Invalid outputs path")

            if not summary_file.exists() or not summary_file.is_file():
                raise FileNotFoundError("part_summary.json not found for job_id")

            try:
                with open(summary_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except json.JSONDecodeError:
                raise ValueError("Invalid JSON in part_summary.json")
            except OSError:
                raise ValueError("Failed to read part_summary.json")
        else:
            raise ValueError("Source must contain either 'part_summary' or 'job_id'")

    def _compute_finish_len_in(self, z_range: Optional[List[float]],
                              segments: List[Dict[str, Any]],
                              length_unit: str) -> Optional[float]:
        """Compute finish length in inches."""
        # Prefer z_range if available
        if z_range and len(z_range) >= 2:
            length = z_range[1] - z_range[0]
        else:
            # Fallback to segments
            if not segments:
                return None
            z_starts = [seg.get("z_start", 0) for seg in segments if "z_start" in seg]
            z_ends = [seg.get("z_end", 0) for seg in segments if "z_end" in seg]
            if not z_starts or not z_ends:
                return None
            length = max(z_ends) - min(z_starts)

        # Convert to inches if needed
        if length_unit == "mm":
            length = length / 25.4

        return float(length)

    def _compute_finish_max_od_in(self, segments: List[Dict[str, Any]],
                                 finish_len_in: float, length_unit: str) -> Optional[float]:
        """Compute robust max OD in inches using segment filtering."""
        if not segments:
            return None

        # Define minimum length gate
        min_len_gate_in = max(0.02, 0.01 * finish_len_in)

        # Convert gate to source units for filtering
        min_len_gate = min_len_gate_in
        if length_unit == "mm":
            min_len_gate = min_len_gate_in * 25.4

        # Build pool of candidate segments
        candidates = []
        for seg in segments:
            seg_len = abs(seg.get("z_end", 0) - seg.get("z_start", 0))
            if seg_len >= min_len_gate:
                od_diameter = seg.get("od_diameter")
                if od_diameter is not None:
                    candidates.append({
                        "od": float(od_diameter),
                        "len": seg_len,
                        "low_conf": seg.get("flag", {}).get("low_confidence", False)
                    })

        if not candidates:
            # Fallback: max OD across all segments
            all_ods = [seg.get("od_diameter") for seg in segments if seg.get("od_diameter") is not None]
            if not all_ods:
                return None
            max_od = max(all_ods)
        else:
            # Filter low confidence if we have >= 2 candidates
            if len(candidates) >= 2:
                filtered = [c for c in candidates if not c["low_conf"]]
                if filtered:
                    candidates = filtered

            max_od = max(c["od"] for c in candidates)

        # Convert to inches if needed
        if length_unit == "mm":
            max_od = max_od / 25.4

        return float(max_od)

    def _compute_status_and_reasons(self, scale_method: str, validation_passed: Optional[bool],
                                   base_conf: float, finish_od: float, finish_len: float) -> Tuple[str, List[str]]:
        """Compute status and reasons based on validation flags."""
        reasons = []

        # Check validation failure
        if validation_passed is False:
            reasons.append("VALIDATION_FAILED")
            return "REJECTED", reasons

        # Check scale method
        if scale_method != "anchor_dimension":
            reasons.append("SCALE_ESTIMATED")

        # Check for missing/invalid dimensions
        if not (finish_od > 0 and finish_len > 0):
            reasons.append("INVALID_DIMENSIONS")
            return "REJECTED", reasons

        # Determine status
        if reasons:  # Has issues like SCALE_ESTIMATED
            status = "NEEDS_REVIEW"
        elif base_conf >= 0.85:
            status = "AUTO_FILLED"
        else:
            status = "NEEDS_REVIEW"

        return status, reasons

    def _create_field_value(self, value: float, confidence: float, source: str):
        """Create RFQFieldValue object."""
        from ..models.rfq_autofill import RFQFieldValue
        return RFQFieldValue(value=value, confidence=confidence, source=source)

    def _create_error_response(self, part_no: str, reasons: List[str], note: str) -> EnvelopeResponse:
        """Create error response for invalid geometry."""
        from ..models.rfq_envelope import EnvelopeDebug

        # Create empty fields with null values
        empty_field = self._create_field_value(None, 0.0, "error")
        fields = EnvelopeFields(
            finish_max_od_in=empty_field,
            finish_len_in=empty_field,
            raw_max_od_in=empty_field,
            raw_len_in=empty_field
        )

        debug = EnvelopeDebug(
            max_od_in=0.0,
            overall_len_in=0.0,
            min_len_gate_in=0.0,
            scale_method="error",
            overall_confidence=0.0,
            notes=[note]
        )

        return EnvelopeResponse(
            part_no=part_no,
            fields=fields,
            status="REJECTED",
            reasons=reasons,
            debug=debug
        )
