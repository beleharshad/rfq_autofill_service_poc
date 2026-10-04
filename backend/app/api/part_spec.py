"""Additive part contract endpoint; no CAD or LLM dependencies."""

from fastapi import APIRouter, HTTPException

from app.models.part_spec import PartSpec
from app.security import validate_job_id
from app.services.part_spec_service import PartSpecService
from app.storage.job_storage import JobStorage

router = APIRouter()


@router.get("/{job_id}/part-spec", response_model=PartSpec)
def get_part_spec(job_id: str):
    validate_job_id(job_id)
    if JobStorage().get_job(job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return PartSpecService().get_snapshot(job_id)
