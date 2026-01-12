"""Job data models."""

from datetime import datetime
from typing import List, Optional, Dict, Any
from enum import Enum
from pydantic import BaseModel, Field
from app.models.outputs import OutputsInfo


class JobStatus:
    """Job status constants."""
    CREATED = "CREATED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class JobMode(str, Enum):
    """Job processing mode."""
    ASSISTED_MANUAL = "assisted_manual"
    AUTO_CONVERT = "auto_convert"


class JobCreate(BaseModel):
    """Request model for creating a job."""
    name: Optional[str] = None
    description: Optional[str] = None


class JobResponse(BaseModel):
    """Response model for job."""
    job_id: str
    name: Optional[str] = None
    description: Optional[str] = None
    status: str
    mode: Optional[str] = Field(None, description="Job processing mode: assisted_manual or auto_convert")
    input_files: List[str] = []
    output_files: List[str] = []
    created_at: datetime
    updated_at: datetime
    outputs_info: Optional[OutputsInfo] = Field(None, description="Detailed information about output files")
    run_report: Optional[Dict[str, Any]] = Field(None, description="Run report summary")

    class Config:
        from_attributes = True


class FileInfo(BaseModel):
    """File information model."""
    path: str
    name: str
    size: int
    url: str


class JobModeRequest(BaseModel):
    """Request model for setting job mode."""
    mode: str = Field(..., description="Job processing mode: assisted_manual or auto_convert")

