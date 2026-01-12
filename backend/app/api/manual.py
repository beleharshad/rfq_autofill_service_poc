"""Manual dimension entry endpoints for Assisted Manual mode."""

import json
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional
from app.services.job_service import JobService
from app.services.manual_stack_service import ManualStackService
from app.storage.file_storage import FileStorage
from app.utils.outputs_helper import build_outputs_info

router = APIRouter()

job_service = JobService()
file_storage = FileStorage()
manual_stack_service = ManualStackService()


class SegmentInput(BaseModel):
    """Input model for a single segment."""
    z_start: float = Field(..., description="Start Z coordinate")
    z_end: float = Field(..., description="End Z coordinate")
    od_diameter: float = Field(..., description="Outer diameter")
    id_diameter: float = Field(default=0.0, description="Inner diameter (0 if no bore)")


class TurnedStackRequest(BaseModel):
    """Request model for turned stack input."""
    units: str = Field(default="in", description="Units (in, mm, etc.)")
    segments: List[SegmentInput] = Field(..., description="List of segments")
    notes: Optional[str] = Field(None, description="Optional notes")


class TurnedStackResponse(BaseModel):
    """Response model for turned stack processing."""
    job_id: str
    status: str
    summary: dict = Field(default_factory=dict)
    totals: dict = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    outputs: List[str] = Field(default_factory=list)
    outputs_info: Optional[dict] = Field(None, description="Detailed information about output files")


@router.post("/jobs/{job_id}/manual/turned_stack", response_model=TurnedStackResponse)
async def process_turned_stack(job_id: str, request: TurnedStackRequest):
    """Process turned stack input and generate part summary.
    
    Validates:
    - z ranges contiguous & increasing
    - od > id
    - wall thickness >= minimum threshold
    
    Generates:
    - turned_stack.json
    - part_summary.json
    
    Returns summary + totals + warnings.
    """
    # Verify job exists
    try:
        job = job_service.get_job(job_id)
    except HTTPException:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Process stack
    result = manual_stack_service.process_turned_stack(
        job_id,
        request.units,
        [seg.dict() for seg in request.segments],
        request.notes
    )
    
    # Add outputs info
    result["outputs_info"] = build_outputs_info(job_id).dict()
    
    return TurnedStackResponse(**result)


class GenerateStepResponse(BaseModel):
    """Response model for STEP generation."""
    job_id: str
    status: str
    outputs: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    error: Optional[str] = None
    outputs_info: Optional[dict] = Field(None, description="Detailed information about output files")


@router.post("/jobs/{job_id}/manual/generate_step", response_model=GenerateStepResponse)
async def generate_step(job_id: str):
    """Generate STEP file from existing turned stack.
    
    Converts stack segments to Profile2D, builds solid, exports STEP,
    and regenerates part_summary.json with feature counts.
    
    Generates:
    - model.step
    - part_summary.json (regenerated with feature counts)
    - model.glb (if converter available)
    """
    # Verify job exists
    try:
        job = job_service.get_job(job_id)
    except HTTPException:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Generate STEP
    result = manual_stack_service.generate_step_from_stack(job_id)
    
    # Add outputs info
    result["outputs_info"] = build_outputs_info(job_id).dict()
    
    return GenerateStepResponse(**result)

