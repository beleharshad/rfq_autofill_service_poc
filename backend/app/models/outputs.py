"""Output file information models."""

from typing import Optional
from pydantic import BaseModel, Field


class OutputFileInfo(BaseModel):
    """Information about an output file."""
    exists: bool = Field(..., description="Whether the file exists")
    path: str = Field(..., description="Relative path to the file (e.g., 'outputs/model.step')")
    download_url: str = Field(..., description="URL to download the file")
    size: Optional[int] = Field(None, description="File size in bytes (if available)")


class OutputsInfo(BaseModel):
    """Information about all output files for a job."""
    part_summary_json: Optional[OutputFileInfo] = Field(None, description="part_summary.json file info")
    step_model: Optional[OutputFileInfo] = Field(None, description="model.step file info")
    glb_model: Optional[OutputFileInfo] = Field(None, description="model.glb file info")
    scale_report: Optional[OutputFileInfo] = Field(None, description="scale_report.json file info")
    inferred_stack: Optional[OutputFileInfo] = Field(None, description="inferred_stack.json file info")
    turned_stack: Optional[OutputFileInfo] = Field(None, description="turned_stack.json file info")
    run_report: Optional[OutputFileInfo] = Field(None, description="run_report.json file info")
    features_text: Optional[OutputFileInfo] = Field(None, description="features_text.json file info")








