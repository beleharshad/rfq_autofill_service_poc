"""Development server runner."""

import os
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

