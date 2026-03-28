"""
Test script to compare autofill results for part 050ce0004 with Excel file values.
"""
import sys
import json
import requests
from pathlib import Path

# Expected values from Excel file
EXPECTED_VALUES = {
    "finish_od_in": 1.24,
    "finish_id_in": 0.433,
    "finish_len_in": 0.63,
    "rm_od_in": 35/25.4,  # ~1.378
    "rm_len_in": 0.63 + 0.35,  # ~0.98
}

# Expected values from drawing (based on image)
DRAWING_VALUES = {
    "finish_od_in": 1.008,  # MAX from 1.006-1.008
    "finish_id_in": 0.443,
    "finish_len_in": 0.63,
}

def find_job_with_part(part_no="050ce0004"):
    """Find job_id that contains this part number."""
    jobs_dir = Path("data/jobs")
    if not jobs_dir.exists():
        return None
    
    for job_dir in jobs_dir.iterdir():
        if not job_dir.is_dir():
            continue
        
        summary_file = job_dir / "outputs" / "part_summary.json"
        if summary_file.exists():
            try:
                with open(summary_file, 'r') as f:
                    data = json.load(f)
                    if data.get("part_no", "").lower() == part_no.lower():
                        return job_dir.name
            except:
                continue
    return None

def test_autofill(job_id=None, part_summary=None):
    """Test autofill endpoint."""
    url = "http://localhost:8000/api/v1/rfq/autofill"
    
    request_data = {
        "rfq_id": "test-rfq-050ce0004",
        "part_no": "050ce0004",
        "mode": "GEOMETRY",
        "vendor_quote_mode": False,
        "source": {},
        "tolerances": {
            "rm_od_allowance_in": 0.26,
            "rm_len_allowance_in": 0.35
        },
        "cost_inputs": None
    }
    
    if job_id:
        request_data["source"]["job_id"] = job_id
    elif part_summary:
        request_data["source"]["part_summary"] = part_summary
    else:
        print("ERROR: Need either job_id or part_summary")
        return None
    
    try:
        response = requests.post(url, json=request_data, timeout=30)
        if response.status_code == 200:
            return response.json()
        else:
            print(f"API Error: {response.status_code}")
            print(response.text)
            return None
    except Exception as e:
        print(f"Request failed: {e}")
        return None

def compare_results(result, expected_excel, expected_drawing):
    """Compare autofill results with expected values."""
    print("\n" + "="*80)
    print("COMPARISON: Autofill Results vs Expected Values")
    print("="*80)
    
    if not result:
        print("ERROR: No result to compare")
        return
    
    fields = result.get("fields", {})
    debug = result.get("debug", {})
    
    print("\n--- Finish Dimensions ---")
    finish_od = fields.get("finish_od_in", {}).get("value")
    finish_id = fields.get("finish_id_in", {}).get("value")
    finish_len = fields.get("finish_len_in", {}).get("value")
    
    print(f"Finish OD:")
    print(f"  Excel Expected:  {expected_excel['finish_od_in']:.4f}")
    print(f"  Drawing Expected: {expected_drawing['finish_od_in']:.4f}")
    print(f"  Autofill Result: {finish_od:.4f if finish_od else 'None'}")
    if finish_od:
        excel_diff = abs(finish_od - expected_excel['finish_od_in'])
        drawing_diff = abs(finish_od - expected_drawing['finish_od_in'])
        print(f"  Diff from Excel: {excel_diff:.4f}")
        print(f"  Diff from Drawing: {drawing_diff:.4f}")
    
    print(f"\nFinish ID:")
    print(f"  Excel Expected:  {expected_excel['finish_id_in']:.4f}")
    print(f"  Drawing Expected: {expected_drawing['finish_id_in']:.4f}")
    print(f"  Autofill Result: {finish_id:.4f if finish_id else 'None'}")
    if finish_id:
        excel_diff = abs(finish_id - expected_excel['finish_id_in'])
        drawing_diff = abs(finish_id - expected_drawing['finish_id_in'])
        print(f"  Diff from Excel: {excel_diff:.4f}")
        print(f"  Diff from Drawing: {drawing_diff:.4f}")
    
    print(f"\nFinish Length:")
    print(f"  Excel Expected:  {expected_excel['finish_len_in']:.4f}")
    print(f"  Drawing Expected: {expected_drawing['finish_len_in']:.4f}")
    print(f"  Autofill Result: {finish_len:.4f if finish_len else 'None'}")
    if finish_len:
        excel_diff = abs(finish_len - expected_excel['finish_len_in'])
        drawing_diff = abs(finish_len - expected_drawing['finish_len_in'])
        print(f"  Diff from Excel: {excel_diff:.4f}")
        print(f"  Diff from Drawing: {drawing_diff:.4f}")
    
    print("\n--- Raw Material Dimensions ---")
    rm_od = fields.get("rm_od_in", {}).get("value")
    rm_len = fields.get("rm_len_in", {}).get("value")
    
    print(f"RM OD:")
    print(f"  Excel Expected:  {expected_excel['rm_od_in']:.4f}")
    print(f"  Autofill Result: {rm_od:.4f if rm_od else 'None'}")
    if rm_od:
        diff = abs(rm_od - expected_excel['rm_od_in'])
        print(f"  Diff: {diff:.4f}")
    
    print(f"\nRM Length:")
    print(f"  Excel Expected:  {expected_excel['rm_len_in']:.4f}")
    print(f"  Autofill Result: {rm_len:.4f if rm_len else 'None'}")
    if rm_len:
        diff = abs(rm_len - expected_excel['rm_len_in'])
        print(f"  Diff: {diff:.4f}")
    
    print("\n--- Calibration Debug Info ---")
    print(f"scale_calibration_applied: {debug.get('scale_calibration_applied', False)}")
    print(f"scale_factor_used: {debug.get('scale_factor_used', None)}")
    print(f"matched_pairs: {debug.get('matched_pairs', 0)}")
    print(f"scaled_xy: {debug.get('scaled_xy', None)}")
    print(f"scaled_z: {debug.get('scaled_z', None)}")
    print(f"scale_method: {debug.get('scale_method', 'unknown')}")

if __name__ == "__main__":
    print("="*80)
    print("TESTING PART 050CE0004 AUTOFILL")
    print("="*80)
    
    # Try to find job_id
    job_id = find_job_with_part("050ce0004")
    if job_id:
        print(f"\nFound job_id: {job_id}")
        result = test_autofill(job_id=job_id)
    else:
        print("\nNo job_id found. Searching for any job with part_summary...")
        # Try first available job
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
                            print(f"Using job_id: {job_dir.name}")
                            result = test_autofill(job_id=job_dir.name)
                            break
                    except:
                        continue
    
    if result:
        compare_results(result, EXPECTED_VALUES, DRAWING_VALUES)
    else:
        print("\nERROR: Could not get autofill result")
        print("Make sure:")
        print("1. Backend server is running on http://localhost:8000")
        print("2. A job exists with part_summary.json")
        print("3. The part_summary contains valid geometry data")
