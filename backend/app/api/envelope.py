"""Geometry Envelope endpoints."""

from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from app.models.rfq_envelope import EnvelopeRequest, EnvelopeResponse
from app.services.geometry_envelope_service import GeometryEnvelopeService

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
        svc = GeometryEnvelopeService()
        response = svc.compute_envelope(request)
        return response
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Envelope computation failed: {e}")
