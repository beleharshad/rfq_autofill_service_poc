"""FastAPI application entry point."""

from dotenv import load_dotenv
load_dotenv()

import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
_trace_fh = logging.FileHandler("_trace.log", mode="a", encoding="utf-8")
_trace_fh.setLevel(logging.INFO)
_trace_fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s"))
logging.getLogger().addHandler(_trace_fh)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import health, jobs, profiles, pipeline, profile2d, pdf, manual, step_generation, rfq, envelope, llm, llm_pdf, preview3d

app = FastAPI(
    title="RFQ 3D View API",
    description="API for manufacturing feature extraction from turned parts",
    version="0.1.0"
)

# CORS configuration for frontend development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],  # Vite default port
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(health.router, prefix="/api/v1", tags=["health"])
app.include_router(jobs.router, prefix="/api/v1/jobs", tags=["jobs"])
app.include_router(profiles.router, prefix="/api/v1", tags=["profiles"])
app.include_router(pipeline.router, prefix="/api/v1", tags=["pipeline"])
app.include_router(profile2d.router, prefix="/api/v1", tags=["profile2d"])
app.include_router(pdf.router, prefix="/api/v1", tags=["pdf"])
app.include_router(manual.router, prefix="/api/v1", tags=["manual"])
app.include_router(step_generation.router, prefix="/api/v1", tags=["step-generation"])
app.include_router(rfq.router)
app.include_router(envelope.router)
app.include_router(llm.router, prefix="/api/v1/llm", tags=["llm"])
app.include_router(llm_pdf.router, prefix="/api/v1/llm", tags=["llm-pdf"])
app.include_router(preview3d.router, prefix="/api/v1", tags=["3d-preview"])


@app.on_event("startup")
async def _clear_stuck_pending_stubs() -> None:
    """Convert any llm_analysis.json stubs still marked pending=true to error stubs.

    Background threads that write the final result are daemon threads tied to
    the uvicorn worker process.  When the dev-server hot-reloads (StatReload)
    the worker is restarted, killing all daemon threads and leaving the pending
    stub on disk forever.  This hook replaces them so the frontend polling can
    settle on an actionable state instead of spinning indefinitely.
    """
    import json as _json
    import logging as _logging
    from pathlib import Path as _Path

    _log = _logging.getLogger(__name__)
    jobs_root = _Path("data/jobs")
    if not jobs_root.exists():
        return

    _interrupted_stub = {
        "pending": False,
        "error": "LLM analysis was interrupted (server restarted). Click Auto-Detect to try again.",
        "error_type": "interrupted",
        "rate_limit_info": None,
        "extracted": {},
        "validation": {
            "recommendation": "REVIEW",
            "overall_confidence": 0.0,
            "fields": {},
            "cross_checks": [
                "LLM pipeline was interrupted by a server restart. "
                "Click Auto-Detect to run a fresh analysis."
            ],
        },
        "code_issues": [],
        "valid": False,
    }

    count = 0
    for stub_path in jobs_root.glob("*/outputs/llm_analysis.json"):
        try:
            data = _json.loads(stub_path.read_text(encoding="utf-8-sig"))
            if data.get("pending") is True:
                stub_path.write_text(_json.dumps(_interrupted_stub, indent=2), encoding="utf-8")
                count += 1
                _log.info("Cleared stuck pending stub: %s", stub_path)
        except Exception as exc:
            _log.warning("Failed to check/clear pending stub %s: %s", stub_path, exc)

    if count:
        _log.info("[startup] Cleared %d stuck pending LLM stub(s)", count)


@app.get("/")
async def root():
    """Root endpoint."""
    return {"message": "RFQ 3D View API", "version": "0.1.0"}

