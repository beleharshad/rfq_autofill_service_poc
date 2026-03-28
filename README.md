# RFQ 3D View

Full-stack web application for automated RFQ (Request for Quotation) cost estimation of CNC-turned parts.  
Upload a **STEP file** or a **PDF engineering drawing** → the system extracts dimensions, detects features, estimates material/machining costs, and exports a filled Excel RFQ sheet — with live currency conversion.

---

## Features

| Feature | Status |
|---|---|
| STEP upload → geometry extraction → 3D preview | ✅ |
| PDF upload → LLM dimension extraction (OCR + AI) | ✅ |
| Auto-fill RFQ fields (OD, ID, Length, RM dims, weight) | ✅ |
| Excel export with live formulas (RM weight, costs, P&F, MOQ) | ✅ |
| Live currency exchange rate (USD → INR) | ✅ |
| Vendor Quote Mode (cost breakdown per part) | ✅ |
| 3D part viewer (GLB in-browser) | ✅ |
| Hole / slot / feature detection | ✅ |

---

## Project Structure

```
RFQ_3D_View/
├── backend/                  # FastAPI Python backend
│   ├── app/
│   │   ├── api/              # Route handlers (jobs, rfq, pdf, llm, preview3d …)
│   │   ├── models/           # Pydantic request/response models
│   │   ├── services/         # Business logic (geometry, LLM, Excel export …)
│   │   └── main.py           # App entry point
│   ├── data/
│   │   ├── jobs/             # Per-job working files (auto-created)
│   │   └── rfq_estimation/   # Excel templates
│   └── run.py                # Dev server launcher (uvicorn --reload)
├── frontend/                 # React + TypeScript + Vite frontend
│   └── src/
│       ├── components/       # AutoConvertResults, LLMAnalysis, LatheViewer …
│       ├── pages/            # JobPage, NewJobPage …
│       └── services/         # API client, types
└── requirements.txt          # Root-level Python deps (legacy pipeline)
```

---

## Prerequisites

| Requirement | Version |
|---|---|
| Python | 3.9 + |
| Node.js | 18 + |
| Tesseract OCR | any (for image-PDF fallback) |
| OpenAI **or** Google Gemini API key | — |

> **Tesseract install (Windows):** Download the installer from  
> https://github.com/UB-Mannheim/tesseract/wiki and ensure `tesseract` is on your PATH.

---

## Quick Start

### 1. Environment variables

Create `backend/.env` (copy from the template below):

```env
# LLM provider — set ONE of these
OPENAI_API_KEY=sk-...
GOOGLE_API_KEY=AIza...

# Optional — defaults shown
BACKEND_HOST=0.0.0.0
BACKEND_PORT=8000
```

### 2. Backend

```bash
cd backend

# Create & activate virtual environment
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS / Linux

# Install dependencies
pip install -r requirements.txt

# Start the server (auto-reloads on file changes)
python run.py
```

Backend URLs:
- **API base**: http://localhost:8000
- **Health check**: http://localhost:8000/health
- **Swagger UI**: http://localhost:8000/docs

### 3. Frontend

```bash
# In a second terminal
cd frontend

npm install
npm run dev
```

Open **http://localhost:5173** in your browser.

---

## Typical Workflow

### STEP File → RFQ Excel

1. Click **New Job** and upload a `.step` / `.stp` file.
2. The backend extracts geometry, detects features, and renders a 3D preview.
3. Click **Auto-fill RFQ** — dimensions are computed from the geometry envelope.
4. Review the pre-filled fields, adjust if needed.
5. Click **Download Excel** to get a fully-calculated RFQ spreadsheet.

### PDF Drawing → RFQ Excel

1. Click **New Job** and upload a PDF engineering drawing.
2. The LLM pipeline (OCR + AI) extracts OD, ID, Length, Material, and Quantity.
3. Extracted values appear in the **LLM Analysis** panel with confidence indicators.
4. Click **Download Excel** — LLM dimensions are injected directly, overriding geometry estimates.

> See [QUICK_START_PDF_RFQ.md](QUICK_START_PDF_RFQ.md) for a step-by-step walkthrough with screenshots.

---

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| GET | `/health` | Health check |
| POST | `/api/v1/jobs` | Create a new job |
| GET | `/api/v1/jobs` | List all jobs |
| GET | `/api/v1/jobs/{job_id}` | Get job details |
| GET | `/api/v1/jobs/{job_id}/files` | List job output files |
| GET | `/api/v1/jobs/{job_id}/download` | Download job file |
| DELETE | `/api/v1/jobs/{job_id}` | Delete job |
| POST | `/api/v1/jobs/{job_id}/llm-analyze` | Trigger LLM PDF analysis |
| GET | `/api/v1/jobs/{job_id}/llm-analysis` | Get LLM analysis result |
| GET | `/api/v1/jobs/{job_id}/llm-analysis/export-excel` | Export Excel from LLM result |
| GET | `/api/v1/jobs/{job_id}/3d-preview` | Serve GLB 3D model |
| POST | `/api/v1/rfq/autofill` | Auto-fill RFQ from geometry |
| POST | `/api/v1/rfq/export_xlsx` | Export filled RFQ Excel |
| GET | `/api/v1/rfq/exchange_rate` | Live USD → INR exchange rate |
| POST | `/api/v1/rfq/extract_pdf_specs` | Extract specs from a PDF file |
| POST | `/api/v1/rfq/autofill_from_pdf` | Autofill RFQ from PDF specs |

Full interactive docs: **http://localhost:8000/docs**

---

## Excel Export — Formula Notes

The exported Excel file uses the same formulas as the original RFQ template and recalculates automatically on open (`fullCalcOnLoad = True`):

| Column | Formula |
|---|---|
| Finish OD/ID/Length (MM) | `= Inch × 25.4` |
| RM OD (Inch) | `= ROUND(Finish_OD + 0.1, 3)` |
| RM ID (Inch) | `= IF(ID > 0, ROUND(MAX(0, ID − 0.05), 3), 0)` |
| Length (Inch) — RM stock | `= Finish_Length + 0.35` |
| RM Weight Kg | `= π/4 × (OD²−ID²) × Length × 7.86 g/cm³` (via 0.785 factor) |
| Material Cost | `= RM Rate × RM Weight` |
| Sub Total | `= SUM(Material + Roughing + Turning + VMC + Special + Others + Inspection)` |
| P&F | `= Sub Total × 3%` |
| OH & Profit | `= Sub Total × 15%` |
| Rejection Cost | `= Sub Total × 2%` |
| Price/Each (INR) | `= SUM(SubTotal + P&F + OH&Profit + Rejection)` |
| Price/Each (Currency) | `= Price_INR / Exchange_Rate` |
| MOQ Cost | `= Price/Each × Qty/MOQ` |
| Annual Potential | `= Price/Each × Annual Qty` |

Changing **any input cell** (OD, ID, Length, Rate, Qty, Exchange Rate) automatically cascades through all dependent columns.

---

## Development

### Running tests (backend)

```bash
cd backend
pytest tests/ -v
```

### Building the frontend

```bash
cd frontend
npm run build      # output → frontend/dist/
npm run preview    # serve the production build locally
```

### Key services

| File | Purpose |
|---|---|
| `backend/app/services/rfq_excel_export_service.py` | Excel template fill + formula injection |
| `backend/app/services/llm_service.py` | OpenAI / Gemini LLM integration |
| `backend/app/services/pdf_llm_pipeline.py` | PDF → OCR → LLM → structured dims |
| `backend/app/services/currency_service.py` | Live FX rate fetch + caching |
| `backend/app/services/geometry_envelope_service.py` | STEP geometry → OD/ID/Length |
| `frontend/src/components/AutoConvertResults/AutoConvertResults.tsx` | Main results + download UI |
| `frontend/src/components/LLMAnalysis/LLMAnalysisPanel.tsx` | LLM extraction results display |

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `Port 8000 already in use` | Change `BACKEND_PORT` in `.env` or `backend/run.py` |
| `Module not found` | Activate the virtual environment first |
| `CORS error in browser` | Check allowed origins in `backend/app/main.py` |
| `Port 5173 already in use` | Vite uses the next free port automatically |
| `npm install fails` | Run `npm cache clean --force` then retry |
| `LLM returns no dimensions` | Check `OPENAI_API_KEY` / `GOOGLE_API_KEY` in `.env` |
| `Tesseract not found` | Install Tesseract and add it to PATH |
| `RM Weight shows wrong value` | Ensure the Excel file is saved with `fullCalcOnLoad`; re-download |

---

## Design Philosophy — Human-in-the-Loop CAD

This system **never auto-generates STEP files without user confirmation**.  
PDFs are ambiguous; OCR and computer vision have inherent error rates.  
Every inference carries a confidence score; users review and approve before any CAD or cost export is committed.

```
Upload → Inference → Confidence Scoring → Human Review → Approval → Export
```

---

## License

[Your License Here]
2. **Dimensions Can Be Implied or Missing**
   - Critical dimensions may be implied through geometric relationships
   - Standard features (fillets, chamfers) may be shown but not dimensioned
   - Manufacturing notes may override explicit dimensions
   - Cross-references to other drawings or standards may be required

3. **Inference is Probabilistic, Not Deterministic**
   - Computer vision and OCR have inherent error rates
   - Confidence scores reflect uncertainty, not certainty
   - Low-confidence inferences can produce incorrect geometry
   - Multiple valid interpretations may exist for the same view

### Our Preferred Workflow

Our system follows a **human-in-the-loop** approach:

```
PDF Upload → View Detection → Inference → Confidence Scoring → Human Review → Confirmation → CAD Generation
```

**Key Principles:**

1. **Inference First**: We extract dimensions and geometry from PDFs using computer vision
2. **Confidence Scoring**: Every inference includes confidence scores (overall + per-segment)
3. **Human Confirmation**: Users must review and approve before CAD generation
4. **Safety Gates**: Automatic STEP generation is blocked if:
   - Overall confidence < 0.75
   - Any segment confidence < 0.5
   - More than 1 segment has "thin_wall" flag
5. **Manual Override**: Users can always switch to manual input mode

### Why This Matters

- **Prevents Bad CAD**: Blindly generating CAD from ambiguous PDFs creates incorrect models
- **Builds Trust**: Users see what the system inferred and can correct errors
- **Enables Learning**: Confidence scores help users understand system limitations
- **Maintains Quality**: Human review catches errors before downstream processes

### For Contributors

When adding new features:

- ✅ **DO**: Provide confidence scores for all inferences
- ✅ **DO**: Require explicit user confirmation for CAD generation
- ✅ **DO**: Show inference results with visualizations before export
- ✅ **DO**: Allow users to edit/correct inferred data
- ❌ **DON'T**: Auto-generate STEP files without user approval
- ❌ **DON'T**: Hide confidence scores or uncertainty
- ❌ **DON'T**: Assume PDFs contain complete, unambiguous information

**Remember**: A system that sometimes produces wrong CAD is worse than a system that requires human confirmation.

## Next Steps

1. Implement job CRUD endpoints
2. Add PDF upload functionality
3. Build profile creation UI
4. Integrate existing pipeline modules
5. Add results display

## Troubleshooting

### Backend Issues

- **Port 8000 already in use**: Change port in `backend/run.py`
- **Module not found**: Ensure virtual environment is activated
- **CORS errors**: Check CORS origins in `backend/app/main.py`

### Frontend Issues

- **Port 5173 already in use**: Vite will automatically use next available port
- **npm install fails**: Try `npm cache clean --force` then reinstall
- **TypeScript errors**: Run `npm run build` to see full error details

## License

[Your License Here]
