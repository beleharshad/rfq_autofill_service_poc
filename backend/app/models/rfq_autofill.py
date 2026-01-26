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


class RFQAutofillCostInputs(BaseModel):
    """Optional inputs for Quick Quote (Envelope) estimate block."""

    rm_rate_per_kg: float = Field(..., description="Raw material rate per kg")
    turning_rate_per_min: float = Field(..., description="Turning rate per minute")
    vmc_rate_per_min: float = Field(7.5, description="VMC rate per minute (default 7.5)")
    roughing_cost: float = Field(0.0, description="Optional roughing cost (flat)")
    inspection_cost: float = Field(0.0, description="Optional inspection cost (flat)")
    special_process_cost: Optional[float] = Field(None, description="Optional special process cost (flat)")
    others_cost: float = Field(0.0, description="Other miscellaneous costs")
    material_density_kg_m3: float = Field(7850.0, description="Material density in kg/m^3 (default steel ~7850)")
    
    # Markup percentages (as decimals, e.g., 0.03 for 3%)
    pf_pct: float = Field(0.03, description="P&F percentage (default 3%)")
    oh_profit_pct: float = Field(0.15, description="OH & Profit percentage (default 15%)")
    rejection_pct: float = Field(0.02, description="Rejection cost percentage (default 2%)")
    
    # Currency conversion
    exchange_rate: float = Field(82.0, description="Exchange rate (INR to target currency, default 82)")
    currency: str = Field("USD", description="Target currency code")


class RFQAutofillEstimate(BaseModel):
    """Quick Quote (Envelope) estimate outputs (optional)."""

    # Basic costs
    rm_weight_kg: RFQFieldValue
    material_cost: RFQFieldValue
    roughing_cost: RFQFieldValue
    inspection_cost: RFQFieldValue
    special_process_cost: RFQFieldValue
    
    # Time-based costs
    turning_minutes: RFQFieldValue
    turning_cost: RFQFieldValue
    vmc_minutes: Optional[RFQFieldValue] = Field(None, description="VMC machining time")
    vmc_cost: Optional[RFQFieldValue] = Field(None, description="VMC machining cost")
    drilling_minutes: Optional[RFQFieldValue] = Field(None, description="Drilling time based on detected holes")
    drilling_cost: Optional[RFQFieldValue] = Field(None, description="Drilling cost based on detected holes")
    milling_minutes: Optional[RFQFieldValue] = Field(None, description="Milling time based on detected slots")
    milling_cost: Optional[RFQFieldValue] = Field(None, description="Milling cost based on detected slots")
    
    # Other costs
    others_cost: Optional[RFQFieldValue] = Field(None, description="Other miscellaneous costs")
    
    # Subtotal and markups
    subtotal: RFQFieldValue
    pf_cost: Optional[RFQFieldValue] = Field(None, description="P&F cost (3% of subtotal)")
    oh_profit: Optional[RFQFieldValue] = Field(None, description="OH & Profit (15% of subtotal)")
    rejection_cost: Optional[RFQFieldValue] = Field(None, description="Rejection cost (2% of subtotal)")
    
    # Final prices
    price_each_inr: Optional[RFQFieldValue] = Field(None, description="Price per piece in INR")
    price_each_currency: Optional[RFQFieldValue] = Field(None, description="Price per piece in target currency")
    
    # Contribution metrics
    rm_contribution_pct: Optional[RFQFieldValue] = Field(None, description="RM contribution percentage")
    
    # Legacy field
    total_estimate: RFQFieldValue


class RFQAutofillRequest(BaseModel):
    """Request model for RFQ AutoFill."""

    rfq_id: str = Field(..., description="RFQ identifier")
    part_no: str = Field(..., description="Part number")
    mode: Literal["ENVELOPE", "GEOMETRY"] = Field("ENVELOPE", description="AutoFill mode (v1 default ENVELOPE)")
    vendor_quote_mode: bool = Field(
        False,
        description="Enable vendor quote mode: uses solid cylinder (no bore), no rounding for RM dimensions, matches Excel exactly",
    )
    source: RFQAutofillSource
    tolerances: RFQAutofillTolerances
    cost_inputs: Optional[RFQAutofillCostInputs] = Field(
        None,
        description="Optional cost inputs for ENVELOPE estimate block (if omitted, estimate is not returned)",
    )


class RFQAutofillFields(BaseModel):
    """Fixed set of RFQ fields returned by the v1 AutoFill contract."""

    # Finish dimensions (inches)
    finish_od_in: RFQFieldValue
    finish_len_in: RFQFieldValue
    finish_id_in: RFQFieldValue
    
    # Finish dimensions (mm) - computed from inches
    finish_od_mm: Optional[RFQFieldValue] = Field(None, description="Finish OD in mm")
    finish_id_mm: Optional[RFQFieldValue] = Field(None, description="Finish ID in mm")
    finish_len_mm: Optional[RFQFieldValue] = Field(None, description="Finish Length in mm")
    
    # Raw material dimensions (inches)
    rm_od_in: RFQFieldValue
    rm_id_in: Optional[RFQFieldValue] = Field(None, description="RM ID in inches (usually 0 for solid stock)")
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
    estimate: Optional[RFQAutofillEstimate] = None


