"""Health check endpoint."""

from fastapi import APIRouter
from app.utils.occ_available import check_occ_availability

router = APIRouter()


@router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "rfq-3d-view-api"}


@router.get("/health/occ")
async def occ_health_check():
    """Check OCC (OpenCASCADE) availability.
    
    Returns:
        Dictionary with:
            - occ_available: bool
            - backend: "OCP" | "pythonocc" | null
            - error: str | null
    """
    is_available, backend, error = check_occ_availability()
    
    return {
        "occ_available": is_available,
        "backend": backend,
        "error": error
    }

