"""RFQ AutoFill v1 models.

Implements the API contract for POST /api/v1/rfq/autofill.
"""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class RFQFieldValue(BaseModel):
    """A single extracted value with confidence and provenance."""

    value: Optional[float] = Field(None, description="Field value in inches, or null if unavailable")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence score [0.0, 1.0]")
    source: str = Field(..., description="Provenance string describing how the value was derived")


class RFQAutofillSource(BaseModel):
    """Inputs used by the AutoFill service."""

    part_summary: Optional[Dict[str, Any]] = Field(
        None,
        description="part_summary-like JSON produced by the pipeline (required unless job_id provided)",
    )
    job_id: Optional[str] = Field(
        None,
        description="Optional job id to load outputs/part_summary.json from server-side storage",
    )
    step_metrics: Optional[Dict[str, Any]] = Field(
        None,
        description="Optional STEP metrics (not used in v1)",
    )


class RFQAutofillTolerances(BaseModel):
    """Allowance inputs for converting finish dims to raw material dims."""

    rm_od_allowance_in: float = Field(..., description="OD allowance in inches")
    rm_len_allowance_in: float = Field(..., description="Length allowance in inches")


class RFQAutofillRequest(BaseModel):
    """Request model for RFQ AutoFill."""

    rfq_id: str = Field(..., description="RFQ identifier")
    part_no: str = Field(..., description="Part number")
    source: RFQAutofillSource
    tolerances: RFQAutofillTolerances


class RFQAutofillFields(BaseModel):
    """Fixed set of RFQ fields returned by the v1 AutoFill contract."""

    finish_od_in: RFQFieldValue
    finish_len_in: RFQFieldValue
    finish_id_in: RFQFieldValue
    rm_od_in: RFQFieldValue
    rm_len_in: RFQFieldValue


class RFQAutofillDebug(BaseModel):
    """Debug payload matching the v1 AutoFill contract."""

    max_od_in: float
    overall_len_in: float
    scale_method: str
    overall_confidence: float
    min_len_gate_in: float
    bore_coverage_pct: float
    max_od_seg_conf: Optional[float] = None
    used_z_range: Optional[bool] = None
    od_pool_count: Optional[int] = None
    od_pool_dropped_low_conf: Optional[bool] = None
    id_auto_clamped: Optional[bool] = None
    od_spike_suspect: Optional[bool] = None


class RFQAutofillResponse(BaseModel):
    """Response model for RFQ AutoFill."""

    part_no: str
    fields: RFQAutofillFields
    status: Literal["AUTO_FILLED", "NEEDS_REVIEW", "REJECTED"]
    reasons: List[str] = Field(default_factory=list)
    debug: RFQAutofillDebug


