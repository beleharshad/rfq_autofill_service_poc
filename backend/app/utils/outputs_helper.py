"""Helper functions for building output file information."""

from pathlib import Path
from typing import Optional
from app.models.outputs import OutputFileInfo, OutputsInfo
from app.storage.file_storage import FileStorage


def build_outputs_info(job_id: str, base_url: str = "/api/v1") -> OutputsInfo:
    """Build outputs information for a job.
    
    Args:
        job_id: Job identifier
        base_url: Base URL for API (default: "/api/v1")
        
    Returns:
        OutputsInfo with file existence and download URLs
    """
    file_storage = FileStorage()
    outputs_path = file_storage.get_outputs_path(job_id)
    
    def check_file(filename: str) -> Optional[OutputFileInfo]:
        """Check if a file exists and return its info."""
        file_path = outputs_path / filename
        if file_path.exists() and file_path.is_file():
            try:
                size = file_path.stat().st_size
            except Exception:
                size = None
            
            return OutputFileInfo(
                exists=True,
                path=f"outputs/{filename}",
                download_url=f"{base_url}/jobs/{job_id}/download?path=outputs/{filename}",
                size=size
            )
        else:
            return OutputFileInfo(
                exists=False,
                path=f"outputs/{filename}",
                download_url=f"{base_url}/jobs/{job_id}/download?path=outputs/{filename}",
                size=None
            )
    
    return OutputsInfo(
        part_summary_json=check_file("part_summary.json"),
        step_model=check_file("model.step"),
        glb_model=check_file("model.glb"),
        scale_report=check_file("scale_report.json"),
        inferred_stack=check_file("inferred_stack.json"),
        turned_stack=check_file("turned_stack.json"),
        run_report=check_file("run_report.json")
    )





