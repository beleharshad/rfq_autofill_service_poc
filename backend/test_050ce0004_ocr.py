"""
Enhanced test script to check OCR extraction for part 050ce0004.
Shows OCR-detected values and compares with Excel file.
"""
import sys
import json
import logging
from pathlib import Path

# Add backend to path
backend_dir = Path(__file__).parent
sys.path.insert(0, str(backend_dir))

from app.services.rfq_autofill_service import RFQAutofillService
from app.services.geometry_scale_calibration import GeometryScaleCalibrationService

# Setup logging to see OCR extraction details
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s: %(message)s'
)

# Expected values from Excel file (row 8)
EXPECTED_EXCEL = {
    "finish_od_in": 1.24,
    "finish_id_in": 0.433,
    "finish_len_in": 0.63,
    "rm_od_in": 35/25.4,  # ~1.378
    "rm_len_in": 0.63 + 0.35,  # ~0.98
}

# Expected values from drawing (based on image)
EXPECTED_DRAWING = {
    "finish_od_in": 1.008,  # MAX from 1.006-1.008
    "finish_id_in": 0.443,
    "finish_len_in": 0.63,
}

def find_part_summary(part_no="050ce0004"):
    """Find part_summary.json file for this part."""
    jobs_dir = Path("data/jobs")
    if not jobs_dir.exists():
        return None, None
    
    for job_dir in jobs_dir.iterdir():
        if not job_dir.is_dir():
            continue
        
        summary_file = job_dir / "outputs" / "part_summary.json"
        if summary_file.exists():
            try:
                with open(summary_file, 'r') as f:
                    data = json.load(f)
                    part_no_in_file = data.get("part_no", "").lower()
                    if part_no_in_file == part_no.lower() or part_no.lower() in part_no_in_file:
                        return data, str(job_dir.name)
            except:
                continue
    return None, None

def test_ocr_extraction(part_summary, job_id=None):
    """Test OCR diameter extraction."""
    print("\n" + "="*80)
    print("TESTING OCR DIAMETER EXTRACTION")
    print("="*80)
    
    calibration_service = GeometryScaleCalibrationService()
    
    print(f"\nJob ID: {job_id}")
    print(f"Part Summary Keys: {list(part_summary.keys())[:10]}")
    
    # Check inference_metadata
    inference_meta = part_summary.get("inference_metadata", {})
    print(f"\nInference Metadata Keys: {list(inference_meta.keys()) if isinstance(inference_meta, dict) else 'N/A'}")
    
    if isinstance(inference_meta, dict):
        raw_dims = inference_meta.get("raw_dimensions", [])
        print(f"Raw Dimensions Count: {len(raw_dims) if isinstance(raw_dims, list) else 0}")
        if isinstance(raw_dims, list) and raw_dims:
            print("\nSample Raw Dimensions (first 5):")
            for idx, dim in enumerate(raw_dims[:5]):
                print(f"  [{idx}] text='{dim.get('text', '')}', value={dim.get('value')}, unit={dim.get('unit')}")
    
    # Extract OCR diameters
    print("\n" + "-"*80)
    print("Calling extract_ocr_diameters()...")
    print("-"*80)
    
    ocr_diameters = calibration_service.extract_ocr_diameters(part_summary, job_id=job_id)
    
    print(f"\nExtracted {len(ocr_diameters)} OCR diameter candidates:")
    for idx, dia in enumerate(ocr_diameters[:10]):
        print(f"  [{idx}] {dia.get('value', 0):.4f} in - text='{dia.get('text', '')[:50]}' - confidence={dia.get('confidence', 0):.3f}")
    
    return ocr_diameters

def test_calibration(part_summary, job_id=None):
    """Test calibration process."""
    print("\n" + "="*80)
    print("TESTING CALIBRATION PROCESS")
    print("="*80)
    
    calibration_service = GeometryScaleCalibrationService()
    
    calibrated_summary, scale_factor, confidence, ratios = calibration_service.calibrate_geometry_scale(
        part_summary,
        job_id=job_id
    )
    
    print(f"\nCalibration Results:")
    print(f"  scale_factor: {scale_factor}")
    print(f"  confidence: {confidence}")
    print(f"  ratios count: {len(ratios) if ratios else 0}")
    if ratios:
        print(f"  ratios: {[f'{r:.4f}' for r in ratios]}")
    
    if calibrated_summary:
        scale_report = calibrated_summary.get("scale_report", {})
        print(f"\nScale Report:")
        print(f"  method: {scale_report.get('method', 'unknown')}")
        print(f"  confidence: {scale_report.get('confidence', 'unknown')}")
        print(f"  scaled_xy: {scale_report.get('scaled_xy', False)}")
        print(f"  scaled_z: {scale_report.get('scaled_z', False)}")
    
    return calibrated_summary, scale_factor

def test_autofill_service(part_summary):
    """Test autofill service directly."""
    service = RFQAutofillService()
    
    tolerances = {
        "rm_od_allowance_in": 0.26,
        "rm_len_allowance_in": 0.35
    }
    
    try:
        result = service.autofill(
            part_no="050ce0004",
            part_summary_dict=part_summary,
            tolerances=tolerances,
            job_id=None,
            step_metrics=None,
            mode="GEOMETRY",
            cost_inputs=None,
            vendor_quote_mode=False
        )
        return result
    except Exception as e:
        print(f"ERROR: Autofill failed: {e}")
        import traceback
        traceback.print_exc()
        return None

def compare_results(result, expected_excel, expected_drawing):
    """Compare autofill results with expected values."""
    print("\n" + "="*80)
    print("COMPARISON: Autofill Results vs Expected Values")
    print("="*80)
    
    if not result:
        print("ERROR: No result to compare")
        return
    
    fields = result.fields
    debug = result.debug
    
    print("\n--- Finish Dimensions ---")
    finish_od = fields.finish_od_in.value
    finish_id = fields.finish_id_in.value
    finish_len = fields.finish_len_in.value
    
    print(f"\nFinish OD:")
    print(f"  Excel Expected:  {expected_excel['finish_od_in']:.4f}")
    print(f"  Drawing Expected: {expected_drawing['finish_od_in']:.4f}")
    finish_od_str = f"{finish_od:.4f}" if finish_od else "None"
    print(f"  Autofill Result: {finish_od_str}")
    if finish_od:
        excel_diff = abs(finish_od - expected_excel['finish_od_in'])
        drawing_diff = abs(finish_od - expected_drawing['finish_od_in'])
        excel_match = "OK" if excel_diff < 0.01 else "MISMATCH"
        drawing_match = "OK" if drawing_diff < 0.01 else "MISMATCH"
        print(f"  {excel_match} Diff from Excel: {excel_diff:.4f}")
        print(f"  {drawing_match} Diff from Drawing: {drawing_diff:.4f}")
    
    print(f"\nFinish ID:")
    print(f"  Excel Expected:  {expected_excel['finish_id_in']:.4f}")
    print(f"  Drawing Expected: {expected_drawing['finish_id_in']:.4f}")
    finish_id_str = f"{finish_id:.4f}" if finish_id else "None"
    print(f"  Autofill Result: {finish_id_str}")
    if finish_id:
        excel_diff = abs(finish_id - expected_excel['finish_id_in'])
        drawing_diff = abs(finish_id - expected_drawing['finish_id_in'])
        excel_match = "OK" if excel_diff < 0.01 else "MISMATCH"
        drawing_match = "OK" if drawing_diff < 0.01 else "MISMATCH"
        print(f"  {excel_match} Diff from Excel: {excel_diff:.4f}")
        print(f"  {drawing_match} Diff from Drawing: {drawing_diff:.4f}")
    
    print(f"\nFinish Length:")
    print(f"  Excel Expected:  {expected_excel['finish_len_in']:.4f}")
    print(f"  Drawing Expected: {expected_drawing['finish_len_in']:.4f}")
    finish_len_str = f"{finish_len:.4f}" if finish_len else "None"
    print(f"  Autofill Result: {finish_len_str}")
    if finish_len:
        excel_diff = abs(finish_len - expected_excel['finish_len_in'])
        drawing_diff = abs(finish_len - expected_drawing['finish_len_in'])
        excel_match = "OK" if excel_diff < 0.01 else "MISMATCH"
        drawing_match = "OK" if drawing_diff < 0.01 else "MISMATCH"
        print(f"  {excel_match} Diff from Excel: {excel_diff:.4f}")
        print(f"  {drawing_match} Diff from Drawing: {drawing_diff:.4f}")
    
    print("\n--- Raw Material Dimensions ---")
    rm_od = fields.rm_od_in.value
    rm_len = fields.rm_len_in.value
    
    print(f"\nRM OD:")
    print(f"  Excel Expected:  {expected_excel['rm_od_in']:.4f}")
    rm_od_str = f"{rm_od:.4f}" if rm_od else "None"
    print(f"  Autofill Result: {rm_od_str}")
    if rm_od:
        diff = abs(rm_od - expected_excel['rm_od_in'])
        match = "OK" if diff < 0.01 else "MISMATCH"
        print(f"  {match} Diff: {diff:.4f}")
    
    print(f"\nRM Length:")
    print(f"  Excel Expected:  {expected_excel['rm_len_in']:.4f}")
    rm_len_str = f"{rm_len:.4f}" if rm_len else "None"
    print(f"  Autofill Result: {rm_len_str}")
    if rm_len:
        diff = abs(rm_len - expected_excel['rm_len_in'])
        match = "OK" if diff < 0.01 else "MISMATCH"
        print(f"  {match} Diff: {diff:.4f}")
    
    print("\n--- Calibration Debug Info ---")
    print(f"scale_calibration_applied: {debug.scale_calibration_applied}")
    print(f"scale_factor_used: {debug.scale_factor_used}")
    print(f"matched_pairs: {debug.matched_pairs}")
    print(f"scaled_xy: {debug.scaled_xy}")
    print(f"scaled_z: {debug.scaled_z}")
    print(f"scale_method: {debug.scale_method}")
    
    print("\n--- Field Sources ---")
    print(f"finish_od source: {fields.finish_od_in.source}")
    print(f"finish_id source: {fields.finish_id_in.source}")
    print(f"finish_len source: {fields.finish_len_in.source}")

if __name__ == "__main__":
    print("="*80)
    print("TESTING PART 050CE0004 - OCR EXTRACTION & COMPARISON")
    print("="*80)
    
    # Find part_summary
    part_summary, job_id = find_part_summary("050ce0004")
    
    if not part_summary:
        print("\nWARNING: Could not find part_summary.json for part 050ce0004")
        print("Searching for any available part_summary...")
        jobs_dir = Path("data/jobs")
        if jobs_dir.exists():
            for job_dir in jobs_dir.iterdir():
                if not job_dir.is_dir():
                    continue
                summary_file = job_dir / "outputs" / "part_summary.json"
                if summary_file.exists():
                    try:
                        with open(summary_file, 'r') as f:
                            part_summary = json.load(f)
                            job_id = job_dir.name
                            print(f"\nUsing part_summary from: {summary_file}")
                            print(f"Part No in file: {part_summary.get('part_no', 'N/A')}")
                            print(f"Job ID: {job_id}")
                            break
                    except Exception as e:
                        print(f"Error reading {summary_file}: {e}")
                        continue
        
        if not part_summary:
            print("\nERROR: No part_summary.json found")
            sys.exit(1)
    else:
        print(f"\nFound part_summary for part 050ce0004")
        print(f"Job ID: {job_id}")
    
    # Show part_summary scale info
    scale_report = part_summary.get("scale_report", {})
    print(f"\nPart Summary Scale Info:")
    print(f"  method: {scale_report.get('method', 'unknown')}")
    print(f"  confidence: {scale_report.get('confidence', 'unknown')}")
    
    # Test OCR extraction
    ocr_diameters = test_ocr_extraction(part_summary, job_id=job_id)
    
    # Test calibration
    calibrated_summary, scale_factor = test_calibration(part_summary, job_id=job_id)
    
    # Use calibrated summary if available
    part_summary_to_use = calibrated_summary if calibrated_summary else part_summary
    
    # Test autofill
    print("\n" + "="*80)
    print("CALLING AUTOFILL SERVICE...")
    print("="*80)
    
    result = test_autofill_service(part_summary_to_use)
    
    if result:
        compare_results(result, EXPECTED_EXCEL, EXPECTED_DRAWING)
    else:
        print("\nERROR: Autofill service returned None")
