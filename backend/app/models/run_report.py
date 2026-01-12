"""Run report models."""

from typing import List, Optional, Dict
from pydantic import BaseModel, Field
from datetime import datetime


class PipelineStage(BaseModel):
    """Status of a pipeline stage."""
    name: str = Field(..., description="Stage name")
    status: str = Field(..., description="Status: 'pending', 'running', 'completed', 'failed', 'skipped'")
    started_at: Optional[str] = Field(None, description="ISO timestamp when stage started")
    finished_at: Optional[str] = Field(None, description="ISO timestamp when stage finished")
    duration_ms: Optional[float] = Field(None, description="Duration in milliseconds")
    error: Optional[str] = Field(None, description="Error message if failed")
    warning: Optional[str] = Field(None, description="Warning message if any")


class RunReport(BaseModel):
    """Run report for a job processing."""
    job_id: str
    started_at: str = Field(..., description="ISO timestamp when processing started")
    finished_at: Optional[str] = Field(None, description="ISO timestamp when processing finished")
    duration_ms: Optional[float] = Field(None, description="Total duration in milliseconds")
    status: str = Field(..., description="Overall status: 'running', 'completed', 'failed'")
    stages: List[PipelineStage] = Field(default_factory=list, description="Pipeline stages")
    outputs: List[str] = Field(default_factory=list, description="Generated output files")
    errors: List[str] = Field(default_factory=list, description="Error messages")
    warnings: List[str] = Field(default_factory=list, description="Warning messages")


class RunReportSummary(BaseModel):
    """Summary of run report for API responses."""
    has_report: bool = Field(..., description="Whether a report exists")
    status: Optional[str] = Field(None, description="Overall status")
    started_at: Optional[str] = Field(None, description="When processing started")
    finished_at: Optional[str] = Field(None, description="When processing finished")
    duration_ms: Optional[float] = Field(None, description="Total duration")
    stage_count: int = Field(0, description="Number of stages")
    completed_stages: int = Field(0, description="Number of completed stages")
    failed_stages: int = Field(0, description="Number of failed stages")
    output_count: int = Field(0, description="Number of outputs generated")
    error_count: int = Field(0, description="Number of errors")





