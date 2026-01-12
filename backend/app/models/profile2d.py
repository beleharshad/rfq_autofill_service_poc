"""Profile2D input models."""

from typing import List, Optional, Literal
from pydantic import BaseModel, Field
from app.models.outputs import OutputsInfo


class Point2DInput(BaseModel):
    """2D point input."""
    x: float = Field(..., description="X coordinate (radius)")
    y: float = Field(..., description="Y coordinate (axial position)")


class LineSegmentInput(BaseModel):
    """Line segment primitive input."""
    type: Literal["line"] = "line"
    start: Point2DInput = Field(..., description="Start point")
    end: Point2DInput = Field(..., description="End point")


class ArcSegmentInput(BaseModel):
    """Arc segment primitive input (for future use)."""
    type: Literal["arc"] = "arc"
    start: Point2DInput = Field(..., description="Start point")
    end: Point2DInput = Field(..., description="End point")
    center: Point2DInput = Field(..., description="Center point")
    radius: float = Field(..., description="Arc radius")
    clockwise: bool = Field(default=False, description="Arc direction (clockwise if True)")


class Profile2DRequest(BaseModel):
    """Request model for Profile2D input."""
    primitives: List[LineSegmentInput] = Field(..., description="List of line segment primitives")
    axis_point: Point2DInput = Field(
        default=Point2DInput(x=0.0, y=0.0),
        description="Revolution axis point (default: origin)"
    )


class Profile2DResponse(BaseModel):
    """Response model for Profile2D processing."""
    job_id: str
    status: str
    mode: str = Field(..., description="Job processing mode: assisted_manual or auto_convert")
    outputs: List[str] = Field(..., description="List of output files (step, json, debug artifacts)")
    outputs_info: Optional[OutputsInfo] = Field(None, description="Detailed information about output files")
    warnings: List[str] = Field(default_factory=list, description="Non-fatal warnings")
    validation_errors: List[str] = Field(default_factory=list, description="Validation errors")
    confidence: Optional[float] = Field(None, description="Confidence score [0.0, 1.0] for auto_convert mode")

