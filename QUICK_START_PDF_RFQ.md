# From PDF Drawing to Priced RFQ in Under 60 Seconds

This guide walks through the complete end-to-end flow: upload an engineering drawing → AI extracts dimensions → fully-calculated Excel RFQ downloads to your machine.

---

## Before You Begin

| Requirement | Check |
|---|---|
| Backend running on port 8000 | `curl http://localhost:8000/health` |
| LLM key in `backend/.env` | `OPENAI_API_KEY=sk-...` or `GOOGLE_API_KEY=AIza...` |
| Tesseract on PATH (scanned PDFs) | `tesseract --version` |

First time? Run through the [README](README.md) setup (≈5 min).

---

## The 3-Step Flow

```
1. Upload PDF           →   Job created, file stored
2. Analyze with AI      →   LLM extracts OD, ID, Length, Material, Qty
3. Download Excel       →   Vendor-ready .xlsx with all formulas live
```

---

## Step 1 — Upload Your Drawing

1. Open **http://localhost:5173**
2. Click **New Job**
3. Drag and drop your PDF engineering drawing (or `.step` file)
4. The system creates a job and stores the file — you land on the **Job Page**

**Supported inputs:**
- Text-based PDFs (CAD-generated drawings) — fastest
- Scanned / image PDFs — OCR pipeline runs automatically (EasyOCR → Tesseract fallback)
- STEP / STP files — geometry extracted directly from 3D solid model

---

## Step 2 — AI Dimension Extraction

On the Job Page, open the **LLM Analysis** panel:

1. Click **Analyze with AI**
2. Processing takes 5–15 seconds — the pipeline runs OCR → LLM in the background
3. Result appears with per-field confidence:

| Field | Example Output | Confidence |
|---|---|---|
| Finish OD | `1.880 in` | High |
| Finish ID | `1.019 in` | High |
| Finish Length | `4.250 in` | Medium |
| Material | `EN8` | High |
| Quantity | `500` | High |

- **Green** — high confidence, use as-is
- **Amber** — review against the drawing before downloading
- Any field can be manually corrected in the panel before export

---

## Step 3 — Download the RFQ Excel

In the **Auto Convert Results** panel, click **Download Excel**.

The system:
- Injects LLM-extracted dimensions as authoritative values (they override any geometry estimate)
- Computes RM blank dimensions, weight, material cost, machining costs, markups, and pricing
- Fetches the **live exchange rate** and embeds it with source + timestamp
- Exports a `.xlsx` with every cost column backed by a live Excel formula

Open the file in Excel — **all cells recalculate on open**. Change any input (rate, quantity, exchange rate) and the entire sheet updates instantly.

### What the Excel contains

| Column | Value Source |
|---|---|
| Finish OD / ID / Length | LLM extraction (or geometry if no PDF) |
| MM conversions | Formula: `= Inch × 25.4` |
| RM OD | Formula: `= ROUND(OD + 0.1", 3)` — standard stock allowance |
| RM Stock Length | Formula: `= Length + 0.35"` — facing + parting allowance |
| RM Weight Kg | Formula: `= (OD² − ID²) × L × 0.785 × 7.86 / 1,000,000` |
| Material Cost | Formula: `= RM Rate × RM Weight` |
| Turning / VMC / Special | Computed from machining time rates |
| P&F, OH & Profit, Rejection | 3% / 15% / 2% of Sub Total |
| Price/Each (INR & Currency) | Divided by live exchange rate |
| MOQ Cost & Annual Potential | Qty-driven formula columns |

---

## Batch Processing via API

For high-volume RFQ batches, drive the pipeline programmatically:

```python
import requests, time

BASE = "http://localhost:8000/api/v1"

def pdf_to_rfq_excel(pdf_path: str, part_no: str, rfq_id: str) -> bytes:
    # 1. Upload
    with open(pdf_path, "rb") as f:
        job = requests.post(f"{BASE}/jobs", files={"file": f}).json()
    job_id = job["job_id"]

    # 2. Analyze
    requests.post(f"{BASE}/llm/jobs/{job_id}/llm-analyze")

    # 3. Poll (typical: 5–15 s)
    for _ in range(30):
        result = requests.get(f"{BASE}/llm/jobs/{job_id}/llm-analysis").json()
        if not result.get("pending"):
            break
        time.sleep(2)

    ext = result.get("extracted", {})

    # 4. Export
    payload = {
        "rfq_id": rfq_id,
        "part_no": part_no,
        "mode": "ENVELOPE",
        "vendor_quote_mode": True,
        "source": {"job_id": job_id, "part_summary": None, "step_metrics": None},
        "tolerances": {"rm_od_allowance_in": 0.10, "rm_len_allowance_in": 0.35},
        "cost_inputs": {
            "rm_rate_per_kg": 100.0,
            "currency": "USD",
            "qty_moq": ext.get("qty", 100),
            "annual_potential_qty": ext.get("qty", 100),
        },
        "dimension_overrides": {
            "finish_od_in":  ext.get("od_in"),
            "finish_id_in":  ext.get("id_in"),
            "finish_len_in": ext.get("length_in"),
        },
    }
    return requests.post(f"{BASE}/rfq/export_xlsx", json=payload).content


# Process a batch
parts = [
    ("drawings/050CE0004.pdf", "050CE0004"),
    ("drawings/050DZ0017.pdf", "050DZ0017"),
    ("drawings/060AB0033.pdf", "060AB0033"),
]

for pdf, part in parts:
    xlsx = pdf_to_rfq_excel(pdf, part, "RFQ-2025-01369")
    with open(f"output/RFQ_{part}.xlsx", "wb") as f:
        f.write(xlsx)
    print(f"✓  {part}  →  output/RFQ_{part}.xlsx")
```

50 parts. 50 Excel files. No manual spreadsheet work.

---

## Checking the Live Exchange Rate

```python
rate = requests.get("http://localhost:8000/api/v1/rfq/exchange_rate").json()
# {"rate": 83.5, "source": "live", "timestamp": "2026-03-28T10:15:00Z", "currency": "USD"}
print(f"1 USD = {rate['rate']} INR  ({rate['source']} · {rate['timestamp']})")
```

The rate is fetched from an external FX API, cached for 1 hour, and embedded in every exported Excel with source and timestamp — auditable by finance teams.

---

## Troubleshooting

| Symptom | Root cause | Fix |
|---|---|---|
| LLM Analysis shows empty dims | Missing API key | Add `OPENAI_API_KEY` or `GOOGLE_API_KEY` to `backend/.env` |
| "pending" never clears | Server restarted mid-job | Refresh page; backend clears stuck jobs on startup |
| Scanned PDF — no text extracted | Tesseract not on PATH | Install Tesseract; confirm with `tesseract --version` |
| Excel `RM Weight` looks wrong | Stale formula from template | Re-download; latest build always overwrites template formulas |
| Exchange rate stale | Cache not expired | Wait 1h or call `GET /api/v1/rfq/exchange_rate` to verify |
| Wrong OD in Excel | Geometry estimate used instead of LLM | Check LLM panel — ensure extraction succeeded before downloading |

---

## Interactive API Docs

http://localhost:8000/docs
   ```env
   OPENAI_API_KEY=sk-...       # OpenAI GPT-4o
   # --- OR ---
   GOOGLE_API_KEY=AIza...      # Google Gemini
   ```
3. **Tesseract** installed and on PATH (for image/scanned PDFs)
   Windows: https://github.com/UB-Mannheim/tesseract/wiki

---

## Option 1 — Web UI (Recommended)

### Step 1 — Create a job

1. Open **http://localhost:5173**
2. Click **New Job**
3. Upload your **PDF engineering drawing** (or STEP file)
4. Wait for the job to initialize

### Step 2 — LLM Analysis

Once the job page loads:

1. Go to the **LLM Analysis** panel
2. Click **Analyze with AI**
3. The pipeline will:
   - Extract text from the PDF (pdfplumber → EasyOCR fallback for scanned files)
   - Send to LLM (GPT-4o or Gemini) for structured dimension extraction
   - Return: `OD`, `ID`, `Length`, `Material`, `Quantity`, confidence scores
4. Review the extracted values — green = high confidence, amber = low confidence

### Step 3 — Download Excel

1. In the **Auto Convert Results** panel, click **Download Excel**
2. The system:
   - Passes the LLM-extracted dimensions as `dimension_overrides`
   - Fills the RFQ template with correct inch values (3 decimal places)
   - Injects Excel formulas for all calculated columns
   - Returns a `.xlsx` file ready to open in Excel
3. Open the file — all formula cells auto-recalculate on open

---

## Option 2 — API (Programmatic / Batch)

Useful for processing multiple parts from a script.

### Single PDF → Excel

```python
import requests

BASE = "http://localhost:8000/api/v1"

# 1. Create a job and upload the PDF
with open("your_drawing.pdf", "rb") as f:
    job = requests.post(f"{BASE}/jobs", files={"file": f}).json()

job_id = job["job_id"]
print(f"Job created: {job_id}")

# 2. Trigger LLM analysis
resp = requests.post(f"{BASE}/llm/jobs/{job_id}/llm-analyze")
print("LLM analysis started:", resp.status_code)

# 3. Poll for result (analysis runs in background)
import time
for _ in range(30):
    result = requests.get(f"{BASE}/llm/jobs/{job_id}/llm-analysis").json()
    if not result.get("pending"):
        break
    time.sleep(2)

extracted = result.get("extracted", {})
print("Extracted dimensions:", extracted)
# e.g. {'od_in': 1.88, 'id_in': 1.019, 'length_in': 4.25, 'material': 'EN8', 'qty': 500}

# 4. Export Excel with LLM overrides
export_payload = {
    "rfq_id": "RFQ-2025-01369",
    "part_no": "050CE0004",
    "mode": "ENVELOPE",
    "vendor_quote_mode": True,
    "source": {
        "job_id": job_id,
        "part_summary": None,
        "step_metrics": None
    },
    "tolerances": {
        "rm_od_allowance_in": 0.10,
        "rm_len_allowance_in": 0.35
    },
    "cost_inputs": {
        "rm_rate_per_kg": 100.0,
        "currency": "USD",
        "qty_moq": extracted.get("qty", 100),
        "annual_potential_qty": extracted.get("qty", 100)
    },
    "dimension_overrides": {
        "finish_od_in": extracted.get("od_in"),
        "finish_id_in": extracted.get("id_in"),
        "finish_len_in": extracted.get("length_in")
    }
}

xlsx = requests.post(f"{BASE}/rfq/export_xlsx", json=export_payload)
with open("RFQ_output.xlsx", "wb") as f:
    f.write(xlsx.content)

print("✅ Excel saved: RFQ_output.xlsx")
```

### Exchange Rate Check

```python
rate = requests.get(f"{BASE}/rfq/exchange_rate").json()
print(f"1 USD = {rate['rate']} INR  (source: {rate['source']})")
```

---

## What Gets Filled in Excel

| Column | Source |
|---|---|
| Finish OD (Inch) | LLM extracted `od_in` |
| Finish ID (Inch) | LLM extracted `id_in` |
| Finish Length (Inch) | LLM extracted `length_in` |
| Finish OD/ID/Length (MM) | Formula: `= Inch × 25.4` |
| RM OD (Inch) | Formula: `= ROUND(Finish_OD + 0.1, 3)` |
| RM ID (Inch) | Formula: `= IF(ID>0, ROUND(MAX(0, ID−0.05), 3), 0)` |
| RM Length (Inch) | Formula: `= Finish_Length + 0.35` |
| RM Weight Kg | Formula: `= density × (OD²−ID²) × Length` |
| Material Cost | Formula: `= RM Rate × RM Weight` |
| Sub Total | Formula: `= SUM(all cost columns)` |
| P&F | Formula: `= SubTotal × 3%` |
| OH & Profit | Formula: `= SubTotal × 15%` |
| Rejection | Formula: `= SubTotal × 2%` |
| Price/Each (INR) | Formula: `= SUM(SubTotal + markups)` |
| Price/Each (Currency) | Formula: `= Price_INR / Exchange_Rate` |
| MOQ Cost | Formula: `= Price/Each × Qty/MOQ` |

**All formulas recalculate live** — change any input cell and dependent columns update instantly.

---

## Troubleshooting

| Problem | Solution |
|---|---|
| LLM returns empty dimensions | Check `OPENAI_API_KEY` / `GOOGLE_API_KEY` in `backend/.env` |
| Scanned PDF, no text detected | Install Tesseract and ensure it's on PATH |
| Wrong OD/ID in Excel | Check the LLM Analysis panel — confirm extracted values before downloading |
| Excel shows `#VALUE!` | Re-download — the formula guard was likely triggered by a template cell |
| `RM Weight` shows wrong value | Re-download — old formula was replaced in the latest build |
| Exchange rate not updating | Backend caches FX rate for 1 hour; check `/api/v1/rfq/exchange_rate` |

---

## Key Files

| File | Purpose |
|---|---|
| `backend/app/services/pdf_llm_pipeline.py` | PDF → OCR → LLM → structured dims |
| `backend/app/services/llm_service.py` | OpenAI / Gemini LLM wrapper |
| `backend/app/services/rfq_excel_export_service.py` | Excel template fill + formula injection |
| `backend/app/services/currency_service.py` | Live FX rate with caching |
| `backend/app/api/llm_pdf.py` | `/llm-analyze`, `/llm-analysis` endpoints |
| `backend/app/api/rfq.py` | `/rfq/export_xlsx`, `/rfq/exchange_rate` endpoints |
| `frontend/src/components/LLMAnalysis/LLMAnalysisPanel.tsx` | LLM results UI |
| `frontend/src/components/AutoConvertResults/AutoConvertResults.tsx` | Download Excel button |

---

## Full API Docs

http://localhost:8000/docs




