"""RFQ endpoints."""

import json
import logging
from json import JSONDecodeError
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

logger = logging.getLogger(__name__)

from app.models.rfq_autofill import RFQAutofillRequest, RFQAutofillResponse
from app.services.rfq_autofill_service import RFQAutofillService
from app.services.rfq_excel_export_service import write_autofill_to_rfq_template, write_autofill_to_master_file
from app.services.pdf_spec_extractor import PDFSpecExtractor
from app.services.vendor_quote_extraction_service import VendorQuoteExtractionService
from app.services.currency_service import get_live_exchange_rate, get_all_rates_for_currency
from app.storage.file_storage import FileStorage
from app.services.rfq_excel_export_service import write_autofill_to_rfq_template, write_autofill_to_master_file

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
    scale_report_file = outputs_path / "scale_report.json"

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

    # Back-compat: older jobs may not embed scale_report into part_summary.json yet.
    # If scale_report.json exists, merge it into the dict (best-effort; do not fail request if unreadable).
    if not isinstance(data.get("scale_report"), dict) and scale_report_file.exists() and scale_report_file.is_file():
        try:
            with open(scale_report_file, "r", encoding="utf-8") as f:
                sr = json.load(f)
            if isinstance(sr, dict):
                data["scale_report"] = sr
        except Exception:
            # scale_report is optional, so ignore failures
            pass

    return data


@router.post("/autofill", response_model=RFQAutofillResponse)
async def rfq_autofill(
    request: RFQAutofillRequest,
    auto_export: bool = True,
    template_filename: str = "RFQ-2025-01369 - R1.custom.xlsx",
):
    """Auto-fill RFQ fields from part_summary + tolerances (v1).
    
    If auto_export=True (default) and cost_inputs are provided, automatically
    generates an Excel file in the exports folder.
    """
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

    # Auto-populate metadata from job if not provided
    cost_inputs_dict = request.cost_inputs.model_dump() if request.cost_inputs is not None else None
    if cost_inputs_dict and request.source.job_id:
        job_id = request.source.job_id
        job_inputs_path = Path("data/jobs") / job_id / "inputs"
        pdf_path = None
        
        # Find PDF file and extract drawing number from filename
        try:
            if job_inputs_path.exists():
                for f in job_inputs_path.iterdir():
                    if f.suffix.lower() == ".pdf" and f.name != "source.pdf":
                        pdf_path = f
                        stem = f.stem  # e.g., "060dm0022_B"
                        
                        # Extract part revision from filename (e.g., _B from 060dm0022_B.pdf)
                        revision = None
                        drawing_num = stem
                        if len(stem) > 2 and stem[-2] == "_" and stem[-1].isalpha():
                            revision = stem[-1].upper()
                            drawing_num = stem[:-2]  # Remove _B suffix -> "060dm0022"
                        
                        # Drawing Number = Part Number (without revision suffix)
                        if not cost_inputs_dict.get("drawing_number"):
                            cost_inputs_dict["drawing_number"] = drawing_num
                        
                        if not cost_inputs_dict.get("part_revision") and revision:
                            cost_inputs_dict["part_revision"] = revision
                        break
        except Exception as e:
            print(f"[RFQ Autofill] Warning: Could not extract drawing number: {e}")
        
        # Extract metadata from PDF text using PDFSpecExtractor
        if pdf_path and pdf_path.exists():
            try:
                extractor = PDFSpecExtractor()
                pdf_specs = extractor.extract_from_file(str(pdf_path))
                
                # Auto-fill from PDF extraction if not already provided by user
                if pdf_specs.get("success"):
                    specs = pdf_specs.get("specs", pdf_specs.get("extracted_specs", {}))
                    
                    if not cost_inputs_dict.get("part_name") and specs.get("part_name"):
                        cost_inputs_dict["part_name"] = specs["part_name"]
                    
                    if not cost_inputs_dict.get("material_grade") and specs.get("material_grade"):
                        cost_inputs_dict["material_grade"] = specs["material_grade"]
                    
                    if not cost_inputs_dict.get("material_spec") and specs.get("material_spec"):
                        cost_inputs_dict["material_spec"] = specs["material_spec"]
                    
                    if not cost_inputs_dict.get("part_revision") and specs.get("revision"):
                        cost_inputs_dict["part_revision"] = specs["revision"]
                    
                    print(f"[RFQ Autofill] PDF extraction: part_name={specs.get('part_name')}, material={specs.get('material_grade')}")
            except Exception as e:
                print(f"[RFQ Autofill] Warning: PDF metadata extraction failed: {e}")
        
        # Set default Part Type based on mode (turned parts)
        if not cost_inputs_dict.get("part_type"):
            cost_inputs_dict["part_type"] = "Turned"
        
        # Set default RFQ Status
        if not cost_inputs_dict.get("rfq_status"):
            cost_inputs_dict["rfq_status"] = "Open"

    # Identify the EXACT part_summary object that will be used for autofill computation
    part_summary_used = part_summary
    
    # ── BUG-A FIX: Populate raw_dimensions from PDF if missing ──────────────
    # Calibration + machining features depend on inference_metadata.raw_dimensions.
    # The client rarely sends OCR dims, so we extract them server-side once, up front.
    job_id = request.source.job_id
    raw_dims_populated = 0
    if job_id:
        meta = part_summary_used.get("inference_metadata")
        if meta is None:
            meta = {}
            part_summary_used["inference_metadata"] = meta
        existing_raw = meta.get("raw_dimensions") or []
        if not existing_raw:
            try:
                extractor = PDFSpecExtractor()
                fs = FileStorage()
                job_inputs = fs.get_inputs_path(job_id)
                if job_inputs.exists():
                    pdf_files = [f for f in job_inputs.iterdir() if f.suffix.lower() == ".pdf"]
                    if pdf_files:
                        all_candidates = extractor.extract_all_dimension_candidates(str(pdf_files[0]))
                        if all_candidates:
                            meta["raw_dimensions"] = all_candidates
                            raw_dims_populated = len(all_candidates)
                            logger.info(f"[RFQ_AUTOFILL] Populated raw_dimensions from PDF text scrape: {raw_dims_populated} candidates")
                            for c in all_candidates[:10]:
                                logger.info(f"  {c['kind']} val={c['value_in']} conf={c['confidence']} tol={c.get('is_tolerance')} text={c['text'][:60]}")
            except Exception as e:
                logger.warning(f"[RFQ_AUTOFILL] Failed to populate raw_dimensions from PDF: {e}")
    
    logger.info(f"[RFQ_AUTOFILL] raw_dimensions_count={len((part_summary_used.get('inference_metadata') or {}).get('raw_dimensions') or [])} (populated_from_pdf={raw_dims_populated})")
    
    # ── Pre-compute OCR finish dims so calibration can use them ─────────────
    from app.services.ocr_finish_selector import select_finish_dims_from_ocr
    pre_ocr = select_finish_dims_from_ocr(part_summary_used) if part_summary_used else None
    pre_ocr_od = None
    if pre_ocr and pre_ocr.get("finish_od_in") is not None:
        pre_ocr_od = pre_ocr["finish_od_in"]
        inference_meta = part_summary_used.setdefault("inference_metadata", {})
        existing_diams = inference_meta.get("ocr_diameters_in") or []
        if not any(abs(d.get("value", 0) - pre_ocr_od) < 0.002 for d in existing_diams if isinstance(d, dict)):
            existing_diams.append({"value": pre_ocr_od, "text": f"OCR finish OD {pre_ocr_od}", "confidence": 0.90})
            inference_meta["ocr_diameters_in"] = existing_diams
            logger.info(f"[RFQ_AUTOFILL] Injected OCR finish OD={pre_ocr_od:.4f} into calibration candidates")
        pre_ocr_id = pre_ocr.get("finish_id_in")
        if pre_ocr_id:
            if not any(abs(d.get("value", 0) - pre_ocr_id) < 0.002 for d in existing_diams if isinstance(d, dict)):
                existing_diams.append({"value": pre_ocr_id, "text": f"OCR finish ID {pre_ocr_id}", "confidence": 0.85})
                inference_meta["ocr_diameters_in"] = existing_diams
                logger.info(f"[RFQ_AUTOFILL] Injected OCR finish ID={pre_ocr_id:.4f} into calibration candidates")

    # ── Run geometry scale calibration ──────────────────────────────────────
    from app.services.geometry_scale_calibration import GeometryScaleCalibrationService
    
    calibration_service = GeometryScaleCalibrationService()
    calibrated_summary = None
    scale_factor = None
    calibration_confidence = 0.0
    ratios = []
    
    try:
        calibrated_summary, scale_factor, calibration_confidence, ratios = \
            calibration_service.calibrate_geometry_scale(part_summary_used, job_id=job_id)
    except Exception as e:
        import traceback
        logger.error(f"[RFQ_AUTOFILL] Calibration error: {e}\n{traceback.format_exc()}")
    
    if ratios is None:
        ratios = []
    
    # Extract calibration debug info from calibrated_summary
    scale_calibration_applied = False
    matched_pairs_count = 0
    scaled_xy_flag = False
    scaled_z_flag = False
    
    # Initialize ratios list if not provided
    if ratios is None:
        ratios = []
    
    if calibrated_summary is not None:
        scale_report_after = calibrated_summary.get("scale_report", {})
        if isinstance(scale_report_after, dict):
            scale_method_after = scale_report_after.get("method", "")
            # Calibration was applied if method is "calibrated_from_ocr" OR scale_factor is not None
            scale_calibration_applied = (
                scale_method_after == "calibrated_from_ocr" or 
                scale_factor is not None
            )
            
            if scale_calibration_applied:
                # CRITICAL: Replace part_summary_used with calibrated version
                part_summary_used = calibrated_summary
                
                scaled_xy_flag = scale_report_after.get("scaled_xy", False)
                scaled_z_flag = scale_report_after.get("scaled_z", False)
                matched_pairs_count = scale_report_after.get("matched_pairs_count", len(ratios) if ratios else 0)
    
    # If calibration didn't apply, use original counts
    if not scale_calibration_applied:
        matched_pairs_count = len(ratios) if ratios else 0
    
    # Definitive log line right before autofill calculation
    raw_dims_count = len((part_summary_used.get("inference_metadata") or {}).get("raw_dimensions") or [])
    scale_method_final = part_summary_used.get("scale_report", {}).get("method", "unknown") if isinstance(part_summary_used.get("scale_report"), dict) else "unknown"
    scale_factor_str = f"{scale_factor:.4f}" if scale_factor is not None else "None"
    logger.info(f"[RFQ_AUTOFILL] raw_dimensions_count={raw_dims_count} calibration_applied={scale_calibration_applied} scale_factor={scale_factor_str} matched_pairs={matched_pairs_count}")

    # ── RFQ_DIMENSION_TRACE: Geometry Scale Calibration ──────────────────
    _T = "[RFQ_DIMENSION_TRACE]"
    logger.info(
        f"{_T}[SCALE_CALIBRATION] "
        f"scale_method={scale_method_final} "
        f"scale_factor={scale_factor_str} "
        f"matched_pairs={matched_pairs_count} "
        f"xy_scaled={scaled_xy_flag} "
        f"z_scaled={scaled_z_flag} "
        f"calibration_confidence={calibration_confidence}"
    )
    if ratios:
        for _ri, _ratio in enumerate(ratios):
            if isinstance(_ratio, dict):
                logger.info(
                    f"{_T}[SCALE_MATCH] "
                    f"ocr={_ratio.get('ocr_value', 'N/A')} "
                    f"geometry={_ratio.get('geometry_value', 'N/A')} "
                    f"ratio={_ratio.get('ratio', 'N/A')}"
                )
            elif isinstance(_ratio, (int, float)):
                logger.info(f"{_T}[SCALE_MATCH] ratio_value={_ratio}")
            else:
                logger.info(f"{_T}[SCALE_MATCH] raw={_ratio}")
    else:
        logger.info(f"{_T}[SCALE_MATCH] no_matching_pairs_found")

    service = RFQAutofillService()
    try:
        # CRITICAL: Pass part_summary_used (which is calibrated if calibration succeeded)
        result = service.autofill(
            part_no=part_no_original,
            part_summary_dict=part_summary_used,  # Use calibrated part_summary if calibration applied
            tolerances=request.tolerances.model_dump(),
            job_id=request.source.job_id,
            step_metrics=request.source.step_metrics,
            mode=request.mode,
            cost_inputs=cost_inputs_dict,
            vendor_quote_mode=request.vendor_quote_mode,
        )
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        logger.error(f"RFQ autofill error: {e}\n{error_details}")
        raise HTTPException(
            status_code=500,
            detail=f"RFQ autofill failed: {str(e)}. Check server logs for details."
        )
    
    # CRITICAL: Update response debug fields from calibration results
    try:
        result.debug.scale_calibration_applied = scale_calibration_applied
        result.debug.scale_factor_used = scale_factor if scale_calibration_applied else None
        result.debug.matched_pairs = matched_pairs_count
        result.debug.scaled_xy = scaled_xy_flag if scale_calibration_applied else None
        result.debug.scaled_z = scaled_z_flag if scale_calibration_applied else None
    except Exception as e:
        logger.warning(f"[RFQ_AUTOFILL] Failed to update debug fields: {e}")
        # Debug fields are optional, continue without them
    
    # Extract machining features (pure in-memory, no I/O — safe to call directly)
    try:
        from app.services.machining_feature_extractor import extract_machining_features
        machining_features = extract_machining_features(part_summary_used)
        result.debug.machining_features = machining_features
        logger.info(f"[RFQ_AUTOFILL] machining_features: status={machining_features.get('status')}, od={machining_features.get('main_turning_od_in')}, id={machining_features.get('main_bore_id_in')}, len={machining_features.get('main_turning_len_in')}")
    except Exception as e:
        logger.warning(f"[RFQ_AUTOFILL] machining_features error: {e}")
        result.debug.machining_features = {"status": "ERROR", "error": str(e)}
    
    # Auto-export Excel if enabled and cost_inputs provided
    if auto_export and cost_inputs_dict is not None:
        try:
            template_path = Path("data") / "rfq_estimation" / template_filename
            if template_path.exists():
                now = datetime.utcnow()
                date_tag = now.strftime("%d-%b%Y")
                time_tag = now.strftime("%H%M%S")
                safe_rfq = "".join(ch for ch in str(request.rfq_id) if ch.isalnum() or ch in ("-", "_")).strip() or "RFQ"
                out_dir = Path("data") / "rfq_estimation" / "exports" / safe_rfq
                out_path = out_dir / f"autofill_{safe_rfq}_{date_tag}_{time_tag}.xlsx"
                
                write_autofill_to_rfq_template(
                    template_path=template_path,
                    output_path=out_path,
                    part_no=part_no_original,
                    autofill_response=result.model_dump(),
                    cost_inputs=cost_inputs_dict,  # Use dict with auto-extracted metadata
                    sheet_name="RFQ Details",
                )
                print(f"[RFQ Autofill] Auto-exported Excel to: {out_path}")
        except Exception as e:
            # Don't fail the autofill request if export fails
            print(f"[RFQ Autofill] Warning: Auto-export failed: {e}")

    # ── RFQ_DIMENSION_TRACE: API Output ────────────────────────────────
    try:
        import json as _json
        _api_output = result.model_dump()
        logger.info(f"{_T}[API_OUTPUT] {_json.dumps(_api_output, indent=2, default=str)}")
    except Exception as _trace_err:
        logger.info(f"{_T}[API_OUTPUT] error_serializing_response: {_trace_err}")

    return result


@router.post("/vendor_quote_extract")
async def rfq_vendor_quote_extract(job_id: str):
    """
    OCR-extract vendor-quote fields from the job's uploaded PDF (image-based drawings supported).

    This does NOT touch Jobs API. It reads existing job artifacts:
    - outputs/pdf_pages/page_*.png (rendered at 300 DPI)
    - outputs/auto_detect_results.json + outputs/pdf_views/page_*.json (for best-view crop)

    Returns:
    - fields: { <excel-like-field>: { value: str|null, confidence: 0..1, source: "ocr" } }
    - debug: crop + ocr stats
    """
    if not job_id or not str(job_id).strip():
        raise HTTPException(status_code=400, detail="job_id is required")

    svc = VendorQuoteExtractionService()
    try:
        out = svc.extract_from_job(str(job_id).strip())
        # Debug: confirm which module file/version the running server imported.
        try:
            import app.services.vendor_quote_extraction_service as vq_mod  # type: ignore

            out.setdefault("debug", {})
            if isinstance(out["debug"], dict):
                out["debug"]["vendor_quote_extractor_file"] = getattr(vq_mod, "__file__", None)
                out["debug"]["vendor_quote_extractor_version_runtime"] = getattr(vq_mod, "EXTRACTOR_VERSION", None)
        except Exception:
            pass
        return out
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"OCR extraction failed: {e}")


@router.post("/export_xlsx")
async def rfq_export_xlsx(
    request: RFQAutofillRequest,
    template_filename: str = "RFQ-2025-01369 - R1.custom.xlsx",
    template_path: Optional[str] = None,
    export_mode: str = "master",  # "master", "new_file", or "copy"
    custom_filename: Optional[str] = None,  # For new_file mode: user-specified filename
):
    """Export RFQ data to Excel.
    
    Three modes:
    - export_mode="master" (default): Updates a single master file. If Part No exists, updates that row.
      New Part Nos are appended. Each row gets a timestamp. Saves disk space.
    - export_mode="new_file": Creates a NEW Excel file from template (empty except this part).
      User can specify custom_filename or it will be auto-generated.
    - export_mode="copy": Creates a new timestamped copy for each export (legacy behavior).
    """
    if not request.part_no or not request.part_no.strip():
        raise HTTPException(status_code=400, detail="part_no is required")

    part_no_original = request.part_no.strip()

    part_summary: Optional[Dict[str, Any]] = None
    if request.source.part_summary is not None:
        if not isinstance(request.source.part_summary, dict):
            raise HTTPException(status_code=400, detail="source.part_summary must be a JSON object")
        part_summary = request.source.part_summary
    elif request.source.job_id is not None:
        part_summary = load_part_summary(request.source.job_id)
    else:
        raise HTTPException(status_code=400, detail="Either source.part_summary or source.job_id is required")

    # Auto-populate metadata from job if not provided (same logic as /autofill)
    cost_inputs_dict = request.cost_inputs.model_dump() if request.cost_inputs is not None else None
    if cost_inputs_dict and request.source.job_id:
        job_id = request.source.job_id
        job_inputs_path = Path("data/jobs") / job_id / "inputs"
        pdf_path = None
        
        # Find PDF file and extract drawing number from filename
        try:
            if job_inputs_path.exists():
                for f in job_inputs_path.iterdir():
                    if f.suffix.lower() == ".pdf" and f.name != "source.pdf":
                        pdf_path = f
                        stem = f.stem  # e.g., "060dm0022_B"
                        
                        # Extract part revision from filename (e.g., _B from 060dm0022_B.pdf)
                        revision = None
                        drawing_num = stem
                        if len(stem) > 2 and stem[-2] == "_" and stem[-1].isalpha():
                            revision = stem[-1].upper()
                            drawing_num = stem[:-2]  # Remove _B suffix -> "060dm0022"
                        
                        # Drawing Number = Part Number (without revision suffix)
                        if not cost_inputs_dict.get("drawing_number"):
                            cost_inputs_dict["drawing_number"] = drawing_num
                        
                        if not cost_inputs_dict.get("part_revision") and revision:
                            cost_inputs_dict["part_revision"] = revision
                        break
        except Exception as e:
            print(f"[RFQ Export] Warning: Could not extract drawing number: {e}")
        
        # Extract metadata from PDF text using OCR
        if pdf_path and pdf_path.exists():
            try:
                extractor = PDFSpecExtractor()
                pdf_specs = extractor.extract_from_file(str(pdf_path))
                
                if pdf_specs.get("success"):
                    specs = pdf_specs.get("specs", pdf_specs.get("extracted_specs", {}))
                    
                    if not cost_inputs_dict.get("part_name") and specs.get("part_name"):
                        cost_inputs_dict["part_name"] = specs["part_name"]
                    
                    if not cost_inputs_dict.get("material_grade") and specs.get("material_grade"):
                        cost_inputs_dict["material_grade"] = specs["material_grade"]
                    
                    if not cost_inputs_dict.get("material_spec") and specs.get("material_spec"):
                        cost_inputs_dict["material_spec"] = specs["material_spec"]
                    
                    if not cost_inputs_dict.get("part_revision") and specs.get("revision"):
                        cost_inputs_dict["part_revision"] = specs["revision"]
                    
                    print(f"[RFQ Export] PDF extraction: part_name={specs.get('part_name')}, material={specs.get('material_grade')}")
            except Exception as e:
                print(f"[RFQ Export] Warning: PDF metadata extraction failed: {e}")
        
        # Set defaults
        if not cost_inputs_dict.get("part_type"):
            cost_inputs_dict["part_type"] = "Turned"
        if not cost_inputs_dict.get("rfq_status"):
            cost_inputs_dict["rfq_status"] = "Open"

    service = RFQAutofillService()
    resp = service.autofill(
        part_no=part_no_original,
        part_summary_dict=part_summary,
        tolerances=request.tolerances.model_dump(),
        step_metrics=request.source.step_metrics,
        mode=request.mode,
        cost_inputs=cost_inputs_dict,
        vendor_quote_mode=request.vendor_quote_mode,
    )

    # Apply LLM / user dimension overrides so Excel uses the correct extracted values
    # rather than the geometry-computed fallback values.
    resp_dict = resp.model_dump()
    if request.dimension_overrides:
        _dim_fields = ("finish_od_in", "finish_id_in", "finish_len_in", "rm_od_in", "rm_id_in", "rm_len_in")
        for key, val in request.dimension_overrides.items():
            if key in _dim_fields and val is not None:
                try:
                    resp_dict["fields"][key]["value"] = float(val)
                    resp_dict["fields"][key]["source"] = "llm_override"
                except (KeyError, TypeError):
                    pass  # field not present — skip
        print(f"[RFQ Export] Applied dimension_overrides: {request.dimension_overrides}")

    # Template can be an explicit path or a filename under backend/data/rfq_estimation/
    if template_path:
        template_path = Path(template_path)
    else:
        template_path = Path("data") / "rfq_estimation" / template_filename
    if not template_path.exists():
        raise HTTPException(status_code=404, detail=f"Template not found: {template_path}")

    safe_rfq = "".join(ch for ch in str(request.rfq_id) if ch.isalnum() or ch in ("-", "_")).strip() or "RFQ"
    now = datetime.utcnow()
    
    try:
        if export_mode == "master":
            # MASTER FILE MODE: Single file, update/append rows
            master_dir = Path("data") / "rfq_estimation" / "master"
            master_filename = "RFQ-Master-2025.xlsx"
            master_path = master_dir / master_filename
            
            result = write_autofill_to_master_file(
                master_path=master_path,
                template_path=template_path,
                part_no=part_no_original,
                autofill_response=resp_dict,
                cost_inputs=cost_inputs_dict,
                sheet_name="RFQ Details",
            )
            out_path = result.output_path
            
        elif export_mode == "new_file":
            # NEW FILE MODE: Create fresh Excel or update existing custom file
            if custom_filename:
                # Sanitize user-provided filename
                safe_name = "".join(ch for ch in custom_filename if ch.isalnum() or ch in ("-", "_", " ")).strip()
                if not safe_name:
                    safe_name = f"RFQ-{part_no_original}"
                if not safe_name.endswith(".xlsx"):
                    safe_name += ".xlsx"
            else:
                # Auto-generate filename
                date_tag = now.strftime("%d-%b-%Y")
                safe_name = f"RFQ-{part_no_original}-{date_tag}.xlsx"
            
            out_dir = Path("data") / "rfq_estimation" / "custom"
            out_path = out_dir / safe_name
            
            # Check if the file already exists - if so, UPDATE it instead of creating fresh
            if out_path.exists():
                # Use master file update logic on existing custom file
                print(f"[RFQ Export] Updating existing custom file: {out_path.name}")
                result = write_autofill_to_master_file(
                    master_path=out_path,
                    template_path=template_path,
                    part_no=part_no_original,
                    autofill_response=resp_dict,
                    cost_inputs=cost_inputs_dict,
                    sheet_name="RFQ Details",
                )
                out_path = result.output_path
            else:
                # Create fresh file from template
                from app.services.rfq_excel_export_service import write_autofill_to_new_file
                result = write_autofill_to_new_file(
                    template_path=template_path,
                    output_path=out_path,
                    part_no=part_no_original,
                    autofill_response=resp_dict,
                    cost_inputs=cost_inputs_dict,
                    sheet_name="RFQ Details",
                )
                out_path = result.output_path
            
        else:  # "copy" mode (legacy)
            # COPY MODE: Create new timestamped copy each time
            date_tag = now.strftime("%d-%b%Y")
            time_tag = now.strftime("%H%M%S")
            out_dir = Path("data") / "rfq_estimation" / "exports" / safe_rfq
            out_path = out_dir / f"autofill_{safe_rfq}_{date_tag}_{time_tag}.xlsx"
            
            write_autofill_to_rfq_template(
                template_path=template_path,
                output_path=out_path,
                part_no=part_no_original,
                autofill_response=resp_dict,
                cost_inputs=cost_inputs_dict,
                sheet_name="RFQ Details",
            )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Template file not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        print(f"[RFQ Export] Error: {e}")
        raise HTTPException(status_code=400, detail=f"Failed to generate RFQ Excel file: {str(e)}")

    return FileResponse(
        path=str(out_path),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=out_path.name,
    )


@router.get("/exports")
async def rfq_list_exports(rfq_id: str) -> Dict[str, Any]:
    """List generated RFQ export XLSX files (newest first) for an rfq_id."""
    safe_rfq = "".join(ch for ch in str(rfq_id) if ch.isalnum() or ch in ("-", "_")).strip()
    if not safe_rfq:
        raise HTTPException(status_code=400, detail="rfq_id is required")

    base_dir = Path("data") / "rfq_estimation" / "exports" / safe_rfq
    if not base_dir.exists():
        return {"rfq_id": rfq_id, "files": []}

    files: List[Dict[str, Any]] = []
    for p in base_dir.glob("*.xlsx"):
        try:
            stat = p.stat()
            files.append(
                {
                    "filename": p.name,
                    "size_bytes": int(stat.st_size),
                    "mtime_utc": datetime.utcfromtimestamp(stat.st_mtime).isoformat() + "Z",
                }
            )
        except OSError:
            continue

    files.sort(key=lambda d: d.get("mtime_utc", ""), reverse=True)
    return {"rfq_id": rfq_id, "files": files}


@router.get("/exports/{rfq_id}/{filename}")
async def rfq_download_export(rfq_id: str, filename: str):
    """Download a previously generated export file (path-safe)."""
    safe_rfq = "".join(ch for ch in str(rfq_id) if ch.isalnum() or ch in ("-", "_")).strip()
    if not safe_rfq:
        raise HTTPException(status_code=400, detail="Invalid rfq_id")

    if not filename or ("/" in filename) or ("\\" in filename) or (".." in filename):
        raise HTTPException(status_code=400, detail="Invalid filename")

    base_dir = Path("data") / "rfq_estimation" / "exports" / safe_rfq
    target = (base_dir / filename)

    try:
        target.resolve().relative_to(base_dir.resolve())
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid path")

    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Export file not found")

    return FileResponse(
        path=str(target),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=target.name,
    )


@router.post("/extract_pdf_specs")
async def extract_pdf_specs(
    rfq_id: str,
    pdf_file_path: str,
):
    """
    Extract specifications from a PDF engineering drawing.
    
    Args:
        rfq_id: RFQ identifier
        pdf_file_path: Path to PDF file (can be absolute or relative to backend/data/pdfs/)
    
    Returns:
        Extracted specifications with confidence scores
    """
    # Resolve PDF path
    pdf_path = Path(pdf_file_path)
    
    if not pdf_path.is_absolute():
        # Try relative to backend/data/pdfs/
        fs = FileStorage()
        pdf_dir = fs.data_root / "pdfs"
        pdf_path = pdf_dir / pdf_file_path
    
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail=f"PDF file not found: {pdf_file_path}")
    
    # Extract specifications
    extractor = PDFSpecExtractor()
    result = extractor.extract_from_file(str(pdf_path))
    
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result.get("error", "Failed to extract from PDF"))
    
    # Add RFQ context
    result["rfq_id"] = rfq_id
    result["pdf_path"] = str(pdf_path)
    
    return result


@router.post("/extract_pdf_from_job")
async def extract_pdf_from_job(
    job_id: str,
    rfq_id: Optional[str] = None,
):
    """
    Extract specifications from a PDF that was uploaded to a job.
    
    This endpoint works with your existing job upload workflow:
    1. User uploads PDF at /jobs/new
    2. Job is created with PDF in inputs/
    3. Call this endpoint with job_id to extract specs
    
    Args:
        job_id: The job ID (e.g., "53f4afc4-10d2-4cdb-864e-fc2777472707")
        rfq_id: Optional RFQ identifier (will use job_id if not provided)
    
    Returns:
        Extracted specifications with confidence scores
    """
    if not rfq_id:
        rfq_id = job_id
    
    # Find PDF in job's inputs directory
    fs = FileStorage()
    job_path = fs.get_job_path(job_id)
    inputs_dir = job_path / "inputs"
    
    if not inputs_dir.exists():
        raise HTTPException(status_code=404, detail=f"Job inputs directory not found: {job_id}")
    
    # Find PDF file
    pdf_files = list(inputs_dir.glob("*.pdf"))
    
    if not pdf_files:
        raise HTTPException(status_code=404, detail=f"No PDF file found in job {job_id}")
    
    if len(pdf_files) > 1:
        raise HTTPException(
            status_code=400, 
            detail=f"Multiple PDF files found. Please specify which one to use."
        )
    
    pdf_path = pdf_files[0]
    
    # Extract specifications
    extractor = PDFSpecExtractor()
    result = extractor.extract_from_file(str(pdf_path))
    
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result.get("error", "Failed to extract from PDF"))
    
    # Add context
    result["job_id"] = job_id
    result["rfq_id"] = rfq_id
    result["pdf_filename"] = pdf_path.name
    result["pdf_path"] = str(pdf_path)
    
    return result


@router.post("/autofill_from_pdf")
async def autofill_from_pdf(
    rfq_id: str,
    pdf_file_path: str,
    rm_od_allowance_in: float = 0.26,
    rm_len_allowance_in: float = 0.35,
    rm_rate_per_kg: float = 100.0,
    turning_rate_per_min: float = 7.5,
    roughing_cost: float = 162.0,
    inspection_cost: float = 10.0,
    material_density_kg_m3: float = 7200.0,
    special_process_cost: Optional[float] = None,
):
    """
    Extract specs from PDF and run autofill + calculate costs.
    
    This is a convenience endpoint that combines:
    1. PDF spec extraction
    2. RFQ autofill
    3. Cost estimation
    
    Returns RFQAutofillResponse with all fields populated.
    """
    # Extract specs from PDF
    pdf_path = Path(pdf_file_path)
    
    if not pdf_path.is_absolute():
        fs = FileStorage()
        pdf_dir = fs.data_root / "pdfs"
        pdf_path = pdf_dir / pdf_file_path
    
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail=f"PDF file not found: {pdf_file_path}")
    
    extractor = PDFSpecExtractor()
    extract_result = extractor.extract_from_file(str(pdf_path))
    
    if not extract_result["success"]:
        raise HTTPException(status_code=400, detail=extract_result.get("error", "Failed to extract from PDF"))
    
    specs = extract_result["extracted_specs"]
    
    # Build part_summary from extracted specs (minimal format for ENVELOPE mode)
    part_summary = {
        "part_no": specs.get("part_no", "UNKNOWN"),
        "units": {"length": "in"},
        "z_range": [0.0, specs.get("finish_len_in", 0.0)] if specs.get("finish_len_in") else None,
        "segments": [
            {
                "z_start": 0.0,
                "z_end": specs.get("finish_len_in", 0.0),
                "od_diameter": specs.get("finish_od_in", 0.0),
                "id_diameter": specs.get("finish_id_in", 0.0),
                "confidence": specs.get("confidence", {}).get("overall", 0.85),
                "flags": [],
            }
        ] if specs.get("finish_od_in") and specs.get("finish_len_in") else [],
        "inference_metadata": {
            "overall_confidence": specs.get("confidence", {}).get("overall", 0.85),
            "source": "pdf_extraction",
        },
        "scale_report": {
            "method": "anchor_dimension",  # Assume PDF dimensions are accurate
            "validation_passed": True,
        },
        "pdf_extracted": True,
        "pdf_path": str(pdf_path),
    }
    
    # Build cost inputs
    cost_inputs_dict = {
        "rm_rate_per_kg": rm_rate_per_kg,
        "turning_rate_per_min": turning_rate_per_min,
        "roughing_cost": roughing_cost,
        "inspection_cost": inspection_cost,
        "material_density_kg_m3": material_density_kg_m3,
        "special_process_cost": special_process_cost,
    }
    
    # Run autofill
    service = RFQAutofillService()
    tolerances_dict = {
        "rm_od_allowance_in": rm_od_allowance_in,
        "rm_len_allowance_in": rm_len_allowance_in,
    }
    
    autofill_response = service.autofill(
        part_no=specs.get("part_no", "UNKNOWN"),
        part_summary_dict=part_summary,
        tolerances=tolerances_dict,
        step_metrics=None,
        mode="ENVELOPE",
        cost_inputs=cost_inputs_dict,
        vendor_quote_mode=True,  # Use Excel-exact calculations
    )
    
    # Add PDF extraction metadata to response
    autofill_response.debug["pdf_extracted"] = True
    autofill_response.debug["pdf_path"] = str(pdf_path)
    autofill_response.debug["pdf_extraction_confidence"] = specs.get("confidence", {})
    
    return autofill_response


@router.post("/export_xlsx_from_pdf")
async def export_xlsx_from_pdf(
    rfq_id: str,
    pdf_file_path: str,
    template_filename: str = "RFQ-2025-01369 - R1.xlsx",
    rm_od_allowance_in: float = 0.26,
    rm_len_allowance_in: float = 0.35,
    rm_rate_per_kg: float = 100.0,
    turning_rate_per_min: float = 7.5,
    roughing_cost: float = 162.0,
    inspection_cost: float = 10.0,
    material_density_kg_m3: float = 7200.0,
    special_process_cost: Optional[float] = None,
):
    """
    Complete workflow: PDF → Extract → Autofill → Export Excel.
    
    This endpoint:
    1. Extracts specs from PDF
    2. Runs RFQ autofill with cost estimation
    3. Exports to Excel file
    4. Returns the Excel file for download
    """
    # Get autofill result from PDF
    autofill_response = await autofill_from_pdf(
        rfq_id=rfq_id,
        pdf_file_path=pdf_file_path,
        rm_od_allowance_in=rm_od_allowance_in,
        rm_len_allowance_in=rm_len_allowance_in,
        rm_rate_per_kg=rm_rate_per_kg,
        turning_rate_per_min=turning_rate_per_min,
        roughing_cost=roughing_cost,
        inspection_cost=inspection_cost,
        material_density_kg_m3=material_density_kg_m3,
        special_process_cost=special_process_cost,
    )
    
    # Prepare export directory
    fs = FileStorage()
    export_base = fs.data_root / "rfq_estimation" / "exports" / rfq_id
    export_base.mkdir(parents=True, exist_ok=True)
    
    # Generate output filename
    timestamp = datetime.now().strftime("%d%b%Y_%H%M%S")
    output_filename = f"autofill_pdf_{autofill_response.part_no}_{timestamp}.xlsx"
    output_path = export_base / output_filename
    
    # Find template
    template_dir = fs.data_root / "rfq_estimation"
    template_path = template_dir / template_filename
    
    if not template_path.exists():
        raise HTTPException(status_code=404, detail=f"Template not found: {template_filename}")
    
    # Write to Excel
    write_autofill_to_rfq_template(
        template_path=str(template_path),
        autofill_response=autofill_response,
        output_path=str(output_path),
    )
    
    # Return the file for download
    return FileResponse(
        path=str(output_path),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=output_filename,
        headers={"Content-Disposition": f'attachment; filename="{output_filename}"'},
    )


@router.get("/exchange_rate")
async def get_exchange_rate(
    from_currency: str = "USD",
    to_currency: str = "INR",
    fallback: Optional[float] = None,
):
    """
    Get live exchange rate between two currencies.
    
    Args:
        from_currency: Source currency code (default: USD)
        to_currency: Target currency code (default: INR)
        fallback: Optional fallback rate if API fails
        
    Returns:
        Exchange rate info with source
    """
    try:
        rate, source = get_live_exchange_rate(
            from_currency=from_currency,
            to_currency=to_currency,
            fallback_rate=fallback,
        )
        return {
            "from_currency": from_currency.upper(),
            "to_currency": to_currency.upper(),
            "rate": round(rate, 4),
            "source": source,
            "timestamp": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get exchange rate: {str(e)}")


@router.get("/exchange_rates")
async def get_all_exchange_rates(base_currency: str = "USD"):
    """
    Get all exchange rates for a base currency.
    
    Args:
        base_currency: Base currency code (default: USD)
        
    Returns:
        Dictionary of all rates for the base currency
    """
    try:
        rates = get_all_rates_for_currency(base_currency=base_currency)
        if not rates:
            raise HTTPException(status_code=503, detail="Exchange rate API unavailable")
        return {
            "base_currency": base_currency.upper(),
            "rates": rates,
            "timestamp": datetime.utcnow().isoformat(),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get exchange rates: {str(e)}")