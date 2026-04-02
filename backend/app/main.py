"""FastAPI application entry point."""

import os

from dotenv import load_dotenv
load_dotenv()

import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
_trace_fh = logging.FileHandler("_trace.log", mode="a", encoding="utf-8")
_trace_fh.setLevel(logging.INFO)
_trace_fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s"))
logging.getLogger().addHandler(_trace_fh)

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from app.api import health, jobs, profiles, pipeline, profile2d, pdf, manual, step_generation, rfq, envelope, llm, llm_pdf, preview3d
from app.security import require_api_key, check_rate_limit

# In production (PRODUCTION=true) disable the interactive Swagger/ReDoc UIs so
# the full API surface is not publicly browseable via /docs or /redoc.
_is_production = os.environ.get("PRODUCTION", "").lower() in ("1", "true", "yes")

app = FastAPI(
    title="RFQ 3D View API",
    description="API for manufacturing feature extraction from turned parts",
    version="0.1.0",
    docs_url=None if _is_production else "/docs",
    redoc_url=None if _is_production else "/redoc",
    openapi_url=None if _is_production else "/openapi.json",
)

# ---------------------------------------------------------------------------
# Request body size limit (50 MB) — rejects oversized uploads before they
# are buffered into memory.
# ---------------------------------------------------------------------------
_MAX_BODY_BYTES: int = int(os.environ.get("MAX_BODY_MB", "50")) * 1024 * 1024


class _BodySizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > _MAX_BODY_BYTES:
            return JSONResponse(
                status_code=413,
                content={"detail": f"Request body too large (max {_MAX_BODY_BYTES // (1024*1024)} MB)."},
            )
        return await call_next(request)


app.add_middleware(_BodySizeLimitMiddleware)

# ---------------------------------------------------------------------------
# Per-IP rate limiting — applied to all routes except /api/v1/health.
# ---------------------------------------------------------------------------

class _RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if not path.startswith("/api/v1/health"):
            client_ip = request.client.host if request.client else "unknown"
            try:
                check_rate_limit(client_ip)
            except Exception as exc:
                from fastapi import status as _status
                return JSONResponse(
                    status_code=_status.HTTP_429_TOO_MANY_REQUESTS,
                    content={"detail": str(exc)},
                    headers={"Retry-After": "60"},
                )
        return await call_next(request)

app.add_middleware(_RateLimitMiddleware)

# CORS configuration – expand with production origin when ALLOWED_ORIGINS env var is set.
_extra_origins = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"] + _extra_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*", "X-API-Key"],
)

# Shared security dependency – all non-health routes require a valid X-API-Key.
_auth = [Depends(require_api_key)]

# Include routers
app.include_router(health.router, prefix="/api/v1", tags=["health"])  # public – no auth
app.include_router(jobs.router, prefix="/api/v1/jobs", tags=["jobs"], dependencies=_auth)
app.include_router(profiles.router, prefix="/api/v1", tags=["profiles"], dependencies=_auth)
app.include_router(pipeline.router, prefix="/api/v1", tags=["pipeline"], dependencies=_auth)
app.include_router(profile2d.router, prefix="/api/v1", tags=["profile2d"], dependencies=_auth)
app.include_router(pdf.router, prefix="/api/v1", tags=["pdf"], dependencies=_auth)
app.include_router(manual.router, prefix="/api/v1", tags=["manual"], dependencies=_auth)
app.include_router(step_generation.router, prefix="/api/v1", tags=["step-generation"], dependencies=_auth)
app.include_router(rfq.router, dependencies=_auth)
app.include_router(envelope.router, dependencies=_auth)
app.include_router(llm.router, prefix="/api/v1/llm", tags=["llm"], dependencies=_auth)
app.include_router(llm_pdf.router, prefix="/api/v1/llm", tags=["llm-pdf"], dependencies=_auth)
app.include_router(preview3d.router, prefix="/api/v1", tags=["3d-preview"], dependencies=_auth)


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
        "error": "Analysis was interrupted (server restarted). Click Auto-Detect to try again.",
        "error_type": "interrupted",
        "rate_limit_info": None,
        "extracted": {},
        "validation": {
            "recommendation": "REVIEW",
            "overall_confidence": 0.0,
            "fields": {},
            "cross_checks": [
                "Analysis pipeline was interrupted by a server restart. "
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

