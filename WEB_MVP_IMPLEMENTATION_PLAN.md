# Web MVP Implementation Plan

## Overview
Build a web application where users upload a PDF engineering drawing (reference), manually create a revolve profile (via dimensions or sketching), and run the existing pipeline to generate manufacturing metrics.

## 1. Proposed Folder Structure

```
RFQ_3D_View/
├── backend/                          # Python FastAPI backend
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                   # FastAPI app entry point
│   │   ├── api/
│   │   │   ├── __init__.py
│   │   │   ├── jobs.py              # Job management endpoints
│   │   │   ├── profiles.py          # Profile creation/update endpoints
│   │   │   └── pipeline.py          # Pipeline execution endpoints
│   │   ├── models/
│   │   │   ├── __init__.py
│   │   │   ├── job.py               # Job data models
│   │   │   ├── profile.py           # Profile input models
│   │   │   └── response.py          # API response models
│   │   ├── services/
│   │   │   ├── __init__.py
│   │   │   ├── job_service.py      # Job CRUD operations
│   │   │   ├── profile_service.py  # Profile validation/conversion
│   │   │   └── pipeline_service.py # Pipeline orchestration
│   │   └── storage/
│   │       ├── __init__.py
│   │       ├── file_storage.py     # PDF/JSON file storage
│   │       └── job_storage.py      # Job metadata storage (SQLite)
│   ├── core/                        # Existing pipeline modules (moved/symlinked)
│   │   ├── geometry_2d.py
│   │   ├── revolved_solid_builder.py
│   │   └── feature_extractor.py
│   ├── requirements.txt
│   ├── .env                         # Environment variables
│   └── run.py                       # Development server
│
├── frontend/                        # React/TypeScript frontend
│   ├── public/
│   │   ├── index.html
│   │   └── favicon.ico
│   ├── src/
│   │   ├── components/
│   │   │   ├── PDFViewer/
│   │   │   │   ├── PDFViewer.tsx
│   │   │   │   └── PDFViewer.css
│   │   │   ├── ProfileBuilder/
│   │   │   │   ├── ProfileBuilder.tsx
│   │   │   │   ├── DimensionInput.tsx
│   │   │   │   ├── ProfileSketch.tsx
│   │   │   │   └── ProfileBuilder.css
│   │   │   ├── ResultsView/
│   │   │   │   ├── ResultsView.tsx
│   │   │   │   ├── MetricsTable.tsx
│   │   │   │   └── ResultsView.css
│   │   │   └── Layout/
│   │   │       ├── Header.tsx
│   │   │       └── Layout.tsx
│   │   ├── pages/
│   │   │   ├── HomePage.tsx
│   │   │   ├── JobPage.tsx
│   │   │   └── JobsListPage.tsx
│   │   ├── services/
│   │   │   ├── api.ts               # API client
│   │   │   └── types.ts             # TypeScript types
│   │   ├── hooks/
│   │   │   ├── useJob.ts
│   │   │   └── useProfile.ts
│   │   ├── App.tsx
│   │   ├── index.tsx
│   │   └── index.css
│   ├── package.json
│   ├── tsconfig.json
│   └── vite.config.ts              # Vite build config
│
├── storage/                         # Job artifacts (gitignored)
│   ├── pdfs/                        # Uploaded PDFs
│   │   └── {job_id}/
│   │       └── drawing.pdf
│   ├── json/                        # Generated JSON results
│   │   └── {job_id}/
│   │       └── part_summary.json
│   └── db/                          # SQLite database
│       └── jobs.db
│
├── tests/
│   ├── backend/
│   │   └── test_api.py
│   └── frontend/
│       └── (test files)
│
└── README.md
```

## 2. Minimal API Contract

### Base URL
```
http://localhost:8000/api/v1
```

### Endpoints

#### 1. Job Management

**POST `/jobs`** - Create new job
```json
Request:
{
  "name": "Part-001",
  "description": "Optional description"
}

Response:
{
  "job_id": "uuid-string",
  "name": "Part-001",
  "description": "Optional description",
  "status": "created",
  "created_at": "2026-01-03T19:00:00Z",
  "updated_at": "2026-01-03T19:00:00Z"
}
```

**GET `/jobs`** - List all jobs
```json
Response:
{
  "jobs": [
    {
      "job_id": "uuid-string",
      "name": "Part-001",
      "status": "completed",
      "created_at": "2026-01-03T19:00:00Z"
    }
  ]
}
```

**GET `/jobs/{job_id}`** - Get job details
```json
Response:
{
  "job_id": "uuid-string",
  "name": "Part-001",
  "status": "completed",
  "has_pdf": true,
  "has_profile": true,
  "has_results": true,
  "created_at": "2026-01-03T19:00:00Z",
  "updated_at": "2026-01-03T19:00:00Z"
}
```

**DELETE `/jobs/{job_id}`** - Delete job and all artifacts
```json
Response:
{
  "message": "Job deleted successfully"
}
```

#### 2. PDF Upload

**POST `/jobs/{job_id}/pdf`** - Upload PDF
```
Content-Type: multipart/form-data
Body: file (PDF file)

Response:
{
  "job_id": "uuid-string",
  "pdf_url": "/api/v1/jobs/{job_id}/pdf",
  "message": "PDF uploaded successfully"
}
```

**GET `/jobs/{job_id}/pdf`** - Download PDF
```
Response: PDF file (application/pdf)
```

#### 3. Profile Management

**POST `/jobs/{job_id}/profile`** - Create/update profile
```json
Request (Dimension-based):
{
  "type": "dimensions",
  "dimensions": [
    {
      "label": "L",
      "value": 4.25,
      "unit": "in"
    },
    {
      "label": "OD1",
      "value": 1.63,
      "unit": "in"
    },
    {
      "label": "OD2",
      "value": 0.806,
      "unit": "in"
    },
    {
      "label": "ID1",
      "value": 1.13,
      "unit": "in"
    },
    {
      "label": "ID2",
      "value": 0.753,
      "unit": "in"
    },
    {
      "label": "yS",
      "value": 3.27,
      "unit": "in"
    }
  ]
}

Request (Sketch-based):
{
  "type": "sketch",
  "points": [
    {"x": 0.565, "y": 0.0},
    {"x": 0.565, "y": 3.27},
    {"x": 0.3765, "y": 3.27},
    {"x": 0.3765, "y": 4.25},
    {"x": 0.403, "y": 4.25},
    {"x": 0.403, "y": 3.27},
    {"x": 0.815, "y": 3.27},
    {"x": 0.815, "y": 0.0}
  ],
  "unit": "in"
}

Response:
{
  "job_id": "uuid-string",
  "profile_type": "dimensions",
  "message": "Profile saved successfully"
}
```

**GET `/jobs/{job_id}/profile`** - Get profile
```json
Response:
{
  "type": "dimensions",
  "dimensions": [...],
  "or": {
    "type": "sketch",
    "points": [...],
    "unit": "in"
  }
}
```

#### 4. Pipeline Execution

**POST `/jobs/{job_id}/run`** - Execute pipeline
```json
Request:
{
  "tolerance": 1e-6  // optional, defaults to 1e-6
}

Response:
{
  "job_id": "uuid-string",
  "status": "running",
  "message": "Pipeline started"
}
```

**GET `/jobs/{job_id}/status`** - Get pipeline status
```json
Response:
{
  "job_id": "uuid-string",
  "status": "completed",  // "running", "completed", "failed"
  "progress": 100,
  "error": null
}
```

#### 5. Results

**GET `/jobs/{job_id}/results`** - Get results JSON
```json
Response:
{
  "job_id": "uuid-string",
  "results": {
    "schema_version": "0.1",
    "generated_at_utc": "...",
    "units": {...},
    "z_range": [...],
    "segments": [...],
    "totals": {...},
    "feature_counts": {...}
  }
}
```

**GET `/jobs/{job_id}/results/download`** - Download results JSON file
```
Response: JSON file (application/json)
```

## 3. UI Screens List

### Screen 1: Home / Jobs List
- **Purpose**: Landing page showing all jobs
- **Components**:
  - Header with "New Job" button
  - Table/list of jobs with:
    - Job name
    - Status (created, pdf_uploaded, profile_ready, completed)
    - Created date
    - Actions (view, delete)
  - Search/filter functionality

### Screen 2: Job Creation
- **Purpose**: Create new job
- **Components**:
  - Form: Job name, description (optional)
  - "Create Job" button
  - Redirects to Job Detail page

### Screen 3: Job Detail / Workflow
- **Purpose**: Main workflow page for a single job
- **Layout**: Split view
  - **Left Panel (50%)**: PDF Viewer
    - PDF.js integration
    - Zoom controls
    - Page navigation
    - Read-only display
  - **Right Panel (50%)**: Profile Builder
    - Tabs: "Dimensions" | "Sketch"
    - **Dimensions Tab**:
      - Form inputs for each dimension (L, OD1, OD2, ID1, ID2, yS)
      - Unit selector (inches/mm)
      - "Save Profile" button
    - **Sketch Tab**:
      - Canvas/SVG drawing area
      - Grid overlay
      - Point click to add vertices
      - Drag to move points
      - "Clear" and "Save Profile" buttons
    - Status indicator: "Profile ready" / "Profile incomplete"
    - "Run Pipeline" button (enabled when profile exists)
    - Results section (shown after pipeline completes)

### Screen 4: Results View
- **Purpose**: Display pipeline results
- **Components**:
  - Summary cards:
    - Total volume
    - Total surface area
    - Number of segments
    - Feature counts
  - Detailed metrics table:
    - Segment-by-segment breakdown
    - Volume, OD area, ID area per segment
  - Planar ring areas breakdown
  - "Download JSON" button
  - "View 3D Model" button (future enhancement)

## 4. Storage Strategy

### Job Artifacts Storage

**Option A: File System + SQLite (Recommended for MVP)**
```
storage/
├── pdfs/
│   └── {job_id}/
│       └── drawing.pdf
├── json/
│   └── {job_id}/
│       └── part_summary.json
└── db/
    └── jobs.db (SQLite)
```

**Database Schema (SQLite)**:
```sql
CREATE TABLE jobs (
    job_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL,  -- 'created', 'pdf_uploaded', 'profile_ready', 'completed', 'failed'
    profile_type TEXT,     -- 'dimensions' or 'sketch'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE profiles (
    job_id TEXT PRIMARY KEY,
    profile_data TEXT NOT NULL,  -- JSON string
    FOREIGN KEY (job_id) REFERENCES jobs(job_id)
);
```

**File Paths**:
- PDF: `storage/pdfs/{job_id}/drawing.pdf`
- JSON: `storage/json/{job_id}/part_summary.json`

**Benefits**:
- Simple, no external dependencies
- Easy backup (copy `storage/` folder)
- Fast for MVP
- Can migrate to S3/cloud storage later

**Alternative (Future)**: 
- S3/MinIO for production
- PostgreSQL for metadata
- Redis for job status/queuing

### Implementation Notes

1. **Job ID**: Use UUID v4 for all job IDs
2. **File Naming**: Use job_id as folder name to avoid collisions
3. **Cleanup**: Implement job deletion that removes:
   - Database record
   - PDF file
   - JSON file
   - Folder structure
4. **Backup**: Regular backup of `storage/` folder recommended

## 5. Technology Stack

### Backend
- **Framework**: FastAPI (Python 3.9+)
- **Database**: SQLite (MVP), PostgreSQL (future)
- **File Storage**: Local filesystem (MVP), S3 (future)
- **Dependencies**: 
  - Existing: geometry_2d, revolved_solid_builder, feature_extractor
  - New: fastapi, uvicorn, python-multipart, aiofiles

### Frontend
- **Framework**: React 18+ with TypeScript
- **Build Tool**: Vite
- **PDF Viewer**: react-pdf or pdf.js
- **Drawing**: Canvas API or SVG with react-konva
- **HTTP Client**: axios or fetch
- **State Management**: React Context or Zustand (simple)

## 6. Development Phases

### Phase 1: Backend Foundation
1. Set up FastAPI project structure
2. Implement job CRUD endpoints
3. Implement PDF upload/download
4. Set up SQLite database
5. Basic error handling

### Phase 2: Profile Management
1. Implement profile creation endpoints
2. Validate profile inputs
3. Convert profile to Profile2D format
4. Store profile data

### Phase 3: Pipeline Integration
1. Integrate existing pipeline modules
2. Implement pipeline execution endpoint
3. Add job status tracking
4. Generate and store JSON results

### Phase 4: Frontend Foundation
1. Set up React project
2. Create basic routing
3. Implement jobs list page
4. Implement job creation page

### Phase 5: PDF Viewer
1. Integrate PDF.js
2. Create PDF viewer component
3. Add zoom/navigation controls

### Phase 6: Profile Builder
1. Implement dimensions input form
2. Implement sketch canvas
3. Add profile validation
4. Connect to backend API

### Phase 7: Results Display
1. Create results view component
2. Display metrics tables
3. Add JSON download
4. Polish UI/UX

### Phase 8: Testing & Polish
1. End-to-end testing
2. Error handling improvements
3. UI/UX refinements
4. Documentation

## 7. API Error Responses

All endpoints return consistent error format:
```json
{
  "error": {
    "code": "ERROR_CODE",
    "message": "Human-readable error message",
    "details": {}  // Optional additional details
  }
}
```

Common error codes:
- `JOB_NOT_FOUND`
- `INVALID_PROFILE`
- `PIPELINE_FAILED`
- `FILE_TOO_LARGE`
- `INVALID_FILE_TYPE`

## 8. Security Considerations (MVP)

- File size limits (PDF: 10MB max)
- File type validation (PDF only)
- Input validation (dimensions, points)
- CORS configuration for frontend
- Rate limiting (future)

## 9. Deployment (Future)

- Docker containers for backend/frontend
- Docker Compose for local development
- Environment-based configuration
- Production deployment guide








