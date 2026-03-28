"""Development server runner."""

import os
from pathlib import Path

# Load .env from the backend directory (sibling of this file)
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())
    print(f"[run.py] Loaded env from {_env_file}")
else:
    print(f"[run.py] No .env file found at {_env_file} — set GOOGLE_API_KEY in environment")

import uvicorn

# Ensure data directory exists
os.makedirs("data/jobs", exist_ok=True)

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_dirs=["app"]
    )

