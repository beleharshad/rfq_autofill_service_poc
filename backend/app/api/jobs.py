"""Job management endpoints."""

import json
from pathlib import Path
from typing import List, Optional
from datetime import datetime
from urllib.parse import urlparse, unquote
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse

from app.models.job import JobResponse, JobModeRequest
from app.services.job_service import JobService
from app.services.run_report_service import RunReportService
from app.storage.file_storage import FileStorage
from app.utils.outputs_helper import build_outputs_info
from app.security import validate_job_id

router = APIRouter()

job_service = JobService()
file_storage = FileStorage()
run_report_service = RunReportService()

# Allowed upload MIME types and their canonical extensions
_ALLOWED_MIME = {
    "application/pdf",
    "application/zip",
    "application/x-zip-compressed",
    "application/step",
    "application/x-step",
    "model/step",
    "chemical/x-step",
    "application/octet-stream",  # some browsers send this for .pdf/.zip
}
_ALLOWED_EXT = {".pdf", ".zip", ".step", ".stp"}
_MAX_FILE_BYTES = 50 * 1024 * 1024  # 50 MB per file


def _auto_convert_requires_source_file(mode: Optional[str]) -> bool:
    """Return True when *mode* requires at least one uploaded source file."""
    return (mode or "").strip() == "auto_convert"


def _validate_upload(file: UploadFile) -> None:
    """Raise 400/413 if *file* has a disallowed extension or exceeds the size limit."""
    name = (file.filename or "").lower()
    ext = "." + name.rsplit(".", 1)[-1] if "." in name else ""
    if ext not in _ALLOWED_EXT:
        raise HTTPException(
            status_code=400,
            detail=f"File type not allowed: '{file.filename}'. Only PDF, ZIP, STEP, and STP files are accepted.",
        )
    if file.size is not None and file.size > _MAX_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File '{file.filename}' exceeds the 50 MB upload limit.",
        )


def _validate_source_url(source_url: str) -> None:
    """Raise 400 if *source_url* is not a supported remote file URL."""
    parsed = urlparse((source_url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="Source URL must be a valid http:// or https:// URL.")

    filename = Path(unquote(parsed.path or "")).name
    ext = Path(filename).suffix.lower()
    if ext not in _ALLOWED_EXT:
        raise HTTPException(
            status_code=400,
            detail="Source URL must point to a PDF, ZIP, STEP, or STP file.",
        )


@router.post("", response_model=JobResponse, status_code=201)
async def create_job(
    name: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    mode: Optional[str] = Form(None),
    source_url: Optional[str] = Form(None),
    files: List[UploadFile] = File(default=[]),
):
    """Create a new job and upload files.

    Accepts multipart/form-data with:
    - files: Optional list of PDF or ZIP files (can be omitted)
    - name: Optional job name
    - description: Optional job description
    - mode: Optional job mode (assisted_manual or auto_convert)

    Files are optional - job can be created without files for later upload.
    Returns job with status CREATED and list of input files.
    """
    job_id: Optional[str] = None
    try:
        # Validate and sanitise text inputs
        if name:
            name = name[:200].strip()
        if description:
            description = description[:1000].strip()

        if source_url:
            source_url = source_url.strip()

        if _auto_convert_requires_source_file(mode) and not files and not source_url:
            raise HTTPException(
                status_code=400,
                detail="Auto Convert jobs require at least one PDF, ZIP, STEP, or STP file.",
            )

        if source_url:
            _validate_source_url(source_url)

        # Validate each uploaded file before creating the job
        for f in (files or []):
            _validate_upload(f)

        job_id = job_service.create_job(name, description, mode)

        if files and len(files) > 0:
            job = job_service.upload_files(job_id, files)
            if _auto_convert_requires_source_file(mode) and not job.input_files:
                job_service.delete_job(job_id)
                raise HTTPException(
                    status_code=400,
                    detail="Auto Convert job upload did not contain any accepted source files. Please upload a PDF, ZIP, STEP, or STP file and try again.",
                )
        else:
            job = job_service.get_job(job_id)

        if source_url:
            job = job_service.upload_remote_file(job_id, source_url)
            if _auto_convert_requires_source_file(mode) and not job.input_files:
                job_service.delete_job(job_id)
                raise HTTPException(
                    status_code=400,
                    detail="Remote URL upload did not produce any accepted source files.",
                )

        return job

    except HTTPException:
        raise

    except Exception as e:
        import traceback

        error_detail = f"Error creating job: {str(e)}\n{traceback.format_exc()}"
        print(f"ERROR: {error_detail}")
        raise HTTPException(status_code=500, detail=error_detail)


@router.get("", response_model=List[JobResponse])
async def list_jobs():
    """List all jobs."""
    return job_service.list_jobs()


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: str):
    """Get job details with run report summary and outputs info."""
    validate_job_id(job_id)
    job = job_service.get_job(job_id)

    report_summary = run_report_service.get_report_summary(job_id)
    job.run_report = report_summary.dict() if report_summary.has_report else None

    outputs_info = build_outputs_info(job_id)
    job.outputs_info = outputs_info

    return job


@router.get("/{job_id}/files")
async def list_job_files(job_id: str):
    """
    List ALL files for a job with download URLs.

    ✅ Fix: Do NOT rely on job.input_files / job.output_files, because those
    lists often don't include newly generated outputs in nested folders like:
    - outputs/pdf_pages/page_0.png
    - outputs/pdf_views/page_0_views.json
    """
    validate_job_id(job_id)
    _ = job_service.get_job(job_id)  # verify job exists

    files = []

    # Use storage scan (recursive) so frontend sees pdf_pages + pdf_views
    input_paths = file_storage.list_input_files(job_id)
    output_paths = file_storage.list_output_files(job_id)

    for rel_path in input_paths + output_paths:
        try:
            _, filename, size = file_storage.get_file_info(job_id, rel_path)
            files.append(
                {
                    "path": rel_path,
                    "name": filename,
                    "size": size,
                    "url": f"/api/v1/jobs/{job_id}/download?path={rel_path}",
                }
            )
        except HTTPException:
            continue

    return {"job_id": job_id, "files": files}


@router.get("/{job_id}/download")
async def download_file(job_id: str, path: str):
    """Download a file from a job.

    Safe download with path traversal protection.
    """
    validate_job_id(job_id)

    # Verify job exists
    try:
        _ = job_service.get_job(job_id)
    except HTTPException:
        raise HTTPException(status_code=404, detail="Job not found")

    # ---- Output whitelist rules ----
    # 1) Always allow inputs/*
    if path.startswith("inputs/"):
        pass

    # 2) Allow specific outputs and subfolders needed by UI + pipeline
    elif path.startswith("outputs/"):
        filename = path.split("/")[-1]

        # Allow rendered PDF pages (images)
        if path.startswith("outputs/pdf_pages/") and (
            filename.lower().endswith(".png")
            or filename.lower().endswith(".jpg")
            or filename.lower().endswith(".jpeg")
        ):
            pass

        # Allow detected view JSON
        elif path.startswith("outputs/pdf_views/") and filename.lower().endswith(".json"):
            pass

        # Allow known pipeline artifacts (extend as needed)
        else:
            allowed_output_files = {
                "part_summary.json",
                "model.step",
                "model.glb",
                "scale_report.json",
                "inferred_stack.json",
                "turned_stack.json",
                "run_report.json",
                "auto_detect_results.json",
                "selected_view.json",
                "step_approval.json",
                "llm_analysis.json",
            }

            if filename not in allowed_output_files:
                raise HTTPException(
                    status_code=403,
                    detail=f"File '{filename}' is not allowed for download.",
                )

    # 3) Disallow everything else
    else:
        raise HTTPException(status_code=400, detail="Invalid file path")

    # Get file info (includes traversal check + existence check)
    try:
        file_path, filename, _ = file_storage.get_file_info(job_id, path)
    except HTTPException as e:
        if e.status_code == 404:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"File not found: {path}. This file may not have been generated yet."
                ),
            )
        raise

    # Determine content type
    media_type = "application/octet-stream"
    lower = filename.lower()
    if lower.endswith(".step") or lower.endswith(".stp"):
        media_type = "application/step"
    elif lower.endswith(".json"):
        media_type = "application/json"
    elif lower.endswith(".glb"):
        media_type = "model/gltf-binary"
    elif lower.endswith(".pdf"):
        media_type = "application/pdf"
    elif lower.endswith(".png"):
        media_type = "image/png"
    elif lower.endswith(".jpg") or lower.endswith(".jpeg"):
        media_type = "image/jpeg"

    return FileResponse(
        path=file_path,
        filename=filename,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.put("/{job_id}/mode", response_model=JobResponse)
async def set_job_mode(job_id: str, request: JobModeRequest):
    """Set job processing mode."""
    validate_job_id(job_id)
    job_service.set_job_mode(job_id, request.mode)
    return job_service.get_job(job_id)


@router.put("/{job_id}/selected-view")
async def set_selected_view(job_id: str, request: dict):
    """Save selected view to job state."""
    validate_job_id(job_id)
    _ = job_service.get_job(job_id)  # verify job exists

    outputs_path = file_storage.get_outputs_path(job_id)
    outputs_path.mkdir(parents=True, exist_ok=True)

    view_file = outputs_path / "selected_view.json"
    with open(view_file, "w") as f:
        json.dump(
            {
                "page": request.get("page"),
                "view_index": request.get("view_index"),
                "timestamp": datetime.now().isoformat(),
            },
            f,
            indent=2,
        )

    return {
        "message": "Selected view saved",
        "page": request.get("page"),
        "view_index": request.get("view_index"),
    }


@router.delete("/{job_id}")
async def delete_job(job_id: str):
    """Delete a job and all its files."""
    validate_job_id(job_id)
    job_service.delete_job(job_id)
    return {"message": "Job deleted successfully"}
