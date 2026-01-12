"""Profile input models."""

from typing import List
from pydantic import BaseModel, Field


class SegmentInput(BaseModel):
    """Input model for a single segment."""
    z_start: float = Field(..., description="Start Z coordinate")
    z_end: float = Field(..., description="End Z coordinate")
    od_diameter: float = Field(..., description="Outer diameter")
    id_diameter: float = Field(default=0.0, description="Inner diameter (0 if no bore)")


class StackInputRequest(BaseModel):
    """Request model for stack input."""
    units: str = Field(default="in", description="Units (in, mm, etc.)")
    segments: List[SegmentInput] = Field(..., description="List of segments")


class StackInputResponse(BaseModel):
    """Response model for stack input."""
    job_id: str
    units: str
    segments: List[SegmentInput]
    saved: bool

