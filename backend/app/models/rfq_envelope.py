"""Geometry Envelope v1 models.

Implements the API contract for POST /api/v1/rfq/envelope.
Provides deterministic computation of finish and raw material dimensions from 3D geometry.
"""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class EnvelopeRequest(BaseModel):
    """Request model for Geometry Envelope computation."""

    rfq_id: str = Field(..., description="RFQ identifier")
    part_no: str = Field(..., description="Part number")
    source: Dict[str, Any] = Field(
        ...,
        description="Source data: either {'job_id': str} or {'part_summary': dict}"
    )
    allowances: Dict[str, float] = Field(
        ...,
        description="Allowances: {'od_in': float, 'len_in': float}"
    )
    rounding: Dict[str, float] = Field(
        default_factory=lambda: {"od_step": 0.05, "len_step": 0.10},
        description="Rounding steps: {'od_step': float, 'len_step': float}"
    )


class EnvelopeFields(BaseModel):
    """Geometry envelope dimension fields."""

    finish_max_od_in: "RFQFieldValue"
    finish_len_in: "RFQFieldValue"
    raw_max_od_in: "RFQFieldValue"
    raw_len_in: "RFQFieldValue"


class EnvelopeDebug(BaseModel):
    """Debug payload for geometry envelope computation."""

    max_od_in: float
    overall_len_in: float
    min_len_gate_in: float
    scale_method: str
    overall_confidence: float
    validation_passed: Optional[bool] = None
    notes: List[str] = Field(default_factory=list)


class EnvelopeResponse(BaseModel):
    """Response model for Geometry Envelope."""

    part_no: str
    fields: EnvelopeFields
    status: Literal["AUTO_FILLED", "NEEDS_REVIEW", "REJECTED"]
    reasons: List[str] = Field(default_factory=list)
    debug: EnvelopeDebug


# Import here to avoid circular imports
from .rfq_autofill import RFQFieldValue

# Update forward references
EnvelopeFields.model_rebuild()
