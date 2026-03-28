"""3D preview endpoint — generates a GLB from the job's inferred stack via OCC B-Rep.

Pipeline:
  inferred_stack.json
      → StackToProfileService  → Profile2D
      → RevolvedSolidBuilder   → OCC BRep solid
      → STEPControl_Writer     → outputs/model.step  (cached)
      → StepToGlbConverter     → outputs/model.glb   (cached)
      → FileResponse           → frontend Three.js GLTFLoader

GET /api/v1/jobs/{job_id}/3d-preview
    Returns the GLB binary (generates on demand, uses cache if up-to-date).
    Responds 202 {"status":"generating"} while building if async is needed.
    Responds 503 {"status":"unavailable", "reason":...} when OCC not installed.

GET /api/v1/jobs/{job_id}/3d-preview/status
    Returns {"available": bool, "glb_ready": bool, "occ_available": bool}
"""

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.services.step_from_stack_service import StepFromStackService
from app.services.step_to_glb_converter import StepToGlbConverter
from app.services.job_service import JobService
from app.storage.file_storage import FileStorage

logger = logging.getLogger(__name__)

router = APIRouter()

_job_service = JobService()
_file_storage = FileStorage()
_step_service = StepFromStackService()
_glb_converter = StepToGlbConverter()


def _outputs(job_id: str) -> Path:
    return _file_storage.get_outputs_path(job_id)


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

@router.get("/jobs/{job_id}/3d-preview/status")
async def get_3d_preview_status(job_id: str):
    """Check whether a GLB preview is ready / can be generated."""
    out = _outputs(job_id)
    # Accept if either the job is registered OR its output directory exists on disk
    if not out.exists():
        try:
            _job_service.get_job(job_id)
        except HTTPException:
            raise HTTPException(status_code=404, detail="Job not found")

    stack_ready = (out / "inferred_stack.json").exists()
    step_ready  = (out / "model.step").exists()
    glb_ready   = (out / "model.glb").exists()

    # Determine OCC availability (import check only — no heavy init)
    occ_available = False
    try:
        from app.utils.occ_available import occ_available as _occ_avail
        occ_available = _occ_avail()
    except Exception:
        pass

    return {
        "occ_available":  occ_available,
        "stack_ready":    stack_ready,
        "step_ready":     step_ready,
        "glb_ready":      glb_ready,
        "can_generate":   occ_available and stack_ready and _glb_converter.available,
    }


# ---------------------------------------------------------------------------
# Preview GLB
# ---------------------------------------------------------------------------

@router.get("/jobs/{job_id}/3d-preview")
async def get_3d_preview(job_id: str, force: bool = False, bore_diameter: float = 0.0):
    """Return the GLB preview for a job, generating it on demand if needed.

    Query params:
        force (bool): Regenerate even if a cached GLB exists.
        bore_diameter (float): Inner diameter in inches. When >0 all segment
            id_diameter values are overridden before STEP generation so the
            exported solid is hollow. Passing a non-zero value automatically
            forces cache invalidation for this job.

    Returns:
        The model.glb binary as application/octet-stream.
        503 when OCC or trimesh are not available.
        404 when no inferred_stack.json exists yet.
    """
    out = _outputs(job_id)
    if not out.exists():
        try:
            _job_service.get_job(job_id)
        except HTTPException:
            raise HTTPException(status_code=404, detail="Job not found")

    step_path = out / "model.step"
    glb_path  = out / "model.glb"

    # Fast path: GLB already cached and not forced
    # bore_diameter changes invalidate the cache so the solid is rebuilt with the correct bore.
    force_rebuild = force or (bore_diameter > 0.001)
    if not force_rebuild and glb_path.exists() and step_path.exists():
        step_mt = step_path.stat().st_mtime
        glb_mt  = glb_path.stat().st_mtime
        if glb_mt >= step_mt:
            logger.info(f"[3d-preview] Serving cached GLB for {job_id}")
            return FileResponse(
                str(glb_path),
                media_type="model/gltf-binary",
                filename="model.glb",
            )

    # When bore override is active, delete stale step/glb so we start fresh.
    if bore_diameter > 0.001:
        for stale in (step_path, glb_path):
            if stale.exists():
                logger.info(f"[3d-preview] Removing stale cache file: {stale.name}")
                stale.unlink(missing_ok=True)

    # ── Step 1: generate STEP via OCC ────────────────────────────────────
    stack_path = out / "inferred_stack.json"
    if not stack_path.exists():
        raise HTTPException(
            status_code=404,
            detail="inferred_stack.json not found. Run auto-detect first.",
        )

    logger.info(f"[3d-preview] Generating STEP for {job_id} (bore_diameter={bore_diameter:.4f}) …")
    step_result = _step_service.generate_step_from_inferred_stack(job_id, bore_diameter=bore_diameter)

    if step_result.get("status") == "UNAVAILABLE":
        raise HTTPException(
            status_code=503,
            detail={
                "status": "unavailable",
                "reason": "OCC not installed on this server. "
                          "Install python-occ-core to enable B-Rep preview.",
            },
        )

    if step_result.get("status") != "OK":
        raise HTTPException(
            status_code=500,
            detail={
                "status": "failed",
                "reason": step_result.get("message", "STEP generation failed"),
                "debug":  step_result.get("debug", {}),
            },
        )

    if not step_path.exists():
        raise HTTPException(status_code=500, detail="STEP file missing after generation")

    # ── Step 2: convert STEP → GLB ───────────────────────────────────────
    logger.info(f"[3d-preview] Converting STEP → GLB for {job_id} …")
    ok, err = _glb_converter.convert_step_to_glb(step_path, glb_path, check_cache=not force_rebuild)

    if not ok:
        raise HTTPException(
            status_code=503,
            detail={
                "status": "unavailable",
                "reason": f"STEP→GLB conversion failed: {err}. "
                          "Install 'trimesh' (pip install trimesh) to enable GLB export.",
            },
        )

    logger.info(f"[3d-preview] GLB ready for {job_id}")
    return FileResponse(
        str(glb_path),
        media_type="model/gltf-binary",
        filename="model.glb",
    )
