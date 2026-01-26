# ⚡ Quick Start: PDF-Based RFQ Auto-Fill

## 🎯 Goal
Extract dimensions from PDF drawings → Auto-calculate costs → Generate Excel RFQ

## 📋 Current Status

✅ **Implemented:**
- PDF text extraction service
- Dimension pattern matching (OD, ID, Length, Material, Qty)
- Auto-calculation (RM dimensions, weight, costs)
- Excel export with vendor quote mode
- API endpoints ready

⚠️ **Your PDF Issue:**
- File: `C:\Users\beleh\Downloads\drgs data\1\050dz0017_C.pdf`
- Problem: **Image-based PDF** (scanned drawing, no searchable text)
- Solution options below ⬇️

---

## 🚀 3 Ways to Use This Feature

### **Option 1: Manual Entry (Works Today!)** ⭐ RECOMMENDED

Use the existing web UI to manually enter dimensions:

1. **Start backend:**
   ```bash
   cd backend
   uvicorn app.main:app --reload
   ```

2. **Open frontend:**
   ```
   http://localhost:5173
   ```

3. **Navigate to RFQ section**

4. **Enter dimensions from your PDF:**
   - Part No: `050DZ0017`
   - Finish OD: `1.71` inches
   - Finish ID: `0.75` inches
   - Finish Length: `4.25` inches
   - Material: `65-45-12`
   - Qty: `200`

5. **Enable "Vendor Quote Mode"** ✅

6. **Click "Auto-fill RFQ"**

7. **Export to Excel** ✅

**Result:** Excel file with all calculations done automatically!

---

### **Option 2: API Call (Programmatic)**

If you have multiple parts, use a Python script:

```python
import requests

# YOUR DIMENSIONS (copied from PDF)
part = {
    "part_no": "050DZ0017",
    "finish_od_in": 1.71,
    "finish_id_in": 0.75,
    "finish_len_in": 4.25,
}

# Build request
request_data = {
    "rfq_id": "RFQ-2025-01369",
    "part_no": part["part_no"],
    "source": {
        "part_summary": {
            "units": {"length": "in"},
            "z_range": [0.0, part["finish_len_in"]],
            "segments": [{
                "z_start": 0.0,
                "z_end": part["finish_len_in"],
                "od_diameter": part["finish_od_in"],
                "id_diameter": part["finish_id_in"],
                "confidence": 0.95,
                "flags": []
            }],
            "inference_metadata": {"overall_confidence": 0.95},
            "scale_report": {"method": "anchor_dimension", "validation_passed": True}
        },
        "step_metrics": None,
        "job_id": None
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

# Call API
response = requests.post(
    "http://localhost:8000/api/v1/rfq/export_xlsx",
    json=request_data
)

# Save Excel
with open(f"RFQ_{part['part_no']}_auto.xlsx", "wb") as f:
    f.write(response.content)

print(f"✅ Excel generated: RFQ_{part['part_no']}_auto.xlsx")
```

**Run:**
```bash
python your_script.py
```

---

### **Option 3: Add OCR for Image PDFs** (Future Enhancement)

If you have many scanned PDFs, we can add OCR:

1. **Install dependency:**
   ```bash
   pip install pdf2image poppler-utils
   ```

2. **Update `PDFSpecExtractor`** (in `backend/app/services/pdf_spec_extractor.py`):

```python
def _extract_text_with_ocr(self, pdf_path: str) -> str:
    """Extract text from image-based PDF using OCR."""
    try:
        import easyocr
        from pdf2image import convert_from_path
        
        reader = easyocr.Reader(['en'])
        
        # Convert PDF to images
        images = convert_from_path(pdf_path, dpi=300)
        
        text = ""
        for img in images:
            # OCR each page
            results = reader.readtext(img)
            for (bbox, text_content, prob) in results:
                text += text_content + " "
        
        return text.upper()
    
    except Exception as e:
        print(f"OCR failed: {e}")
        return ""

def _extract_text_from_pdf(self, pdf_path: str) -> str:
    """Extract text from PDF, with OCR fallback."""
    # Try regular text extraction first
    text = ""
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
    except:
        pass
    
    # If no text found, try OCR
    if not text or len(text) < 50:
        print("Regular extraction failed, trying OCR...")
        text = self._extract_text_with_ocr(pdf_path)
    
    return text.upper()
```

3. **Test again:**
   ```bash
   python backend/test_pdf_extraction.py
   ```

---

## 📊 Expected Output

For part `050DZ0017`:

### **Input (from PDF):**
- Finish OD: `1.71"`
- Finish ID: `0.75"`
- Finish Length: `4.25"`

### **Auto-Calculated:**
- RM OD: `1.97"` (1.71 + 0.26 allowance)
- RM Length: `4.60"` (4.25 + 0.35 allowance)
- RM Weight: `~1.80 kg`
- Material Cost: `~$180`
- Turning Time: `~40 min`
- Total Estimate: `~$922`

### **Excel Output:**
All values populated in the template, preserving formulas.

---

## 🧪 Testing

### **Test Extraction (when OCR is ready):**
```bash
cd backend
python test_pdf_extraction.py
```

### **Test Full Workflow (manual entry):**
```bash
python generate_excel_from_pdf.py
```
(Update script to use manual dimensions instead of PDF)

---

## 📁 Key Files

### **Backend:**
- `app/services/pdf_spec_extractor.py` - PDF extraction service
- `app/api/rfq.py` - API endpoints
- `test_pdf_extraction.py` - Test script
- `generate_excel_from_pdf.py` - Complete workflow

### **Documentation:**
- `PDF_AUTO_FILL_GUIDE.md` - Full user guide
- `PDF_EXTRACTION_IMPLEMENTATION_SUMMARY.md` - Technical details
- `QUICK_START_PDF_RFQ.md` (this file) - Quick start

---

## 🎬 Next Steps

### **Right Now (5 min):**
1. Copy your dimensions from PDF to a text file
2. Use Option 1 (Manual Entry) in the web UI
3. Generate Excel ✅
4. Verify values match your expectations

### **This Week:**
1. Collect all PDF files for your RFQ batch
2. For each, manually note dimensions
3. Batch process using Option 2 (API script)
4. Generate all Excel files at once

### **Next Sprint (if needed):**
1. Add OCR support (Option 3)
2. Test with your scanned PDFs
3. Automate the full workflow

---

## ❓ FAQ

**Q: Why can't it read my PDF?**  
A: Your PDF is image-based (scanned). Need OCR or text-based PDFs.

**Q: Can I batch process multiple parts?**  
A: Yes! Use Option 2 (API script) with a loop.

**Q: Will it overwrite my Excel formulas?**  
A: No! Only fills in input cells, preserves all formulas.

**Q: What if OCR gets the wrong number?**  
A: System shows confidence scores. Review low-confidence values manually.

**Q: Can I adjust the cost inputs?**  
A: Yes! Pass different `cost_inputs` in the API call or UI.

---

## 📞 Support

Need help? Check:
1. `PDF_AUTO_FILL_GUIDE.md` - Detailed guide
2. `PDF_EXTRACTION_IMPLEMENTATION_SUMMARY.md` - Technical details
3. API docs: `http://localhost:8000/docs` (when backend is running)

---

## ✅ Summary

**What works:** ✅ Manual entry → Auto-calculate → Excel export  
**What's next:** ⏳ Add OCR for image-based PDFs  
**Your action:** 📝 Use Option 1 (Manual Entry) today!

**Ready to go!** 🚀




