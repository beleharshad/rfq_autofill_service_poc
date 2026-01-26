"""RFQ endpoints."""

import json
from json import JSONDecodeError
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.models.rfq_autofill import RFQAutofillRequest, RFQAutofillResponse
from app.services.rfq_autofill_service import RFQAutofillService
from app.services.rfq_excel_export_service import write_autofill_to_rfq_template
from app.services.pdf_spec_extractor import PDFSpecExtractor
from app.services.vendor_quote_extraction_service import VendorQuoteExtractionService
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

    service = RFQAutofillService()
    result = service.autofill(
        part_no=part_no_original,
        part_summary_dict=part_summary,
        tolerances=request.tolerances.dict(),
        job_id=request.source.job_id,
        step_metrics=request.source.step_metrics,
        mode=request.mode,
        cost_inputs=(request.cost_inputs.model_dump() if request.cost_inputs is not None else None),
        vendor_quote_mode=request.vendor_quote_mode,
    )
    
    # Auto-export Excel if enabled and cost_inputs provided
    if auto_export and request.cost_inputs is not None:
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
                    cost_inputs=request.cost_inputs.model_dump(),
                    sheet_name="RFQ Details",
                )
                print(f"[RFQ Autofill] Auto-exported Excel to: {out_path}")
        except Exception as e:
            # Don't fail the autofill request if export fails
            print(f"[RFQ Autofill] Warning: Auto-export failed: {e}")
    
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
):
    """Create a NEW filled RFQ Excel file (copy of template) for the given part_no.

    - Never modifies the template in-place.
    - Writes only the key dimension/estimate columns.
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

    service = RFQAutofillService()
    resp = service.autofill(
        part_no=part_no_original,
        part_summary_dict=part_summary,
        tolerances=request.tolerances.model_dump(),
        step_metrics=request.source.step_metrics,
        mode=request.mode,
        cost_inputs=(request.cost_inputs.model_dump() if request.cost_inputs is not None else None),
        vendor_quote_mode=request.vendor_quote_mode,
    )

    # Template can be an explicit path or a filename under backend/data/rfq_estimation/
    if template_path:
        template_path = Path(template_path)
    else:
        template_path = Path("data") / "rfq_estimation" / template_filename
    if not template_path.exists():
        raise HTTPException(status_code=404, detail=f"Template not found: {template_path}")

    now = datetime.utcnow()
    date_tag = now.strftime("%d-%b%Y")  # e.g. 12-Jan2026
    time_tag = now.strftime("%H%M%S")  # timestamp
    safe_rfq = "".join(ch for ch in str(request.rfq_id) if ch.isalnum() or ch in ("-", "_")).strip() or "RFQ"
    out_dir = Path("data") / "rfq_estimation" / "exports" / safe_rfq
    out_path = out_dir / f"autofill_{safe_rfq}_{date_tag}_{time_tag}.xlsx"

    try:
        write_autofill_to_rfq_template(
            template_path=template_path,
            output_path=out_path,
            part_no=part_no_original,
            autofill_response=resp.model_dump(),
            cost_inputs=(request.cost_inputs.model_dump() if request.cost_inputs is not None else None),
            sheet_name="RFQ Details",
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Template file not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to generate RFQ Excel file")

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


