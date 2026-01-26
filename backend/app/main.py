"""FastAPI application entry point."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import health, jobs, profiles, pipeline, profile2d, pdf, manual, step_generation, rfq, envelope

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


@app.get("/")
async def root():
    """Root endpoint."""
    return {"message": "RFQ 3D View API", "version": "0.1.0"}

