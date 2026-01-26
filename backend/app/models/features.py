"""Detected geometric features models."""

from datetime import datetime
from typing import Optional, List, Dict, Any, Union, Literal
from pydantic import BaseModel, Field, field_validator


class FeatureBase(BaseModel):
    """Base class for all detected features."""
    type: str = Field(..., description="Feature type (hole, slot, chamfer, fillet, thread)")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Detection confidence score")
    source_page: int = Field(..., ge=0, description="Page number in PDF where feature was detected")
    source_view_index: Optional[int] = Field(None, ge=0, description="Index of view within the page (assigned by association logic)")
    source_bbox: Optional[List[float]] = Field(None, min_length=4, max_length=4, description="Bounding box [x1, y1, x2, y2] of the text in source image")
    assigned_view_bbox: Optional[List[float]] = Field(None, min_length=4, max_length=4, description="Bounding box [x1, y1, x2, y2] of the assigned view (normalized coordinates)")
    view_association_confidence: Optional[float] = Field(None, ge=0.0, le=1.0, description="Confidence in view association (0.0-1.0)")
    notes: Optional[str] = Field(None, description="Additional notes about the feature")

    @field_validator('source_bbox', 'assigned_view_bbox', mode='before')
    @classmethod
    def validate_bbox(cls, v):
        """Validate bounding box format."""
        if v is None:
            return v
        if isinstance(v, list) and len(v) == 4:
            return v
        raise ValueError("Bounding box must be a list of 4 floats [x1, y1, x2, y2]")

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FeatureBase":
        """Create from dictionary (for JSON deserialization)."""
        return cls.model_validate(data)


class HoleFeature(FeatureBase):
    """Detected hole feature."""
    type: Literal["hole"] = "hole"
    diameter: float = Field(..., gt=0.0, description="Hole diameter in inches")
    depth: Optional[float] = Field(None, gt=0.0, description="Hole depth in inches (None for through holes)")
    kind: str = Field(..., pattern=r"^(cross|axial)$", description="Hole type: cross (radial) or axial")
    count: Optional[int] = Field(None, ge=1, description="Number of holes (for patterns)")
    pattern: Optional[str] = Field(None, description="Pattern description (e.g., 'equally spaced', 'centered')")
    geometry_px: Optional[Dict[str, Any]] = Field(None, description="Geometric data in pixels")
    geometry_in: Optional[Dict[str, Any]] = Field(None, description="Geometric data in inches")


class SlotFeature(FeatureBase):
    """Detected slot feature."""
    type: Literal["slot"] = "slot"
    width: float = Field(..., gt=0.0, description="Slot width in inches")
    length: float = Field(..., gt=0.0, description="Slot length in inches")
    depth: Optional[float] = Field(None, gt=0.0, description="Slot depth in inches")
    orientation: str = Field(..., pattern=r"^(radial|axial|circumferential)$", description="Slot orientation")
    count: Optional[int] = Field(None, ge=1, description="Number of slots (for patterns)")
    pattern: Optional[str] = Field(None, description="Pattern description")
    geometry_px: Optional[Dict[str, Any]] = Field(None, description="Geometric data in pixels")
    geometry_in: Optional[Dict[str, Any]] = Field(None, description="Geometric data in inches")


class ChamferFeature(FeatureBase):
    """Detected chamfer feature."""
    type: Literal["chamfer"] = "chamfer"
    size: float = Field(..., gt=0.0, description="Chamfer size/dimension in inches")
    angle: float = Field(..., gt=0.0, le=90.0, description="Chamfer angle in degrees")
    edge_location: str = Field(..., description="Location of chamfered edge")


class FilletFeature(FeatureBase):
    """Detected fillet feature."""
    type: Literal["fillet"] = "fillet"
    radius: float = Field(..., gt=0.0, description="Fillet radius in inches")
    edge_location: str = Field(..., description="Location of filleted edge")


class ThreadFeature(FeatureBase):
    """Detected thread feature."""
    type: Literal["thread"] = "thread"
    designation: str = Field(..., description="Thread designation (e.g., '1/2-13 UNC')")
    length: Optional[float] = Field(None, gt=0.0, description="Thread length in inches")
    kind: str = Field(..., pattern=r"^(internal|external)$", description="Internal or external thread")
    notes: Optional[str] = Field(None, description="Additional thread notes")


class FeatureMeta(BaseModel):
    """Metadata for feature detection."""
    model_version: str = Field(..., description="Version of the detection model")
    detector_version: str = Field(..., description="Version of the detector software")
    timestamp_utc: str = Field(..., description="Timestamp when features were detected")
    warnings: List[str] = Field(default_factory=list, description="Detection warnings")

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FeatureMeta":
        """Create from dictionary (for JSON deserialization)."""
        return cls.model_validate(data)


class DetectedFeatures(BaseModel):
    """Container for all detected geometric features."""
    holes: List[HoleFeature] = Field(default_factory=list, description="Detected holes")
    slots: List[SlotFeature] = Field(default_factory=list, description="Detected slots")
    chamfers: List[ChamferFeature] = Field(default_factory=list, description="Detected chamfers")
    fillets: List[FilletFeature] = Field(default_factory=list, description="Detected fillets")
    threads: List[ThreadFeature] = Field(default_factory=list, description="Detected threads")
    meta: FeatureMeta = Field(..., description="Detection metadata")

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DetectedFeatures":
        """Create from dictionary (for JSON deserialization)."""
        return cls.model_validate(data)

    def is_empty(self) -> bool:
        """Check if no features were detected."""
        return (
            len(self.holes) == 0
            and len(self.slots) == 0
            and len(self.chamfers) == 0
            and len(self.fillets) == 0
            and len(self.threads) == 0
        )


# Union type for any feature
AnyFeature = Union[HoleFeature, SlotFeature, ChamferFeature, FilletFeature, ThreadFeature]


def create_feature_meta(model_version: str = "1.0.0", detector_version: str = "1.0.0", warnings: Optional[List[str]] = None) -> FeatureMeta:
    """Create feature metadata with current timestamp."""
    return FeatureMeta(
        model_version=model_version,
        detector_version=detector_version,
        timestamp_utc=datetime.now().isoformat() + "Z",
        warnings=warnings or []
    )