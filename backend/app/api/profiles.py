"""Profile management endpoints."""

import json
from pathlib import Path
from fastapi import APIRouter, HTTPException
from app.models.profile import StackInputRequest, StackInputResponse
from app.services.job_service import JobService
from app.storage.file_storage import FileStorage

router = APIRouter()

job_service = JobService()
file_storage = FileStorage()


@router.post("/jobs/{job_id}/stack-input", response_model=StackInputResponse)
async def save_stack_input(job_id: str, request: StackInputRequest):
    """Save stack input for a job.
    
    Stores the segment stack input as JSON in outputs/stack_input.json
    """
    # Verify job exists
    job = job_service.get_job(job_id)
    
    # Validate segments
    if not request.segments:
        raise HTTPException(status_code=400, detail="At least one segment is required")
    
    # Validate segment data
    for i, segment in enumerate(request.segments):
        if segment.z_start >= segment.z_end:
            raise HTTPException(
                status_code=400,
                detail=f"Segment {i}: z_start ({segment.z_start}) must be less than z_end ({segment.z_end})"
            )
        if segment.od_diameter <= 0:
            raise HTTPException(
                status_code=400,
                detail=f"Segment {i}: od_diameter must be greater than 0"
            )
        if segment.id_diameter < 0:
            raise HTTPException(
                status_code=400,
                detail=f"Segment {i}: id_diameter cannot be negative"
            )
        if segment.id_diameter > segment.od_diameter:
            raise HTTPException(
                status_code=400,
                detail=f"Segment {i}: id_diameter ({segment.id_diameter}) cannot be greater than od_diameter ({segment.od_diameter})"
            )
    
    # Save to outputs directory
    outputs_path = file_storage.get_outputs_path(job_id)
    outputs_path.mkdir(parents=True, exist_ok=True)
    
    output_file = outputs_path / "stack_input.json"
    
    # Prepare data for saving
    data = {
        "units": request.units,
        "segments": [seg.dict() for seg in request.segments]
    }
    
    # Write JSON file
    with open(output_file, 'w') as f:
        json.dump(data, f, indent=2)
    
    return StackInputResponse(
        job_id=job_id,
        units=request.units,
        segments=request.segments,
        saved=True
    )


@router.get("/jobs/{job_id}/stack-input", response_model=StackInputResponse)
async def get_stack_input(job_id: str):
    """Get saved stack input for a job.
    
    Returns empty response if stack input doesn't exist yet.
    """
    # Verify job exists
    job_service.get_job(job_id)
    
    # Check if stack input exists
    outputs_path = file_storage.get_outputs_path(job_id)
    input_file = outputs_path / "stack_input.json"
    
    if not input_file.exists():
        # Return empty response instead of 404
        from app.models.profile import SegmentInput
        return StackInputResponse(
            job_id=job_id,
            units="in",
            segments=[],
            saved=False
        )
    
    # Read JSON file
    with open(input_file, 'r') as f:
        data = json.load(f)
    
    from app.models.profile import SegmentInput
    
    return StackInputResponse(
        job_id=job_id,
        units=data.get("units", "in"),
        segments=[SegmentInput(**seg) for seg in data.get("segments", [])],
        saved=True
    )

