"""PDF processing endpoints for Assisted Manual Mode."""

import json
from typing import List
from fastapi import APIRouter, UploadFile, File, HTTPException, Request
from pathlib import Path
import tempfile
from app.services.job_service import JobService
from app.services.pdf_service import PDFService
from app.services.auto_detect_service import AutoDetectService
from app.services.stack_inference_service import StackInferenceService
from app.services.auto_step_service import AutoStepService
from app.utils.outputs_helper import build_outputs_info

router = APIRouter()

job_service = JobService()
pdf_service = PDFService()
auto_detect_service = AutoDetectService()
stack_inference_service = StackInferenceService()
auto_step_service = AutoStepService()


@router.post("/jobs/{job_id}/pdf/upload")
async def upload_pdf(job_id: str, file: UploadFile = File(...)):
    """Upload PDF and render page images at 300 DPI.
    
    Saves PDF to inputs/source.pdf and renders pages to outputs/pdf_pages/.
    
    Args:
        job_id: Job identifier
        file: PDF file to upload
        
    Returns:
        Dictionary with page count and rendered image paths
    """
    # Verify job exists
    try:
        job = job_service.get_job(job_id)
    except HTTPException:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Validate file type
    if not file.filename or not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="File must be a PDF")
    
    # Save uploaded file temporarily
    with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_file:
        tmp_path = Path(tmp_file.name)
        content = await file.read()
        tmp_path.write_bytes(content)
    
    try:
        # Upload and render PDF
        result = pdf_service.upload_and_render_pdf(job_id, tmp_path)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF processing failed: {str(e)}")
    finally:
        # Clean up temp file
        if tmp_path.exists():
            tmp_path.unlink()


@router.post("/jobs/{job_id}/pdf/detect_views")
async def detect_views(job_id: str):
    """Detect candidate view rectangles on rendered PDF pages.
    
    Uses OpenCV to detect rectangles on each page image.
    Stores results in outputs/pdf_views/page_{n}_views.json.
    
    Args:
        job_id: Job identifier
        
    Returns:
        List of detected views per page with bounding boxes
    """
    # Verify job exists
    try:
        job = job_service.get_job(job_id)
    except HTTPException:
        raise HTTPException(status_code=404, detail="Job not found")
    
    try:
        # Detect views
        views = pdf_service.detect_views(job_id)
        return {
            "job_id": job_id,
            "pages": views,
            "total_views": sum(len(page["views"]) for page in views)
        }
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"View detection failed: {str(e)}")


@router.post("/jobs/{job_id}/pdf/auto_detect_turned_view")
async def auto_detect_turned_view(job_id: str):
    """Auto-detect turned part view from PDF pages (Phase A: detection only).
    
    Pipeline:
    - Uses existing rendered page images and detected views
    - For each view crop:
      - Detect long axis candidate via HoughLinesP
      - Compute axis_conf
      - Compute symmetry score around axis (sym_conf)
      - OPTIONAL OCR hint for Ø / SECTION using EasyOCR (dia_text_conf, section_conf)
    - Combine into view_conf (weighted score)
    
    Returns:
    - Ranked views with sub-scores
    - Chosen best view if conf >= threshold (0.65)
    - Debug artifact images (axis overlay, symmetry overlay)
    
    No stack inference yet. No STEP. Fully deterministic and debuggable.
    """
    # Verify job exists
    try:
        job = job_service.get_job(job_id)
    except HTTPException:
        raise HTTPException(status_code=404, detail="Job not found")
    
    try:
        # Auto-detect turned view
        result = auto_detect_service.auto_detect_turned_view(job_id)
        return result
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Auto-detection failed: {str(e)}")

@router.post("/jobs/{job_id}/pdf/infer_stack")
async def infer_stack(job_id: str, request: Request):
    """Infer TurnedPartStack from chosen turned view (Phase B: stack inference)."""

    print(f"[API] infer_stack called for job_id: {job_id}")

    # Verify job exists
    try:
        job = job_service.get_job(job_id)
        print(f"[API] Job found: {job.job_id}, status: {job.status}")
    except HTTPException:
        print(f"[API] Job not found: {job_id}")
        raise HTTPException(status_code=404, detail="Job not found")

    outputs_path = stack_inference_service.file_storage.get_outputs_path(job_id)
    results_file = outputs_path / "auto_detect_results.json"

    print(f"[API] Looking for auto_detect_results.json at: {results_file}")
    if not results_file.exists():
        raise HTTPException(
            status_code=400,
            detail="Auto-detect results not found. Run auto_detect_turned_view first."
        )

    with open(results_file, "r") as f:
        auto_detect_results = json.load(f)

    # Parse request body
    request_body = {}
    try:
        body = await request.body()
        if body:
            request_body = json.loads(body)
            print(f"[API] Request body parsed: {request_body}")
    except Exception as e:
        print(f"[API] Error parsing request body: {e}")
        request_body = {}

    page = request_body.get("page")
    view_index = request_body.get("view_index")

    selected_view = None

    # Manual selection: try to match ranked_views
    if page is not None and view_index is not None:
        print(f"[API] Manual view selection: page={page}, view_index={view_index}")
        ranked_views = auto_detect_results.get("ranked_views", [])
        for v in ranked_views:
            if v.get("page") == page and v.get("view_index") == view_index:
                selected_view = v
                break

        # ✅ Fallback: allow user selection even if not in ranked_views
        if selected_view is None:
            selected_view = {"page": page, "view_index": view_index, "scores": {}}

    # If still none: use best_view
    if selected_view is None:
        best_view = auto_detect_results.get("best_view")
        if best_view is None:
            raise HTTPException(
                status_code=400,
                detail="No best view found. Auto-detection below threshold. Provide page and view_index."
            )
        selected_view = best_view

    print(
        f"[API] Selected view (pre-enrich): "
        f"page={selected_view.get('page')} view_index={selected_view.get('view_index')}"
    )

    # ✅ CRITICAL FIX: Enrich selected_view with bbox_pixels from pdf_views
    views_dir = outputs_path / "pdf_views"
    page_num = selected_view.get("page")
    idx = selected_view.get("view_index")

    if page_num is None or idx is None:
        raise HTTPException(status_code=400, detail="Selected view missing page/view_index")

    page_views_file = views_dir / f"page_{page_num}_views.json"
    if not page_views_file.exists():
        raise HTTPException(
            status_code=400,
            detail=f"Missing {page_views_file.name}. Run /pdf/detect_views first."
        )

    with open(page_views_file, "r") as f:
        page_views = json.load(f)

    views = page_views.get("views", [])
    if not views:
        raise HTTPException(status_code=400, detail=f"No views found for page {page_num}")

    if not isinstance(idx, int):
        try:
            idx = int(idx)
        except Exception:
            raise HTTPException(status_code=400, detail="view_index must be an integer")

    if idx < 0 or idx >= len(views):
        raise HTTPException(
            status_code=400,
            detail=f"view_index {idx} out of range for page {page_num} (0..{len(views) - 1})"
        )

    selected_view["bbox_pixels"] = views[idx].get("bbox_pixels")
    selected_view["bbox"] = views[idx].get("bbox")
    selected_view["area"] = views[idx].get("area")
    selected_view["image_size"] = page_views.get("image_size")

    if not selected_view.get("bbox_pixels"):
        raise HTTPException(
            status_code=400,
            detail="Selected view missing bbox_pixels; cannot crop for stack inference."
        )

    print(
        f"[API] Selected view (enriched): page={page_num}, view_index={idx}, "
        f"bbox_pixels={selected_view.get('bbox_pixels')}"
    )

    try:
        print(f"[API] Starting stack inference...")
        mode = "auto_detect"
        result = stack_inference_service.infer_stack_from_view(job_id, selected_view, mode=mode)
        print(f"[API] Stack inference completed. Segments: {len(result.get('segments', []))}")

        # OPTIONAL: keep your STEP auto-generation logic here if needed...
        result["outputs_info"] = build_outputs_info(job_id).dict()
        return result

    except FileNotFoundError as e:
        print(f"[API] FileNotFoundError during stack inference: {e}")
        raise HTTPException(status_code=400, detail=str(e))

    except ValueError as e:
        # ✅ predictable inference failures → 400 not 500
        print(f"[API] ValueError during stack inference: {e}")
        raise HTTPException(status_code=400, detail=str(e))

    except Exception as e:
        print(f"[API] Exception during stack inference: {type(e).__name__}: {e}")
        import traceback
        print(f"[API] Traceback:\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Stack inference failed: {str(e)}")

@router.post("/jobs/{job_id}/auto/approve_step")
async def approve_step(job_id: str):
    """Approve stack for STEP generation (bypasses safety gate).
    
    This endpoint allows manual approval of the stack even if it fails
    the safety gate checks. Use with caution.
    
    Args:
        job_id: Job identifier
        
    Returns:
        Dictionary with approval status
    """
    # Verify job exists
    try:
        job = job_service.get_job(job_id)
    except HTTPException:
        raise HTTPException(status_code=404, detail="Job not found")
    
    try:
        from pathlib import Path
        from datetime import datetime, timezone
        import json
        
        outputs_path = stack_inference_service.file_storage.get_outputs_path(job_id)
        approval_file = outputs_path / "step_approval.json"
        
        # Save approval
        approval_data = {
            "status": "approved",
            "approved_at_utc": datetime.now(timezone.utc).isoformat(),
            "approved_by": "user"  # Could be extended to track user ID
        }
        with open(approval_file, 'w') as f:
            json.dump(approval_data, f, indent=2)
        
        return {
            "job_id": job_id,
            "status": "approved",
            "message": "Stack approved for STEP generation"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to approve stack: {str(e)}")


@router.post("/jobs/{job_id}/auto/generate_step")
async def auto_generate_step(job_id: str):
    """Generate STEP file from auto-converted inferred stack.
    
    Uses inferred_stack.json to:
    - Convert segments to Profile2D
    - Build solid using RevolvedSolidBuilder
    - Export STEP
    - Run FeatureExtractor
    - Update part_summary.json with feature counts
    
    Safety Gate:
    - Checks overall_confidence >= 0.75
    - Checks all segments have confidence >= 0.5
    - Checks no more than 1 segment has "thin_wall" flag
    - Returns "needs_review" if any check fails
    - Only generates STEP when status == "approved"
    
    Generates:
    - model.step
    - part_summary.json (updated with feature counts)
    - model.glb (if converter available)
    """
    # Verify job exists
    try:
        job = job_service.get_job(job_id)
    except HTTPException:
        raise HTTPException(status_code=404, detail="Job not found")
    
    try:
        # Generate STEP from inferred stack (includes safety gate)
        result = auto_step_service.generate_step_from_inferred_stack(job_id)
        
        if result["status"] == "FAILED":
            raise HTTPException(status_code=400, detail=result.get("error", "STEP generation failed"))
        
        if result["status"] == "needs_review":
            # Return needs_review status (don't raise error, let frontend handle it)
            result["outputs_info"] = build_outputs_info(job_id).dict()
            return result
        
        # Add outputs info
        result["outputs_info"] = build_outputs_info(job_id).dict()
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"STEP generation failed: {str(e)}")

