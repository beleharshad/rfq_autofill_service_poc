# RFQ 3D View Backend

FastAPI backend for manufacturing feature extraction.

## Setup

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run server
python run.py
```

## API Endpoints

### Health Check
```
GET /health
```

### Jobs

**POST /api/v1/jobs** - Create job and upload files
- Accepts: `multipart/form-data`
- Fields:
  - `files`: One or more PDF files, or a ZIP file containing PDFs
  - `name`: (optional) Job name
  - `description`: (optional) Job description
- Returns: Job with `job_id`, `status: "CREATED"`, and `input_files` list

**GET /api/v1/jobs** - List all jobs
- Returns: List of all jobs with status and file counts

**GET /api/v1/jobs/{job_id}** - Get job details
- Returns: Job with status, input_files, and output_files

**GET /api/v1/jobs/{job_id}/files** - List job files
- Returns: List of files with download URLs

**GET /api/v1/jobs/{job_id}/download?path=...** - Download file
- Safe download with path traversal protection
- Query param: `path` - relative path from job directory (e.g., `inputs/file.pdf`)

**DELETE /api/v1/jobs/{job_id}** - Delete job
- Deletes job and all associated files

## Storage Structure

```
backend/data/
├── jobs/
│   └── {job_id}/
│       ├── inputs/      # Uploaded PDFs
│       └── outputs/      # Generated results
└── jobs.db              # SQLite database
```

## Testing

```bash
# Run tests
pytest

# Run with coverage
pytest --cov=app tests/
```

## RFQ Processing

### Geometry Envelope System (v1)

The backend now uses a **Geometry Envelope** system as the source-of-truth for OD/Length dimensions in RFQ processing.

#### Key Changes (v1.0.0)

**BEFORE (Deprecated):**
- OCR-extracted dimensions from PDFs were used to override 3D geometry
- Vendor quote mode created synthetic part_summary from OCR data
- RFQ RM calculations could use inaccurate OCR dimensions

**AFTER (Current):**
- 3D geometry from part_summary.json is the authoritative source
- Deterministic envelope computation: finish → raw (stock) → RM dimensions
- OCR dimensions moved to `pdf_hint` fields (metadata only)
- Vendor quote mode affects rounding precision only, not geometry

#### API Endpoints

**POST /api/v1/rfq/envelope** - Compute geometry envelope
- Input: job_id or part_summary + allowances + rounding rules
- Output: finish_max_od_in, raw_max_od_in, finish_len_in, raw_len_in with confidence
- Status: AUTO_FILLED, NEEDS_REVIEW, or REJECTED with reasons

**POST /api/v1/rfq/autofill** - RFQ field computation (updated)
- RM dimensions now derived from RAW envelope values (not finish)
- Uses geometry envelope service internally for deterministic calculations

**POST /api/v1/rfq/vendor_quote_extract** - OCR extraction (updated)
- Returns only metadata fields: part_no, revision, material_grade, qty_moq
- OD/Length dimensions moved to `pdf_hint` (deprecated for RFQ use)
- No longer overrides geometry in RFQ processing

#### Migration Guide

1. **For Frontend Integration:**
   - Call `/api/v1/rfq/envelope` to get authoritative dimensions
   - Remove OCR dimension overrides from part_summary construction
   - Vendor quote mode checkbox affects rounding only

2. **For API Consumers:**
   - Use geometry envelope for dimension calculations
   - OCR hints available in `pdf_hint` fields if needed for reference
   - RM calculations now use RAW stock dimensions

3. **Data Flow:**
   ```
   3D Geometry → Geometry Envelope → RFQ Autofill → Excel Export
                ↓
   PDF OCR → Metadata Hints (no dimension override)
   ```

## Security Features

- Path traversal protection in file operations
- Filename sanitization
- Safe file download with path validation
- ZIP extraction with PDF-only filtering








