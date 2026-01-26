"""Shared schemas and types for feature detection."""

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
    size: float = Field(..., gt=0.0, description="Chamfer size in inches")
    angle: float = Field(..., gt=0.0, le=180.0, description="Chamfer angle in degrees")
    edge_location: str = Field(..., description="Location on part (e.g., 'corner', 'edge')")


class FilletFeature(FeatureBase):
    """Detected fillet feature."""
    type: Literal["fillet"] = "fillet"
    radius: float = Field(..., gt=0.0, description="Fillet radius in inches")
    edge_location: str = Field(..., description="Location on part (e.g., 'internal corner', 'external edge')")


class ThreadFeature(FeatureBase):
    """Detected thread feature."""
    type: Literal["thread"] = "thread"
    designation: str = Field(..., description="Thread designation (e.g., '1/4-20 UNC', 'M6x1')")
    length: Optional[float] = Field(None, gt=0.0, description="Thread length in inches")
    kind: str = Field(..., pattern=r"^(internal|external)$", description="Thread type: internal or external")


# Type alias for any feature
AnyFeature = Union[HoleFeature, SlotFeature, ChamferFeature, FilletFeature, ThreadFeature]


class FeatureMeta(BaseModel):
    """Metadata for detected features."""
    model_version: str = Field(..., description="Version of the feature detection model")
    detector_version: str = Field(..., description="Version of the detector implementation")
    timestamp_utc: str = Field(..., description="Timestamp when detection was performed (ISO format)")
    warnings: List[str] = Field(default_factory=list, description="Warnings encountered during detection")


class DetectedFeatures(BaseModel):
    """Container for all detected features."""
    holes: List[HoleFeature] = Field(default_factory=list, description="Detected holes")
    slots: List[SlotFeature] = Field(default_factory=list, description="Detected slots")
    chamfers: List[ChamferFeature] = Field(default_factory=list, description="Detected chamfers")
    fillets: List[FilletFeature] = Field(default_factory=list, description="Detected fillets")
    threads: List[ThreadFeature] = Field(default_factory=list, description="Detected threads")
    meta: FeatureMeta = Field(..., description="Detection metadata")


def create_feature_meta(
    detector_version: str = "1.0.0",
    model_version: str = "v1",
    warnings: Optional[List[str]] = None,
) -> FeatureMeta:
    """Create feature metadata with current timestamp."""
    return FeatureMeta(
        model_version=model_version,
        detector_version=detector_version,
        timestamp_utc=datetime.now().isoformat() + "Z",
        warnings=list(warnings) if warnings else [],
    )


# Detection result types
class DetectionResult(BaseModel):
    """Result of feature detection."""
    success: bool = Field(..., description="Whether detection was successful")
    error: Optional[str] = Field(None, description="Error message if detection failed")
    features: Optional[DetectedFeatures] = Field(None, description="Detected features")
    page_count: Optional[int] = Field(None, description="Number of pages processed")
    total_candidates: Optional[int] = Field(None, description="Total candidate features found")


class MergeResult(BaseModel):
    """Result of feature merging."""
    success: bool = Field(..., description="Whether merging was successful")
    error: Optional[str] = Field(None, description="Error message if merging failed")
    features_added: Optional[int] = Field(None, description="Number of features added to part summary")