"""Additive part contract endpoint; no CAD or LLM dependencies."""

from fastapi import APIRouter, HTTPException, BackgroundTasks
import hashlib
import os
from fastapi.responses import FileResponse

from app.models.part_spec import PartSpec
from app.security import validate_job_id
from app.services.part_spec_service import PartSpecService
from app.storage.job_storage import JobStorage
from app.storage.file_storage import FileStorage
from app.storage.document_registry import DocumentRegistry
from app.models.document_registry import AssociationRequest
from app.models.accepted_part import AcceptedPartRequest
from app.storage.accepted_parts import AcceptedParts
from app.services.generic_build_runner import run_build
from app.services.source_analysis import analyze_source

router = APIRouter()


@router.post("/{job_id}/documents/{document_id}/analyze")
def analyze_registered_source(job_id: str, document_id: str):
    _, registry = _registry(job_id)
    try:
        return analyze_source(registry, document_id)
    except HTTPException:
        raise
    except (ImportError, RuntimeError) as exc:
        raise HTTPException(503, f"PDF extraction is unavailable: {exc}")


@router.get("/{job_id}/accepted-parts")
def list_accepted_parts(job_id: str):
    files, _ = _registry(job_id)
    return AcceptedParts(files.get_job_path(job_id)).list()


@router.post("/{job_id}/accepted-parts", status_code=201)
def accept_part(job_id: str, request: AcceptedPartRequest):
    files, _ = _registry(job_id)
    return AcceptedParts(files.get_job_path(job_id)).save(request)


@router.post("/{job_id}/accepted-parts/{spec_id}/build", status_code=202)
def enqueue_build(job_id: str, spec_id: str, background: BackgroundTasks):
    files, _ = _registry(job_id)
    job_path = files.get_job_path(job_id)
    task = AcceptedParts(job_path).enqueue(spec_id)
    if os.environ.get("GENERIC_GEOMETRY_QUEUE_ONLY", "").lower() != "true":
        background.add_task(run_build, job_path, task["build_id"])
    return task


@router.get("/{job_id}/accepted-parts/{spec_id}/builds")
def list_geometry_builds(job_id: str, spec_id: str):
    files, _ = _registry(job_id)
    return AcceptedParts(files.get_job_path(job_id)).builds_for(spec_id)


@router.get("/{job_id}/geometry-builds/{build_id}")
def build_status(job_id: str, build_id: str):
    files, _ = _registry(job_id)
    return AcceptedParts(files.get_job_path(job_id)).build(build_id)


@router.delete("/{job_id}/geometry-builds/{build_id}")
def cancel_build(job_id: str, build_id: str):
    files, _ = _registry(job_id)
    store = AcceptedParts(files.get_job_path(job_id))
    store.cancel(build_id)
    return store.build(build_id)


@router.get("/{job_id}/geometry-builds/{build_id}/artifacts/{filename}")
def accepted_artifact(job_id: str, build_id: str, filename: str):
    types = {"model.step": "application/step", "model.glb": "model/gltf-binary",
             "rfq_review.xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
             "manifest.json": "application/json"}
    if filename not in types:
        raise HTTPException(404, "Artifact not found")
    files, _ = _registry(job_id)
    job_path = files.get_job_path(job_id)
    store = AcceptedParts(job_path)
    build = store.build(build_id)
    store.get(build["spec_id"])
    if build["status"] != "complete":
        raise HTTPException(409, "Build is not complete")
    path = job_path / "outputs" / "accepted" / build["build_id"] / filename
    if not path.is_file():
        raise HTTPException(409, "Build artifact is missing; rebuild the specification")
    expected = (build["manifest"] or {}).get("artifacts", {}).get(filename)
    if expected and hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise HTTPException(409, "Artifact integrity check failed; rebuild the specification")
    return FileResponse(path, filename=filename, media_type=types[filename], headers={"Cache-Control": "no-store"})


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
