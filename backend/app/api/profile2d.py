"""Profile2D processing endpoints."""

from fastapi import APIRouter, HTTPException
from app.models.profile2d import Profile2DRequest, Profile2DResponse
from app.services.profile2d_service import Profile2DService
from app.services.job_service import JobService
from app.models.job import JobStatus
from app.utils.outputs_helper import build_outputs_info

router = APIRouter()

profile2d_service = Profile2DService()
job_service = JobService()


@router.post("/jobs/{job_id}/profile2d", response_model=Profile2DResponse)
async def process_profile2d(job_id: str, request: Profile2DRequest):
    """Process Profile2D input and generate solid + analysis.
    
    Builds TopoDS_Solid, exports STEP, runs FeatureExtractor,
    and generates part_summary.json.
    """
    # Verify job exists
    try:
        job = job_service.get_job(job_id)
    except HTTPException:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Update status to processing
    job_service.job_storage.update_job_status(job_id, JobStatus.PROCESSING)
    
    try:
        # Convert request to dict format
        primitives = [prim.dict() for prim in request.primitives]
        axis_point = request.axis_point.dict()
        
        result = profile2d_service.process_profile2d(job_id, primitives, axis_point)
        
        if result["status"] == "FAILED":
            job_service.job_storage.update_job_status(job_id, JobStatus.FAILED)
        
        # Add outputs info
        result["outputs_info"] = build_outputs_info(job_id).dict()
        
        return Profile2DResponse(**result)
    except Exception as e:
        job_service.job_storage.update_job_status(job_id, JobStatus.FAILED)
        raise HTTPException(status_code=500, detail=f"Profile2D processing failed: {str(e)}")

