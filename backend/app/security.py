"""
Internal API key security dependency.

All backend endpoints require the caller to present the shared secret via the
``X-API-Key`` request header.  The value must match the ``INTERNAL_API_KEY``
environment variable.  When the variable is not set the check is **skipped** in
development (so local ``uvicorn`` restarts keep working without extra config).

NOTE: the ``api_key`` query-parameter fallback has been intentionally removed.
All frontend requests (including SSE streams) now use ``fetch + ReadableStream``
which supports custom headers, so the key never needs to travel in a URL.

In production, ensure ``INTERNAL_API_KEY`` is set to a long random string and
that the same value is stored in the frontend ``VITE_INTERNAL_API_KEY`` env var.
"""

import os
import re
import time
import secrets
import threading
import collections
from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

_API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)

_INTERNAL_KEY: str | None = os.environ.get("INTERNAL_API_KEY")

# ---------------------------------------------------------------------------
# UUID validation
# ---------------------------------------------------------------------------
_UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
    re.IGNORECASE,
)

def validate_job_id(job_id: str) -> str:
    """Raise 400 if *job_id* is not a valid UUID v4 string.

    Call this at the top of any route that accepts a job_id path parameter.
    Prevents path-traversal, enumeration and injection via crafted IDs.
    """
    if not _UUID_RE.match(job_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid job ID format.",
        )
    return job_id


# ---------------------------------------------------------------------------
# In-memory sliding-window rate limiter (no external dependencies)
# ---------------------------------------------------------------------------
# Default: 60 requests per minute per client IP.
_RATE_LIMIT_REQUESTS: int = int(os.environ.get("RATE_LIMIT_REQUESTS", "60"))
_RATE_LIMIT_WINDOW_S: int = int(os.environ.get("RATE_LIMIT_WINDOW_S", "60"))

_rate_lock = threading.Lock()
# Maps client_ip -> deque of timestamps inside the current window
_rate_buckets: dict[str, collections.deque] = {}


def check_rate_limit(client_ip: str) -> None:
    """Raise 429 if *client_ip* has exceeded the configured request rate.

    Uses a sliding-window counter stored in-memory.  Safe for single-worker
    production deployments; for multi-worker use Redis instead.
    """
    now = time.monotonic()
    cutoff = now - _RATE_LIMIT_WINDOW_S

    with _rate_lock:
        bucket = _rate_buckets.setdefault(client_ip, collections.deque())
        # Evict timestamps outside the current window
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= _RATE_LIMIT_REQUESTS:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests — please slow down.",
                headers={"Retry-After": str(_RATE_LIMIT_WINDOW_S)},
            )
        bucket.append(now)


# ---------------------------------------------------------------------------
# API key check
# ---------------------------------------------------------------------------

def _load_key() -> str | None:
    """Re-read the key each call so hot-reload / dotenv changes take effect."""
    return os.environ.get("INTERNAL_API_KEY") or _INTERNAL_KEY


def require_api_key(
    header_key: str | None = Security(_API_KEY_HEADER),
) -> None:
    """FastAPI dependency — raises 401 when key is wrong or missing (production only).

    Accepts the secret via:
    - ``X-API-Key`` request header  (only accepted method — query param removed)
    """
    expected = _load_key()
    if not expected:
        # Key not configured → dev mode, skip check.
        return
    if not header_key or not secrets.compare_digest(header_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key.",
        )
