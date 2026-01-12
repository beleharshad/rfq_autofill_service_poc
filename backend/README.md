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

## Security Features

- Path traversal protection in file operations
- Filename sanitization
- Safe file download with path validation
- ZIP extraction with PDF-only filtering





