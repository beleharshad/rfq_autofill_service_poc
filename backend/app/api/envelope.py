"""Geometry Envelope endpoints."""

import logging
from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from app.models.rfq_envelope import EnvelopeRequest, EnvelopeResponse
from app.services.geometry_envelope_service import GeometryEnvelopeService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/rfq", tags=["rfq"])


@router.post("/envelope")
async def rfq_envelope(request: EnvelopeRequest) -> EnvelopeResponse:
    """
    Compute geometry envelope from 3D part summary.

    This endpoint provides deterministic calculation of finish and raw material
    dimensions from part_summary.json geometry data. It serves as the source-of-truth
    for OD/Length envelope computations, replacing OCR-based dimension extraction.

    Args:
        request: Envelope computation request with part summary source and allowances

    Returns:
        EnvelopeResponse with computed dimensions, confidence, and validation status
    """
    if not request.rfq_id or not request.rfq_id.strip():
        raise HTTPException(status_code=400, detail="rfq_id is required")

    if not request.part_no or not request.part_no.strip():
        raise HTTPException(status_code=400, detail="part_no is required")

    # Validate source
    if "job_id" not in request.source and "part_summary" not in request.source:
        raise HTTPException(
            status_code=400,
            detail="source must contain either 'job_id' or 'part_summary'"
        )

    # Validate allowances
    required_allowances = ["od_in", "len_in"]
    missing_allowances = [k for k in required_allowances if k not in request.allowances]
    if missing_allowances:
        raise HTTPException(
            status_code=400,
            detail=f"Missing required allowances: {missing_allowances}"
        )

    try:
        # TASK 1: Runtime verification - log BEFORE calibration
        part_summary = request.source.get("part_summary")
        job_id = request.source.get("job_id")
        
        if part_summary:
            scale_report_before = part_summary.get("scale_report", {})
            scale_method_before = scale_report_before.get("method", "unknown") if isinstance(scale_report_before, dict) else "unknown"
            
            segments_before = part_summary.get("segments", [])
            max_od_before = 0.0
            if segments_before:
                max_od_before = max(seg.get("od_diameter", 0.0) for seg in segments_before if isinstance(seg, dict))
            
            totals_before = part_summary.get("totals", {})
            total_length_before = totals_before.get("total_length_in", 0.0) if isinstance(totals_before, dict) else 0.0
            
            logger.info(f"[RFQ_ENVELOPE_BEFORE_CALIBRATION] scale_method={scale_method_before}, max_od_in={max_od_before:.4f}, total_length_in={total_length_before:.4f}")
        
        svc = GeometryEnvelopeService()
        response = svc.compute_envelope(request)
        
        # TASK 1: Add debug fields for scale calibration (populated by service)
        # The service will populate these fields during calibration
        
        return response
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Envelope computation failed: {e}")
