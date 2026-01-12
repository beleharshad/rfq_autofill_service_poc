"""RFQ endpoints."""

import json
from json import JSONDecodeError
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException

from app.models.rfq_autofill import RFQAutofillRequest, RFQAutofillResponse
from app.services.rfq_autofill_service import RFQAutofillService
from app.storage.file_storage import FileStorage

router = APIRouter(prefix="/api/v1/rfq", tags=["rfq"])


def load_part_summary(job_id: str) -> Dict[str, Any]:
    """Load part_summary.json from data/jobs/{job_id}/outputs with safe path handling."""
    if not job_id or not str(job_id).strip():
        raise HTTPException(status_code=400, detail="source.job_id is required to load part_summary.json")

    job_id = str(job_id).strip()
    if (".." in job_id) or ("/" in job_id) or ("\\" in job_id):
        raise HTTPException(status_code=400, detail="Invalid job_id")

    fs = FileStorage()
    job_path = fs.get_job_path(job_id)
    outputs_path = fs.get_outputs_path(job_id)
    summary_file = outputs_path / "part_summary.json"

    try:
        summary_file.resolve().relative_to(job_path.resolve())
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid outputs path")

    if not summary_file.exists() or not summary_file.is_file():
        raise HTTPException(status_code=404, detail="part_summary.json not found for job_id")

    try:
        with open(summary_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON in part_summary.json")
    except OSError:
        raise HTTPException(status_code=400, detail="Unable to read part_summary.json")

    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="part_summary.json must be a JSON object")

    return data


@router.post("/autofill", response_model=RFQAutofillResponse)
async def rfq_autofill(request: RFQAutofillRequest):
    """Auto-fill RFQ fields from part_summary + tolerances (v1)."""
    if not request.part_no or not request.part_no.strip():
        raise HTTPException(status_code=400, detail="part_no is required")

    part_no_original = request.part_no.strip()
    _part_no_key = part_no_original.lower()  # v1: internal comparisons should be case-insensitive

    part_summary: Optional[Dict[str, Any]] = None
    if request.source.part_summary is not None:
        if not isinstance(request.source.part_summary, dict):
            raise HTTPException(status_code=400, detail="source.part_summary must be a JSON object")
        part_summary = request.source.part_summary
    elif request.source.job_id is not None:
        part_summary = load_part_summary(request.source.job_id)
    else:
        raise HTTPException(status_code=400, detail="Either source.part_summary or source.job_id is required")

    service = RFQAutofillService()
    return service.autofill(
        part_no=part_no_original,
        part_summary_dict=part_summary,
        tolerances=request.tolerances.dict(),
        step_metrics=request.source.step_metrics,
    )


