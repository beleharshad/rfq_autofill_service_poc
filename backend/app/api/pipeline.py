"""Pipeline execution endpoints."""

from fastapi import APIRouter, HTTPException
from app.services.pipeline_service import PipelineService
from app.services.job_service import JobService
from app.models.job import JobStatus

router = APIRouter()

pipeline_service = PipelineService()
job_service = JobService()


@router.post("/jobs/{job_id}/run")
async def run_analysis(job_id: str):
    """Run analysis pipeline for a job.
    
    Reads stack_input.json, builds TurnedPartStack, computes metrics,
    and generates part_summary.json.
    
    Returns:
        Status and list of output files
    """
    # Verify job exists
    try:
        job = job_service.get_job(job_id)
    except HTTPException:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Update status to processing
    job_service.job_storage.update_job_status(job_id, JobStatus.PROCESSING)
    
    try:
        result = pipeline_service.run_analysis(job_id)
        return result
    except FileNotFoundError as e:
        job_service.job_storage.update_job_status(job_id, JobStatus.FAILED)
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        job_service.job_storage.update_job_status(job_id, JobStatus.FAILED)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        job_service.job_storage.update_job_status(job_id, JobStatus.FAILED)
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")


@router.get("/jobs/{job_id}/status")
async def get_analysis_status(job_id: str):
    """Get analysis status for a job.
    
    Returns:
        Job status and progress information
    """
    try:
        job = job_service.get_job(job_id)
        return {
            "job_id": job_id,
            "status": job.status,
            "progress": 100 if job.status == JobStatus.COMPLETED else 0,
            "error": None
        }
    except HTTPException:
        raise HTTPException(status_code=404, detail="Job not found")








