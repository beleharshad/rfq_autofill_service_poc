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
import secrets
from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

_API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)

_INTERNAL_KEY: str | None = os.environ.get("INTERNAL_API_KEY")


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
            detail="Invalid or missing X-API-Key header",
        )
