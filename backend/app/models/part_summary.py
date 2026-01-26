"""Part summary models."""

from datetime import datetime
from typing import Optional, List, Dict, Any

from pydantic import BaseModel, Field, AliasChoices

from .features import DetectedFeatures


class PartSummarySegment(BaseModel):
    """A segment in the part summary."""
    z_start: float = Field(..., description="Starting Z coordinate")
    z_end: float = Field(..., description="Ending Z coordinate")
    od_diameter: float = Field(..., description="Outer diameter")
    id_diameter: float = Field(..., description="Inner diameter")
    wall_thickness: Optional[float] = Field(None, description="Wall thickness")
    volume_in3: float = Field(..., description="Volume in cubic inches")
    od_area_in2: float = Field(..., description="Outer diameter surface area in square inches")
    id_area_in2: float = Field(..., description="Inner diameter surface area in square inches")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence score")
    flags: List[str] = Field(default_factory=list, description="Feature flags")


class PartSummaryTotals(BaseModel):
    """Totals for the part summary."""
    # Back-compat: older producers used `volume_in3` / `od_area_in2` / `id_area_in2`.
    # Accept both shapes, but always serialize using the `total_*` keys.
    total_volume_in3: float = Field(
        ...,
        validation_alias=AliasChoices("total_volume_in3", "volume_in3"),
        serialization_alias="total_volume_in3",
        description="Total volume in cubic inches",
    )
    total_od_area_in2: float = Field(
        ...,
        validation_alias=AliasChoices("total_od_area_in2", "od_area_in2"),
        serialization_alias="total_od_area_in2",
        description="Total outer diameter surface area in square inches",
    )
    total_id_area_in2: float = Field(
        ...,
        validation_alias=AliasChoices("total_id_area_in2", "id_area_in2"),
        serialization_alias="total_id_area_in2",
        description="Total inner diameter surface area in square inches",
    )
    total_length_in: float = Field(..., description="Total length in inches")


class PartSummaryInferenceMetadata(BaseModel):
    """Metadata about the inference process."""
    mode: str = Field(..., description="Inference mode (reference_only, auto_detect, etc.)")
    overall_confidence: float = Field(..., ge=0.0, le=1.0, description="Overall confidence score")
    source: str = Field(..., description="Source of the inference")


class PartSummaryUnits(BaseModel):
    """Units used in the part summary."""
    length: str = Field(..., description="Length unit")
    area: str = Field(..., description="Area unit")
    volume: str = Field(..., description="Volume unit")


class PartSummaryScaleReport(BaseModel):
    """Scale calibration information."""
    method: str = Field(..., description="Calibration method")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Scale confidence")
    notes: Optional[str] = Field(None, description="Scale calibration notes")


class PartSummary(BaseModel):
    """Complete part summary including geometric features."""
    schema_version: str = Field(..., description="Schema version")
    generated_at_utc: str = Field(..., description="Generation timestamp")
    units: PartSummaryUnits = Field(..., description="Units used")
    scale_report: PartSummaryScaleReport = Field(..., description="Scale calibration report")
    z_range: List[float] = Field(..., min_items=2, max_items=2, description="Z coordinate range")
    segments: List[PartSummarySegment] = Field(..., description="Part segments")
    totals: PartSummaryTotals = Field(..., description="Calculated totals")
    inference_metadata: PartSummaryInferenceMetadata = Field(..., description="Inference metadata")
    features: Optional[DetectedFeatures] = Field(None, description="Detected geometric features")

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PartSummary":
        """Create from dictionary (for JSON deserialization)."""
        return cls.model_validate(data)

    def has_features(self) -> bool:
        """Check if features have been detected."""
        return self.features is not None and not self.features.is_empty()