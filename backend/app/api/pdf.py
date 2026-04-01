"""PDF processing endpoints for Assisted Manual Mode."""

import asyncio
import json
import threading
from typing import List
from fastapi import APIRouter, UploadFile, File, HTTPException, Request
from fastapi.responses import StreamingResponse
from pathlib import Path
import tempfile
from app.services.job_service import JobService
from app.services.pdf_service import PDFService
from app.services.auto_detect_service import AutoDetectService
from app.services.stack_inference_service import StackInferenceService
from app.services.auto_step_service import AutoStepService
from app.services.feature_detection_service import FeatureDetectionService
from app.services.cv_feature_detection_service import CVFeatureDetectionService
from app.utils.outputs_helper import build_outputs_info
from app.services.pdf_llm_pipeline import run_pipeline as _run_llm_pipeline

router = APIRouter()

# Per-job threading events — signalled when _run_llm_background finishes.
# Kept in-memory; SSE clients subscribe and get exactly one push.
_llm_done_events: dict[str, threading.Event] = {}
_llm_done_lock = threading.Lock()


def _get_llm_event(job_id: str) -> threading.Event:
    """Return (creating if needed) the completion Event for *job_id*."""
    with _llm_done_lock:
        if job_id not in _llm_done_events:
            _llm_done_events[job_id] = threading.Event()
        return _llm_done_events[job_id]


job_service = JobService()
pdf_service = PDFService()
auto_detect_service = AutoDetectService()
stack_inference_service = StackInferenceService()
auto_step_service = AutoStepService()
feature_detection_service = FeatureDetectionService()
cv_feature_detection_service = CVFeatureDetectionService()


def _pending_stub() -> dict:
    """Return a JSON-serialisable stub that signals the LLM is still running."""
    return {
        "pending": True,
        "available": True,
        "error": None,
        "error_type": None,
        "rate_limit_info": None,
        "extracted": {},
        "validation": {
            "recommendation": "PENDING",
            "overall_confidence": 0.0,
            "fields": {},
            "cross_checks": ["LLM analysis is running in the background. This page will update automatically."],
        },
        "code_issues": [],
        "valid": False,
    }


def _error_stub_from_exc(exc: Exception) -> dict:
    """Build a JSON-serialisable error stub from an exception."""
    from app.services.llm_service import RateLimitError as _RLE
    is_rl = isinstance(exc, _RLE) or "429" in str(exc) or "rate limit" in str(exc).lower()
    info = getattr(exc, "rate_limit_info", None)
    msg = str(exc)
    return {
        "pending": False,
        "error": msg,
        "error_type": "rate_limit" if is_rl else "pipeline_error",
        "rate_limit_info": info,
        "extracted": {},
        "validation": {
            "recommendation": "REVIEW",
            "overall_confidence": 0.0,
            "fields": {},
            "cross_checks": [
                (
                    f"LLM analysis unavailable — Gemini API rate limit (429). "
                    + (lambda i: (
                        f"retry_after={i.get('retry_after_s')}s, "
                        f"remaining_requests={i.get('remaining_requests')}"
                        if i.get('retry_after_s') is not None
                        else "Quota exhausted — try again later"
                    ))(info or {})
                    + ". Click Auto-Detect again after quota resets."
                ) if is_rl else f"LLM pipeline error: {msg}"
            ],
        },
        "code_issues": [],
        "valid": False,
    }


def _run_llm_background(pdf_path: Path, outputs_path: Path, job_id: str = "") -> None:
    """Run the two-agent LLM pipeline in a background thread.

    Writes the pending stub before running and replaces it with the real result
    (or an error stub) when the pipeline finishes.  Never raises.
    Signals the per-job threading.Event so SSE subscribers get exactly one push.
    """
    result_path = outputs_path / "llm_analysis.json"
    try:
        llm_result = _run_llm_pipeline(pdf_path)
        llm_result.pop("pending", None)          # remove pending flag on success
        outputs_path.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps(llm_result, indent=2), encoding="utf-8")
        print(f"[LLM-BG] Pipeline done — valid={llm_result.get('valid')}")
    except Exception as exc:
        print(f"[LLM-BG] Pipeline failed: {exc}")
        stub = _error_stub_from_exc(exc)
        try:
            outputs_path.mkdir(parents=True, exist_ok=True)
            result_path.write_text(json.dumps(stub, indent=2), encoding="utf-8")
        except Exception as write_exc:
            print(f"[LLM-BG] Failed to write error stub: {write_exc}")
    finally:
        if job_id:
            _get_llm_event(job_id).set()
            print(f"[LLM-BG] Signalled SSE event for job {job_id}")


@router.get("/jobs/{job_id}/llm-stream")
async def llm_stream(job_id: str):
    """SSE endpoint — pushes full llm_analysis.json exactly once when LLM completes.

    The frontend opens an EventSource, receives one message, then closes.
    No polling — zero repeated requests.
    """
    outputs_path = job_service.file_storage.get_outputs_path(job_id)
    stub_path = outputs_path / "llm_analysis.json"

    async def generate():
        # If the result is already written and not pending, push immediately —
        # UNLESS it is an 'interrupted' stub (server restart artifact), in which
        # case fall through and wait for the real result from the new background run.
        if stub_path.exists():
            try:
                data = json.loads(stub_path.read_text(encoding="utf-8-sig"))
                if not data.get("pending") and data.get("error_type") != "interrupted":
                    yield f"data: {json.dumps(data)}\n\n"
                    return
            except Exception:
                pass

        # Wait for the background thread to signal completion.
        event = _get_llm_event(job_id)
        loop = asyncio.get_event_loop()
        while True:
            # Check in 1-second slices so we can send keepalives.
            done = await loop.run_in_executor(None, event.wait, 1.0)
            if done or event.is_set():
                try:
                    data = json.loads(stub_path.read_text(encoding="utf-8-sig"))
                    yield f"data: {json.dumps(data)}\n\n"
                except Exception as exc:
                    yield f"data: {{\"error\": \"{exc}\"}}\n\n"
                return
            # Keepalive comment — prevents proxy / browser from timing out.
            yield ": keepalive\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/jobs/{job_id}/rerun_llm")
async def rerun_llm(job_id: str):
    """Delete any cached llm_analysis.json and re-run the LLM pipeline.

    Use this to recover from a previously failed LLM run (e.g. text-extraction
    error, rate-limit, or server-side availability issue).
    """
    try:
        job_service.get_job(job_id)
    except HTTPException:
        raise HTTPException(status_code=404, detail="Job not found")

    inputs_path = job_service.file_storage.get_inputs_path(job_id)
    outputs_path = job_service.file_storage.get_outputs_path(job_id)
    pdf_path = inputs_path / "source.pdf"

    if not pdf_path.exists():
        raise HTTPException(status_code=400, detail="source.pdf not found for this job")

    # Clear the cached (errored) result and reset the SSE event
    llm_out = outputs_path / "llm_analysis.json"
    try:
        llm_out.unlink(missing_ok=True)
    except Exception:
        pass
    _get_llm_event(job_id).clear()

    # Write pending stub immediately so the frontend knows work is in progress
    stub = _pending_stub()
    llm_out.write_text(json.dumps(stub, indent=2), encoding="utf-8")

    import threading as _threading
    t = _threading.Thread(
        target=_run_llm_background,
        args=(pdf_path, outputs_path, job_id),
        daemon=True,
    )
    t.start()

    return {"job_id": job_id, "status": "llm_restarted", "pending": True}


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
        # Auto-detect turned view (CV-based)
        result = auto_detect_service.auto_detect_turned_view(job_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Auto-detection failed: {str(e)}")

    # Run LLM extraction pipeline on the job's source PDF **in a background thread**
    # so this endpoint returns immediately without waiting for Gemini.
    # A pending stub is written to disk first; the thread overwrites it with the
    # real result (or an error stub) when the pipeline completes.
    pdf_path = job_service.file_storage.get_inputs_path(job_id) / "source.pdf"
    outputs_path = job_service.file_storage.get_outputs_path(job_id)
    outputs_path.mkdir(parents=True, exist_ok=True)
    llm_analysis_path = outputs_path / "llm_analysis.json"

    if pdf_path.exists():
        # Write pending stub so GET /llm-analysis returns status immediately
        stub = _pending_stub()
        llm_analysis_path.write_text(json.dumps(stub, indent=2), encoding="utf-8")
        t = threading.Thread(
            target=_run_llm_background,
            args=(pdf_path, outputs_path, job_id),
            daemon=True,
        )
        t.start()
        print(f"[API] LLM spec-extraction started in background thread {t.ident}")
        llm_analysis: dict = stub
    else:
        llm_analysis = {"error": "source.pdf not found", "pending": False}

    result["llm_analysis"] = llm_analysis
    return result

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

        # Run feature detection (non-blocking)
        # First try text-based detection, then CV-based as fallback/supplement
        text_features_ok = False
        try:
            print(f"[API] Starting text-based feature detection...")
            features_result = feature_detection_service.detect_features_text(job_id)

            if features_result["success"]:
                # Merge features into part_summary.json
                merge_success = feature_detection_service.merge_features_into_part_summary(job_id)
                if merge_success:
                    print(f"[API] Text features successfully merged into part_summary.json")
                    text_features_ok = True
                else:
                    print(f"[API] Warning: Failed to merge text features into part_summary.json")
            else:
                print(f"[API] Warning: Text feature detection failed: {features_result.get('error', 'Unknown error')}")

        except Exception as e:
            print(f"[API] Warning: Text feature detection encountered error (non-blocking): {e}")

        # Always run CV-based feature detection (complements text detection or serves as fallback)
        try:
            print(f"[API] Starting CV-based feature detection...")
            cv_result = cv_feature_detection_service.detect_features_cv(job_id)
            
            if cv_result.get("success"):
                holes_count = len(cv_result.get("features", {}).get("holes", []))
                slots_count = len(cv_result.get("features", {}).get("slots", []))
                print(f"[API] CV detection found: {holes_count} holes, {slots_count} slots")
                
                # Merge CV features with existing features
                merge_result = cv_feature_detection_service.merge_cv_with_text_features(job_id)
                if merge_result:
                    print(f"[API] CV features successfully merged into part_summary.json")
                else:
                    print(f"[API] Warning: Failed to merge CV features into part_summary.json")
            else:
                print(f"[API] Warning: CV feature detection failed: {cv_result.get('error', 'Unknown error')}")
                
        except Exception as e:
            print(f"[API] Warning: CV feature detection encountered error (non-blocking): {e}")

        # ----------------------
        # Run LLM dimension reasoning (non-blocking best-effort)
        # ----------------------
        try:
            outputs_path = stack_inference_service.file_storage.get_outputs_path(job_id)
            candidate_json = {
                "external_diameter_candidates": [],
                "internal_diameter_candidates": [],
                "axial_length_candidates": [],
                "ignored_candidates": [],
            }

            # Load text-detected features if available
            text_file = outputs_path / "features_text.json"
            if text_file.exists():
                try:
                    with open(text_file, 'r') as f:
                        text_data = json.load(f)
                    candidate_json["raw_text_features"] = text_data

                    features = text_data.get("features") or {}
                    # Holes often contain diameter candidates
                    for i, h in enumerate(features.get("holes", []) if isinstance(features.get("holes", []), list) else []):
                        try:
                            cand = {
                                "candidate_id": h.get("id") or f"text_hole_{i}",
                                "value": h.get("diameter") if isinstance(h.get("diameter"), (int, float)) else h.get("diameter") ,
                                "source_text": h.get("source_text") or str(h),
                                "type": "external" if h.get("is_external") else "internal" if h.get("is_internal") else "hole",
                                "meta": h,
                            }
                            # Classify as internal/external by simple heuristic
                            if cand["type"] == "external":
                                candidate_json["external_diameter_candidates"].append(cand)
                            elif cand["type"] == "internal":
                                candidate_json["internal_diameter_candidates"].append(cand)
                            else:
                                # If diameter looks like bore (contains 'ID' or similar), push to internal
                                label = str(cand["source_text"]).upper()
                                if "ID" in label or "BORE" in label:
                                    candidate_json["internal_diameter_candidates"].append(cand)
                                else:
                                    candidate_json["external_diameter_candidates"].append(cand)
                        except Exception:
                            continue

                    # Slots or other axial features may contain lengths
                    for i, s in enumerate(features.get("slots", []) if isinstance(features.get("slots", []), list) else []):
                        try:
                            cand = {
                                "candidate_id": s.get("id") or f"text_slot_{i}",
                                "value": s.get("length") or s.get("width") or None,
                                "source_text": s.get("source_text") or str(s),
                                "meta": s,
                            }
                            if cand["value"]:
                                candidate_json["axial_length_candidates"].append(cand)
                        except Exception:
                            continue

                except Exception:
                    pass

            # Load CV features if available
            cv_file = outputs_path / "features_cv.json"
            if cv_file.exists():
                try:
                    with open(cv_file, 'r') as f:
                        cv_data = json.load(f)
                    candidate_json["raw_cv_features"] = cv_data
                    for i, h in enumerate((cv_data.get("features") or {}).get("holes", [])):
                        try:
                            cand = {
                                "candidate_id": h.get("id") or f"cv_hole_{i}",
                                "value": h.get("diameter") or h.get("estimated_diameter"),
                                "source_text": h.get("debug_text") or str(h),
                                "meta": h,
                            }
                            # assume CV holes are internal by default if labeled as 'hole'
                            candidate_json["internal_diameter_candidates"].append(cand)
                        except Exception:
                            continue
                except Exception:
                    pass

            # Load part_summary segments to extract OD/ID and overall length candidates
            summary_file = outputs_path / "part_summary.json"
            if summary_file.exists():
                try:
                    with open(summary_file, 'r') as f:
                        summary = json.load(f)
                    candidate_json["part_summary"] = summary
                    segments = summary.get("segments") or []
                    for i, seg in enumerate(segments if isinstance(segments, list) else []):
                        try:
                            od = seg.get("od_diameter")
                            idd = seg.get("id_diameter")
                            zlen = seg.get("z_end") and seg.get("z_start") and (seg.get("z_end") - seg.get("z_start"))
                            if od:
                                candidate_json["external_diameter_candidates"].append({
                                    "candidate_id": seg.get("id") or f"seg_od_{i}",
                                    "value": od,
                                    "source_text": f"segment_{i}",
                                    "meta": seg,
                                })
                            if idd:
                                candidate_json["internal_diameter_candidates"].append({
                                    "candidate_id": seg.get("id") or f"seg_id_{i}",
                                    "value": idd,
                                    "source_text": f"segment_{i}",
                                    "meta": seg,
                                })
                            if zlen:
                                candidate_json["axial_length_candidates"].append({
                                    "candidate_id": seg.get("id") or f"seg_len_{i}",
                                    "value": zlen,
                                    "source_text": f"segment_{i}",
                                    "meta": seg,
                                })
                        except Exception:
                            continue
                except Exception:
                    pass

        except Exception as e:
            print(f"[API] Warning: Building candidate JSON for LLM failed: {e}")

        # ── Two-agent LLM spec extraction ───────────────────────────────────
        # auto_detect_turned_view already launches this in a background thread and
        # writes outputs/llm_analysis.json (pending stub → real result).
        # Only run here if the file doesn't exist yet (e.g., infer_stack was called
        # directly without going through auto_detect first).
        _llm_out = outputs_path / "llm_analysis.json"
        if not _llm_out.exists():
            try:
                _inputs_path = stack_inference_service.file_storage.get_inputs_path(job_id)
                _pdf_path = _inputs_path / "source.pdf"
                if _pdf_path.exists():
                    print(f"[API] LLM analysis not found — starting background pipeline...")
                    _stub = _pending_stub()
                    _llm_out.write_text(json.dumps(_stub, indent=2), encoding="utf-8")
                    _t = threading.Thread(
                        target=_run_llm_background,
                        args=(_pdf_path, outputs_path, job_id),
                        daemon=True,
                    )
                    _t.start()
                    result["llm_analysis"] = _stub
                else:
                    print(f"[API] Skipping LLM spec-extraction: source.pdf not found")
            except Exception as _e:
                print(f"[API] Warning: Failed to start background LLM: {_e}")
        else:
            # File exists (pending or completed) — read it and include in response
            try:
                result["llm_analysis"] = json.loads(_llm_out.read_text(encoding="utf-8-sig"))
            except Exception:
                pass
        # ───────────────────────────────────────────────────────────────────

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
        import traceback as _tb
        tb_str = _tb.format_exc()
        print(f"[API] Exception during stack inference: {type(e).__name__}: {e}")
        print(f"[API] Traceback:\n{tb_str}")
        raise HTTPException(
            status_code=500,
            detail=f"Stack inference failed: {type(e).__name__}: {e}\n\nTraceback:\n{tb_str}"
        )

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


@router.post("/jobs/{job_id}/detect_features_text")
async def detect_features_text(job_id: str):
    """Detect geometric features from PDF text using regex patterns.

    Extracts text from all PDF pages and uses regex patterns to detect:
    - Holes (drill, thru, csk, etc.)
    - Slots/Keyways
    - Chamfers
    - Fillets
    - Threads

    Saves results to outputs/features_text.json

    Args:
        job_id: Job identifier

    Returns:
        Dictionary with detection results and feature counts
    """
    # Verify job exists
    try:
        job = job_service.get_job(job_id)
    except HTTPException:
        raise HTTPException(status_code=404, detail="Job not found")

    try:
        # Detect features from PDF text
        result = feature_detection_service.detect_features_text(job_id)

        if not result["success"]:
            raise HTTPException(status_code=400, detail=result.get("error", "Feature detection failed"))

        # Log feature counts
        features = result.get("features", {})
        print(f"[Feature Detection] Job {job_id} - Detected:")
        print(f"  Holes: {len(features.get('holes', []))}")
        print(f"  Slots: {len(features.get('slots', []))}")
        print(f"  Chamfers: {len(features.get('chamfers', []))}")
        print(f"  Fillets: {len(features.get('fillets', []))}")
        print(f"  Threads: {len(features.get('threads', []))}")

        return result

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Feature detection failed: {str(e)}")


@router.post("/jobs/{job_id}/detect_features_cv")
async def detect_features_cv(job_id: str):
    """Detect holes and slots using computer vision on the selected turned view.

    Uses OpenCV to analyze the cropped view image and detect:
    - Holes (circular features using Hough circles and contour analysis)
    - Slots (elongated features using contour analysis)

    Requires FEATURE_CV_DETECT=1 environment variable.
    Saves results to outputs/features_cv.json

    Args:
        job_id: Job identifier

    Returns:
        Dictionary with detection results and feature counts
    """
    # Verify job exists
    try:
        job = job_service.get_job(job_id)
    except HTTPException:
        raise HTTPException(status_code=404, detail="Job not found")

    try:
        # Detect features using CV
        result = cv_feature_detection_service.detect_features_cv(job_id)

        if not result["success"]:
            # For CV detection, return the error rather than raising HTTPException
            # since CV detection is optional and may be disabled
            return result

        # Log feature counts
        features = result.get("features", {})
        print(f"[CV Feature Detection] Job {job_id} - Detected:")
        print(f"  Holes: {len(features.get('holes', []))}")
        print(f"  Slots: {len(features.get('slots', []))}")

        # Optionally merge with text features and update part_summary
        merge_result = cv_feature_detection_service.merge_cv_with_text_features(job_id)
        if merge_result:
            print(f"[CV Feature Detection] Successfully merged CV features with existing features")
        else:
            print(f"[CV Feature Detection] Warning: Could not merge CV features")

        return result

    except Exception as e:
        return {
            "success": False,
            "error": f"CV feature detection failed: {str(e)}",
            "features": None
        }


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

