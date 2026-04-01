"""Pipeline service for running analysis."""

import json
from pathlib import Path
from typing import Dict, List
from datetime import datetime, timezone

from app.geometry.feature_extractor import TurnedPartStack, TurnedPartSegment
from app.storage.file_storage import FileStorage
from app.services.job_service import JobService
from app.services.run_report_service import RunReportService
from app.models.job import JobStatus
from app.models.part_summary import PartSummary


class PipelineService:
    """Service for running analysis pipeline."""
    
    def __init__(self):
        """Initialize pipeline service."""
        self.file_storage = FileStorage()
        self.job_service = JobService()
        self.run_report_service = RunReportService()
    
    def build_stack_from_segments(self, segments_data: List[Dict], units: str) -> TurnedPartStack:
        """Build TurnedPartStack from segment data.
        
        Args:
            segments_data: List of segment dictionaries with z_start, z_end, od_diameter, id_diameter
            units: Units string (for reference, all calculations assume inches)
            
        Returns:
            TurnedPartStack instance
        """
        segments = []
        for seg_data in segments_data:
            segment = TurnedPartSegment(
                z_start=seg_data['z_start'],
                z_end=seg_data['z_end'],
                od_diameter=seg_data['od_diameter'],
                id_diameter=seg_data.get('id_diameter', 0.0)
            )
            segments.append(segment)
        
        return TurnedPartStack(segments=segments)
    
    def run_analysis(self, job_id: str) -> Dict:
        """Run analysis pipeline for a job.
        
        Args:
            job_id: Job identifier
            
        Returns:
            Dictionary with status and output files
            
        Raises:
            FileNotFoundError: If stack_input.json not found
            ValueError: If stack input is invalid
        """
        # Create run report
        report = self.run_report_service.create_report(job_id)
        from datetime import datetime, timezone
        
        # Verify job exists
        job = self.job_service.get_job(job_id)
        
        # Stage 1: Read stack input
        stage_start = datetime.now(timezone.utc)
        self.run_report_service.add_stage(report, "read_input", "running", stage_start)
        
        outputs_path = self.file_storage.get_outputs_path(job_id)
        input_file = outputs_path / "stack_input.json"
        
        if not input_file.exists():
            error_msg = "stack_input.json not found. Please create a profile first."
            stage_end = datetime.now(timezone.utc)
            self.run_report_service.add_stage(
                report, "read_input", "failed", stage_start, stage_end,
                error=error_msg
            )
            self.run_report_service.finish_report(report, "failed", errors=[error_msg])
            self.run_report_service.save_report(job_id, report)
            raise FileNotFoundError(error_msg)
        
        with open(input_file, 'r') as f:
            stack_data = json.load(f)
        
        units = stack_data.get('units', 'in')
        segments_data = stack_data.get('segments', [])
        
        if not segments_data:
            error_msg = "No segments found in stack input"
            stage_end = datetime.now(timezone.utc)
            self.run_report_service.add_stage(
                report, "read_input", "failed", stage_start, stage_end,
                error=error_msg
            )
            self.run_report_service.finish_report(report, "failed", errors=[error_msg])
            self.run_report_service.save_report(job_id, report)
            raise ValueError(error_msg)
        
        stage_end = datetime.now(timezone.utc)
        self.run_report_service.add_stage(report, "read_input", "completed", stage_start, stage_end)
        
        # Stage 2: Build and validate stack
        stage_start = datetime.now(timezone.utc)
        self.run_report_service.add_stage(report, "stack_build", "running", stage_start)
        
        # Build stack
        stack = self.build_stack_from_segments(segments_data, units)
        
        # Validate stack
        is_valid, errors = stack.validate(tolerance=1e-6)
        if not is_valid:
            error_msg = f"Stack validation failed: {', '.join(errors)}"
            stage_end = datetime.now(timezone.utc)
            self.run_report_service.add_stage(
                report, "stack_build", "failed", stage_start, stage_end,
                error=error_msg
            )
            self.run_report_service.finish_report(report, "failed", errors=[error_msg])
            self.run_report_service.save_report(job_id, report)
            raise ValueError(error_msg)
        
        stage_end = datetime.now(timezone.utc)
        self.run_report_service.add_stage(report, "stack_build", "completed", stage_start, stage_end)
        
        # Compute z_range
        if stack.segments:
            min_z = min(seg.z_start for seg in stack.segments)
            max_z = max(seg.z_end for seg in stack.segments)
            z_range = [min_z, max_z]
        else:
            z_range = [0.0, 0.0]
        
        # Build segments list for JSON
        segments_list = []
        for seg in stack.segments:
            seg_dict = {
                "z_start": seg.z_start,
                "z_end": seg.z_end,
                "od_diameter": seg.od_diameter,
                "id_diameter": seg.id_diameter,
                "wall_thickness": seg.wall_thickness,
                "volume_in3": seg.volume(),
                "od_area_in2": seg.od_surface_area(),
                "id_area_in2": seg.id_surface_area()
            }
            segments_list.append(seg_dict)
        
        # Compute totals
        totals = {
            "volume_in3": stack.total_volume(),
            "od_area_in2": stack.total_od_surface_area(),
            "id_area_in2": stack.total_id_surface_area(),
            "end_face_area_start_in2": stack.end_face_area_start(),
            "end_face_area_end_in2": stack.end_face_area_end(),
            "od_shoulder_area_in2": stack.od_shoulder_area(),
            "id_shoulder_area_in2": stack.id_shoulder_area(),
            "planar_ring_area_in2": stack.total_planar_ring_area(),
            "total_surface_area_in2": stack.total_surface_area()
        }
        
        # Generate part summary JSON
        generated_at_utc = datetime.now(timezone.utc).isoformat()
        
        # Convert totals to expected format (pipeline service has more detailed totals)
        totals_dict = {
            "total_volume_in3": totals["volume_in3"],
            "total_od_area_in2": totals["od_area_in2"],
            "total_id_area_in2": totals["id_area_in2"],
            "total_length_in": z_range[1] - z_range[0] if len(z_range) >= 2 else 0.0
        }

        part_summary = PartSummary(
            schema_version="0.1",
            generated_at_utc=generated_at_utc,
            units={
                "length": units,
                "area": f"{units}^2",
                "volume": f"{units}^3"
            },
            scale_report={
                "method": "manual",  # Manual stack input, no scale calibration
                "confidence": 1.0,
                "notes": "Scale not applicable for manual stack input"
            },
            z_range=z_range,
            segments=segments_list,
            totals=totals_dict,
            inference_metadata={
                "mode": "manual_stack",
                "overall_confidence": 1.0,  # Manual input is assumed correct
                "source": "manual_stack_input"
            },
            features=None  # Features will be added later by feature detection
        )

        # Convert to dict for JSON serialization
        part_summary_dict = part_summary.to_dict()
        
        # Stage 3: Generate part summary
        stage_start = datetime.now(timezone.utc)
        self.run_report_service.add_stage(report, "generate_summary", "running", stage_start)
        
        # Write part_summary.json
        summary_file = outputs_path / "part_summary.json"
        with open(summary_file, 'w') as f:
            json.dump(part_summary_dict, f, indent=2)
        
        stage_end = datetime.now(timezone.utc)
        self.run_report_service.add_stage(report, "generate_summary", "completed", stage_start, stage_end)
        
        # Update job status
        self.job_service.job_storage.update_job_status(job_id, JobStatus.COMPLETED)
        
        # Finish report
        self.run_report_service.finish_report(report, "completed", outputs=["part_summary.json"])
        self.run_report_service.save_report(job_id, report)
        
        return {
            "status": "DONE",
            "outputs": ["part_summary.json"],
            "job_id": job_id
        }

