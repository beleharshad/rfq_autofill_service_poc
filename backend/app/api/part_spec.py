"""Additive part contract endpoint; no CAD or LLM dependencies."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.models.part_spec import PartSpec
from app.security import validate_job_id
from app.services.part_spec_service import PartSpecService
from app.storage.job_storage import JobStorage
from app.storage.file_storage import FileStorage
from app.storage.document_registry import DocumentRegistry
from app.models.document_registry import AssociationRequest

router = APIRouter()


def _registry(job_id: str):
    validate_job_id(job_id)
    if JobStorage().get_job(job_id) is None:
        raise HTTPException(404, "Job not found")
    files = FileStorage()
    return files, DocumentRegistry(files.get_job_path(job_id))


@router.get("/{job_id}/documents")
def get_documents(job_id: str):
    return _registry(job_id)[1].read()


@router.post("/{job_id}/documents/register")
def register_existing_documents(job_id: str):
    """Explicit, idempotent migration for inputs saved before registry support."""
    files, registry = _registry(job_id)
    for relative_path in files.list_input_files(job_id):
        if relative_path.rsplit("/", 1)[-1].startswith("~$"):
            continue
        path, _, _ = files.get_file_info(job_id, relative_path)
        registry.register(path, relative_path)
    return registry.read()


@router.put("/{job_id}/documents/{document_id}/association")
def associate_document(job_id: str, document_id: str, request: AssociationRequest):
    return _registry(job_id)[1].associate(document_id, request)


@router.get("/{job_id}/documents/{document_id}/history")
def association_history(job_id: str, document_id: str):
    return _registry(job_id)[1].history(document_id)


@router.get("/{job_id}/documents/{document_id}/original")
def download_original(job_id: str, document_id: str):
    registry = _registry(job_id)[1]
    document = next((d for d in registry.read()["documents"] if d["document_id"] == document_id), None)
    if document is None:
        raise HTTPException(404, "Document not registered")
    path = registry.root / "blobs" / document["sha256"]
    if not path.is_file():
        raise HTTPException(409, "Retained source is missing")
    return FileResponse(path, filename=document["path"].rsplit("/", 1)[-1], media_type="application/octet-stream")


@router.get("/{job_id}/part-spec", response_model=PartSpec)
def get_part_spec(job_id: str):
    validate_job_id(job_id)
    if JobStorage().get_job(job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return PartSpecService().get_snapshot(job_id)
