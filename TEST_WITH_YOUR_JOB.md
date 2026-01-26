# 🚀 Test PDF Extraction with Your Uploaded Job

## Your Job Details
- **Job ID:** `53f4afc4-10d2-4cdb-864e-fc2777472707`
- **PDF File:** `inputs/050dz0017_C.pdf`
- **Status:** Job created and PDF uploaded ✅

---

## ✨ **Option 1: Extract Specs from Your Job (Easiest)**

### **API Call:**
```bash
curl -X POST "http://localhost:8000/api/v1/rfq/extract_pdf_from_job?job_id=53f4afc4-10d2-4cdb-864e-fc2777472707"
```

### **Expected Response:**
```json
{
  "success": true,
  "job_id": "53f4afc4-10d2-4cdb-864e-fc2777472707",
  "rfq_id": "53f4afc4-10d2-4cdb-864e-fc2777472707",
  "pdf_filename": "050dz0017_C.pdf",
  "pdf_path": "data/jobs/53f4afc4-10d2-4cdb-864e-fc2777472707/inputs/050dz0017_C.pdf",
  "extracted_specs": {
    "part_no": "050DZ0017",
    "finish_od_in": 1.71,
    "finish_id_in": 0.75,
    "finish_len_in": 4.25,
    "material_grade": "65-45-12",
    "qty_moq": 200,
    "revision": "C",
    "confidence": {
      "part_no": 0.95,
      "finish_od_in": 0.85,
      "finish_id_in": 0.75,
      "finish_len_in": 0.85,
      "overall": 0.85
    }
  },
  "raw_text_preview": "..."
}
```

---

## 🎯 **Option 2: Full Workflow - Extract + AutoFill + Excel**

### **Test Script:**

Create file: `test_your_job.py`

```python
import requests
import json

# Your job details
JOB_ID = "53f4afc4-10d2-4cdb-864e-fc2777472707"
API_BASE = "http://localhost:8000/api/v1"

print("=" * 80)
print("TESTING PDF EXTRACTION FROM YOUR UPLOADED JOB")
print("=" * 80)
print(f"\nJob ID: {JOB_ID}\n")

# Step 1: Extract specs from job PDF
print("[Step 1] Extracting specs from PDF...")
extract_response = requests.post(
    f"{API_BASE}/rfq/extract_pdf_from_job",
    params={"job_id": JOB_ID}
)

if extract_response.status_code != 200:
    print(f"[ERROR] Failed to extract: {extract_response.text}")
    exit(1)

extract_data = extract_response.json()
print("[OK] Extracted specs:")
print(json.dumps(extract_data["extracted_specs"], indent=2))

# Step 2: Use extracted specs for RFQ autofill
print("\n[Step 2] Running RFQ autofill...")
specs = extract_data["extracted_specs"]

# Build part_summary from extracted specs
part_summary = {
    "part_no": specs.get("part_no", "UNKNOWN"),
    "units": {"length": "in"},
    "z_range": [0.0, specs.get("finish_len_in", 0.0)],
    "segments": [{
        "z_start": 0.0,
        "z_end": specs.get("finish_len_in", 0.0),
        "od_diameter": specs.get("finish_od_in", 0.0),
        "id_diameter": specs.get("finish_id_in", 0.0),
        "confidence": specs.get("confidence", {}).get("overall", 0.85),
        "flags": []
    }],
    "inference_metadata": {
        "overall_confidence": specs.get("confidence", {}).get("overall", 0.85),
        "source": "pdf_extraction"
    },
    "scale_report": {
        "method": "anchor_dimension",
        "validation_passed": True
    }
}

autofill_request = {
    "rfq_id": JOB_ID,
    "part_no": specs.get("part_no", "UNKNOWN"),
    "source": {
        "job_id": JOB_ID,
        "part_summary": part_summary,
        "step_metrics": None
    },
    "tolerances": {
        "rm_od_allowance_in": 0.26,
        "rm_len_allowance_in": 0.35
    },
    "mode": "ENVELOPE",
    "vendor_quote_mode": True,
    "cost_inputs": {
        "rm_rate_per_kg": 100.0,
        "turning_rate_per_min": 7.5,
        "roughing_cost": 162.0,
        "inspection_cost": 10.0,
        "material_density_kg_m3": 7200.0
    }
}

autofill_response = requests.post(
    f"{API_BASE}/rfq/autofill",
    json=autofill_request
)

if autofill_response.status_code != 200:
    print(f"[ERROR] Autofill failed: {autofill_response.text}")
    exit(1)

autofill_data = autofill_response.json()
print("[OK] Autofill completed:")
print(f"  Part No: {autofill_data['part_no']}")
print(f"  Status: {autofill_data['status']}")
print(f"  Finish OD: {autofill_data['fields']['finish_od_in']['value']:.3f} in")
print(f"  Finish Length: {autofill_data['fields']['finish_len_in']['value']:.3f} in")
print(f"  RM OD: {autofill_data['fields']['rm_od_in']['value']:.2f} in")
print(f"  RM Length: {autofill_data['fields']['rm_len_in']['value']:.2f} in")

if autofill_data.get('estimate'):
    est = autofill_data['estimate']
    print(f"  RM Weight: {est['rm_weight_kg']['value']:.2f} kg")
    print(f"  Total Cost: ${est['total_estimate']['value']:.2f}")

# Step 3: Export to Excel
print("\n[Step 3] Exporting to Excel...")
export_response = requests.post(
    f"{API_BASE}/rfq/export_xlsx",
    json=autofill_request
)

if export_response.status_code != 200:
    print(f"[ERROR] Export failed: {export_response.text}")
    exit(1)

# Save Excel file
output_filename = f"RFQ_{specs.get('part_no', 'UNKNOWN')}_autofilled.xlsx"
with open(output_filename, "wb") as f:
    f.write(export_response.content)

print(f"[OK] Excel exported: {output_filename}")

print("\n" + "=" * 80)
print("SUCCESS! ✅")
print("=" * 80)
print(f"\n📄 PDF: data/jobs/{JOB_ID}/inputs/050dz0017_C.pdf")
print(f"📊 Excel: {output_filename}")
print("\nNext steps:")
print("1. Open the Excel file")
print("2. Review the auto-filled values")
print("3. Adjust if needed")
print()
```

### **Run Test:**
```bash
python test_your_job.py
```

---

## 🎨 **Option 3: Add "Extract Specs" Button to Frontend**

Add to your job detail page (`/jobs/{job_id}`):

```typescript
// In your job detail component
const handleExtractSpecs = async () => {
  try {
    setLoading(true);
    
    // Extract specs from PDF
    const extractResponse = await fetch(
      `/api/v1/rfq/extract_pdf_from_job?job_id=${jobId}`,
      { method: 'POST' }
    );
    
    const extractData = await extractResponse.json();
    
    if (!extractData.success) {
      alert('Failed to extract specs from PDF');
      return;
    }
    
    const specs = extractData.extracted_specs;
    
    // Show extracted specs to user
    alert(`Extracted from PDF:
Part No: ${specs.part_no}
Finish OD: ${specs.finish_od_in}"
Finish ID: ${specs.finish_id_in}"
Finish Length: ${specs.finish_len_in}"
Material: ${specs.material_grade}
Confidence: ${(specs.confidence.overall * 100).toFixed(0)}%`);
    
    // Now auto-fill RFQ with extracted specs...
    const autofillResponse = await api.rfqAutofillForJob({
      rfq_id: jobId,
      job_id: jobId,
      part_no: specs.part_no,
      mode: 'ENVELOPE',
      tolerances: {
        rm_od_allowance_in: 0.26,
        rm_len_allowance_in: 0.35
      },
      cost_inputs: {
        rm_rate_per_kg: 100.0,
        turning_rate_per_min: 7.5,
        roughing_cost: 162.0,
        inspection_cost: 10.0,
        material_density_kg_m3: 7200.0
      },
      vendor_quote_mode: true
    });
    
    // Display results...
    setRfqResult(autofillResponse);
    
  } catch (error) {
    console.error('Extract failed:', error);
    alert('Error extracting specs');
  } finally {
    setLoading(false);
  }
};

// Add button to UI
<button onClick={handleExtractSpecs}>
  📄 Extract Specs from PDF
</button>
```

---

## ⚡ **Quick Test with curl**

```bash
# 1. Extract specs
curl -X POST "http://localhost:8000/api/v1/rfq/extract_pdf_from_job?job_id=53f4afc4-10d2-4cdb-864e-fc2777472707" | json_pp

# 2. If your PDF is image-based (scanned), it will fail with:
# {"success": false, "error": "Could not extract text from PDF"}

# In that case, you'll need:
# - OCR support (add to PDFSpecExtractor), OR
# - Use manual entry with the existing UI
```

---

## 🔍 **Troubleshooting**

### **If extraction fails:**

1. **Check if PDF is in job directory:**
   ```bash
   ls backend/data/jobs/53f4afc4-10d2-4cdb-864e-fc2777472707/inputs/
   ```
   Should show: `050dz0017_C.pdf`

2. **Check if PDF has text (not just image):**
   - Open PDF in Adobe Reader
   - Try to select/copy text with mouse
   - If you CAN'T select text → PDF is image-based (needs OCR)

3. **If image-based PDF:**
   - See `PDF_EXTRACTION_IMPLEMENTATION_SUMMARY.md` Option 2 for OCR setup
   - OR use manual entry (already working in your UI)

---

## 📊 **Expected Excel Output**

After running the workflow, you'll get an Excel file with:

| Field | PDF Extracted | Calculated | Excel Column |
|-------|---------------|------------|--------------|
| Part No | `050DZ0017` | - | A |
| Finish OD | `1.71"` | - | N |
| Finish ID | `0.75"` | - | P |
| Finish Length | `4.25"` | - | R |
| RM OD | - | `1.97"` | T |
| RM Length | - | `4.60"` | V |
| RM Weight | - | `~1.80 kg` | W |
| Material Cost | - | `~$180` | Y |
| Turning Cost | - | `~$303` | AB |
| **Total** | - | `~$922` | AI |

---

## ✅ **Next Steps**

1. **Test extraction with your job:**
   ```bash
   python test_your_job.py
   ```

2. **If it works:** Celebrate! 🎉 You have PDF → Excel automation!

3. **If it fails (image PDF):** Two options:
   - Add OCR support (~30 min work)
   - Use manual entry (already working)

4. **Integrate into UI:** Add "Extract Specs" button to job page

---

**Ready to test?** Run the test script and let me know the results! 🚀




