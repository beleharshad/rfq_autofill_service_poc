"""Service for managing run reports."""

import json
from pathlib import Path
from typing import Optional, Dict, List
from datetime import datetime, timezone
from app.models.run_report import RunReport, PipelineStage, RunReportSummary
from app.storage.file_storage import FileStorage


class RunReportService:
    """Service for managing run reports."""
    
    def __init__(self):
        """Initialize run report service."""
        self.file_storage = FileStorage()
    
    def create_report(self, job_id: str) -> RunReport:
        """Create a new run report.
        
        Args:
            job_id: Job identifier
            
        Returns:
            New RunReport instance
        """
        started_at = datetime.now(timezone.utc).isoformat()
        return RunReport(
            job_id=job_id,
            started_at=started_at,
            status="running",
            stages=[],
            outputs=[],
            errors=[],
            warnings=[]
        )
    
    def add_stage(
        self,
        report: RunReport,
        name: str,
        status: str,
        started_at: Optional[datetime] = None,
        finished_at: Optional[datetime] = None,
        error: Optional[str] = None,
        warning: Optional[str] = None
    ) -> PipelineStage:
        """Add a pipeline stage to the report.
        
        Args:
            report: RunReport to add stage to
            name: Stage name
            status: Stage status
            started_at: When stage started (defaults to now)
            finished_at: When stage finished (defaults to now if status is completed/failed)
            error: Error message if failed
            warning: Warning message if any
            
        Returns:
            Created PipelineStage
        """
        now = datetime.now(timezone.utc)
        
        if started_at is None:
            started_at = now
        if isinstance(started_at, datetime):
            started_at_iso = started_at.isoformat()
        else:
            started_at_iso = started_at
        
        finished_at_iso = None
        duration_ms = None
        
        if finished_at is not None:
            if isinstance(finished_at, datetime):
                finished_at_iso = finished_at.isoformat()
            else:
                finished_at_iso = finished_at
        elif status in ['completed', 'failed']:
            finished_at_iso = now.isoformat()
        
        if started_at_iso and finished_at_iso:
            start_dt = datetime.fromisoformat(started_at_iso.replace('Z', '+00:00'))
            finish_dt = datetime.fromisoformat(finished_at_iso.replace('Z', '+00:00'))
            duration_ms = (finish_dt - start_dt).total_seconds() * 1000
        
        stage = PipelineStage(
            name=name,
            status=status,
            started_at=started_at_iso,
            finished_at=finished_at_iso,
            duration_ms=duration_ms,
            error=error,
            warning=warning
        )
        
        report.stages.append(stage)
        return stage
    
    def finish_report(
        self,
        report: RunReport,
        status: str,
        outputs: Optional[List[str]] = None,
        errors: Optional[List[str]] = None,
        warnings: Optional[List[str]] = None
    ) -> RunReport:
        """Finish a run report.
        
        Args:
            report: RunReport to finish
            status: Final status ('completed' or 'failed')
            outputs: List of generated output files
            errors: List of error messages
            warnings: List of warning messages
            
        Returns:
            Updated RunReport
        """
        finished_at = datetime.now(timezone.utc).isoformat()
        report.finished_at = finished_at
        report.status = status
        
        if outputs:
            report.outputs.extend(outputs)
        if errors:
            report.errors.extend(errors)
        if warnings:
            report.warnings.extend(warnings)
        
        # Calculate total duration
        start_dt = datetime.fromisoformat(report.started_at.replace('Z', '+00:00'))
        finish_dt = datetime.fromisoformat(finished_at.replace('Z', '+00:00'))
        report.duration_ms = (finish_dt - start_dt).total_seconds() * 1000
        
        return report
    
    def save_report(self, job_id: str, report: RunReport) -> None:
        """Save run report to file.
        
        Args:
            job_id: Job identifier
            report: RunReport to save
        """
        outputs_path = self.file_storage.get_outputs_path(job_id)
        outputs_path.mkdir(parents=True, exist_ok=True)
        
        report_file = outputs_path / "run_report.json"
        
        # Convert to dict for JSON serialization
        report_dict = report.dict()
        
        with open(report_file, 'w') as f:
            json.dump(report_dict, f, indent=2)
    
    def load_report(self, job_id: str) -> Optional[RunReport]:
        """Load run report from file.
        
        Args:
            job_id: Job identifier
            
        Returns:
            RunReport if exists, None otherwise
        """
        outputs_path = self.file_storage.get_outputs_path(job_id)
        report_file = outputs_path / "run_report.json"
        
        if not report_file.exists():
            return None
        
        with open(report_file, 'r') as f:
            report_dict = json.load(f)
        
        return RunReport(**report_dict)
    
    def get_report_summary(self, job_id: str) -> RunReportSummary:
        """Get summary of run report for API responses.
        
        Args:
            job_id: Job identifier
            
        Returns:
            RunReportSummary
        """
        report = self.load_report(job_id)
        
        if report is None:
            return RunReportSummary(has_report=False)
        
        completed_stages = sum(1 for s in report.stages if s.status == 'completed')
        failed_stages = sum(1 for s in report.stages if s.status == 'failed')
        
        return RunReportSummary(
            has_report=True,
            status=report.status,
            started_at=report.started_at,
            finished_at=report.finished_at,
            duration_ms=report.duration_ms,
            stage_count=len(report.stages),
            completed_stages=completed_stages,
            failed_stages=failed_stages,
            output_count=len(report.outputs),
            error_count=len(report.errors)
        )








