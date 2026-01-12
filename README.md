# RFQ 3D View

Web application for extracting manufacturing metrics from turned parts using engineering drawings and manual profile creation.

## Project Structure

```
RFQ_3D_View/
├── backend/          # FastAPI Python backend
├── frontend/         # React + TypeScript frontend
└── (existing Python modules for pipeline)
```

## Prerequisites

- **Python 3.9+** with pip
- **Node.js 18+** with npm
- **Git**

## Quick Start

### 1. Backend Setup

```bash
# Navigate to backend directory
cd backend

# Create virtual environment (recommended)
python -m venv venv

# Activate virtual environment
# On Windows:
venv\Scripts\activate
# On macOS/Linux:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run the server
python run.py
```

The backend API will be available at:
- **API**: http://localhost:8000
- **Health Check**: http://localhost:8000/health
- **API Docs**: http://localhost:8000/docs (Swagger UI)

### 2. Frontend Setup

```bash
# Navigate to frontend directory (in a new terminal)
cd frontend

# Install dependencies
npm install

# Run development server
npm run dev
```

The frontend will be available at:
- **App**: http://localhost:5173

### 3. Verify Setup

1. Open http://localhost:5173 in your browser
2. You should see the home page with "New Job" button
3. Click "New Job" to navigate to the job creation page
4. Check backend health: http://localhost:8000/health

## Development

### Backend

- **Entry Point**: `backend/app/main.py`
- **Run Script**: `backend/run.py` (with auto-reload)
- **Storage**: `backend/data/jobs/{job_id}/` (created automatically)

### Frontend

- **Entry Point**: `frontend/src/main.tsx`
- **Dev Server**: `npm run dev` (Vite with HMR)
- **Build**: `npm run build`

## API Endpoints

### Health Check
```
GET /health
```

### API Documentation
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## Project Status

**Current Phase**: Scaffolding Complete ✅

- ✅ Backend FastAPI structure
- ✅ Frontend React + Vite + TypeScript
- ✅ Basic routing
- ✅ CORS configuration
- ⏳ Business logic (next phase)

## Why We Do NOT Auto-Generate CAD Blindly

**Important Design Philosophy for Contributors**

This system intentionally does **not** automatically generate CAD files (STEP) from PDFs without human confirmation. Here's why:

### The Problem with PDFs

1. **PDFs are Ambiguous**
   - Engineering drawings are visual representations, not precise CAD models
   - Multiple interpretations of the same drawing are often valid
   - Scale, units, and reference dimensions may be unclear
   - Hidden features, tolerances, and manufacturing notes are not machine-readable

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
