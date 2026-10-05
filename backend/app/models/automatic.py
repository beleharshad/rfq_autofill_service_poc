"""Untrusted drawing proposals: bounded data, never executable model code."""
from typing import Literal
from pydantic import Field
from app.models.accepted_part import StrictModel, Base, Feature


class DimensionCheck(StrictModel):
    metric: Literal['envelope_width_mm', 'envelope_height_mm', 'axial_length_mm',
                    'maximum_outer_cylindrical_diameter_mm', 'minimum_coaxial_through_bore_mm']
    lower: float = Field(gt=0, le=10000)
    upper: float = Field(gt=0, le=10000)
    page: int = Field(ge=1, le=20)
    callout: str = Field(min_length=1, max_length=500)


class DrawingEvidence(StrictModel):
    target: str = Field(min_length=1, max_length=100)  # base or an explicit feature_id
    page: int = Field(ge=1, le=20)
    callout: str = Field(min_length=1, max_length=1000)


class DrawingProposal(StrictModel):
    part_number: str | None = Field(default=None, max_length=200)
    revision: str | None = Field(default=None, max_length=100)
    role: Literal['finished_drawing', 'process_drawing', 'unsupported']
    units: Literal['mm', 'in']
    material: str | None = Field(default=None, max_length=200)
    base: Base | None = None
    features: list[Feature] = Field(default_factory=list, max_length=100)
    evidence: list[DrawingEvidence] = Field(default_factory=list, max_length=200)
    complete: bool
    unresolved: list[str] = Field(default_factory=list, max_length=100)


class DrawingAudit(StrictModel):
    agrees: bool
    part_number: str | None = None
    revision: str | None = None
    finished_part: bool
    all_features_accounted_for: bool
    checked_targets: list[str] = Field(max_length=101)
    # Always mm, independently read from drawing limits (not copied from recipe).
    dimensions: list[DimensionCheck] = Field(max_length=20)
    issues: list[str] = Field(max_length=100)
