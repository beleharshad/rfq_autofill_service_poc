"""STEP generation endpoints."""

from fastapi import APIRouter, HTTPException
from app.services.step_from_stack_service import StepFromStackService
from app.services.job_service import JobService
from app.utils.outputs_helper import build_outputs_info

router = APIRouter()

step_from_stack_service = StepFromStackService()
job_service = JobService()


@router.post("/jobs/{job_id}/generate_step_from_stack")
async def generate_step_from_stack(job_id: str):
    """Generate STEP file from inferred stack.
    
    This endpoint:
    - Loads job's outputs/inferred_stack.json
    - Converts stack segments to Profile2D closed loop
    - Builds OCC solid using RevolvedSolidBuilder
    - Exports outputs/model.step
    
    Args:
        job_id: Job identifier
        
    Returns:
        Dictionary with:
            - status: "OK" | "FAILED" | "UNAVAILABLE"
            - output_step_path: "outputs/model.step" (if OK)
            - message: Human-readable message
            - debug: Optional details dictionary
    """
    import logging
    logger = logging.getLogger(__name__)
    
    logger.info(f"[API] generate_step_from_stack called for job_id: {job_id}")
    
    # Verify job exists
    try:
        job = job_service.get_job(job_id)
        logger.info(f"[API] Job found: {job_id}")
    except HTTPException as e:
        logger.error(f"[API] Job not found: {job_id}, error: {e}")
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Generate STEP from inferred stack
    logger.info(f"[API] Calling step_from_stack_service.generate_step_from_inferred_stack...")
    result = step_from_stack_service.generate_step_from_inferred_stack(job_id)
    logger.info(f"[API] STEP generation result status: {result.get('status')}")
    
    # If status is FAILED, return 500 with detailed error
    if result.get("status") == "FAILED":
        logger.error(f"[API] STEP generation failed: {result.get('message')}")
        logger.error(f"[API] Debug info: {result.get('debug', {})}")
        raise HTTPException(
            status_code=500,
            detail={
                "status": "FAILED",
                "message": result.get("message", "STEP generation failed"),
                "debug": result.get("debug", {})
            }
        )
    
    # Add outputs info
    result["outputs_info"] = build_outputs_info(job_id).dict()
    
    logger.info(f"[API] STEP generation completed successfully for job: {job_id}")
    
    # Return result with status in body (always 200 OK for OK/UNAVAILABLE)
    # Frontend will handle UNAVAILABLE and FAILED statuses
    return result

