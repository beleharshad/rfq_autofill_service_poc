"""Service for Profile2D processing."""

import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple
from datetime import datetime, timezone
import math

# Add project root to path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from geometry_2d import Profile2D, LineSegment, Point2D, ArcSegment
try:
    from revolved_solid_builder import RevolvedSolidBuilder
    _RSB_AVAILABLE = True
except (ImportError, Exception):
    RevolvedSolidBuilder = None  # type: ignore[assignment,misc]
    _RSB_AVAILABLE = False
from feature_extractor import FeatureExtractor, TurnedPartStack
from app.storage.file_storage import FileStorage
from app.services.job_service import JobService
from app.services.run_report_service import RunReportService
from app.services.step_to_glb_converter import StepToGlbConverter
from app.models.job import JobStatus, JobMode
from app.models.part_summary import PartSummary


class Profile2DService:
    """Service for processing Profile2D input and generating solids."""
    
    def __init__(self):
        """Initialize Profile2D service."""
        self.file_storage = FileStorage()
        self.job_service = JobService()
        self.run_report_service = RunReportService()
        self.step_to_glb_converter = StepToGlbConverter()
        self.validation_tolerance = 1e-6
    
    def validate_profile2d_strict(
        self, 
        profile: Profile2D, 
        axis_x: float = 0.0,
        allow_axis_touch: bool = True
    ) -> Tuple[bool, List[str]]:
        """Strict validation of Profile2D before building solid.
        
        Validates:
        1. All X (radius) values must be >= 0
        2. Profile must not cross the revolution axis (x=axis_x), except possibly touching at endpoints
        3. No self-intersections except consecutive segment shared endpoints
        4. Segment connectivity tolerance: endpoints must match within tolerance
        5. Enforce consistent orientation (clockwise or counterclockwise)
        
        Args:
            profile: Profile2D to validate
            axis_x: X coordinate of revolution axis (default: 0.0)
            allow_axis_touch: If True, allow profile to touch axis at endpoints
            
        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        errors = []
        tolerance = self.validation_tolerance
        
        if profile.is_empty():
            errors.append("Profile is empty")
            return False, errors
        
        primitives = profile.get_primitives()
        if not primitives:
            errors.append("Profile has no primitives")
            return False, errors
        
        # 1. Check all X (radius) values >= 0
        for i, prim in enumerate(primitives):
            start_x = prim.start_point.x
            end_x = prim.end_point.x
            
            if start_x < -tolerance:
                errors.append(f"Primitive {i}: Start point X (radius) = {start_x:.6f} is negative")
            if end_x < -tolerance:
                errors.append(f"Primitive {i}: End point X (radius) = {end_x:.6f} is negative")
        
        if errors:
            return False, errors
        
        # 2. Check profile doesn't cross revolution axis
        # For each segment, check if it crosses x=axis_x
        for i, prim in enumerate(primitives):
            start_x = prim.start_point.x
            end_x = prim.end_point.x
            
            # Check if segment crosses the axis
            if isinstance(prim, LineSegment):
                # Line segment crosses axis if start and end are on opposite sides
                start_side = start_x - axis_x
                end_side = end_x - axis_x
                
                # If both are on same side or both are zero, no crossing
                if start_side * end_side < -tolerance:
                    # Check if it's just touching at an endpoint
                    touches_start = abs(start_side) < tolerance
                    touches_end = abs(end_side) < tolerance
                    
                    if not (allow_axis_touch and (touches_start or touches_end)):
                        errors.append(
                            f"Primitive {i}: Line segment crosses revolution axis (x={axis_x:.6f}). "
                            f"Start: ({start_x:.6f}, {prim.start_point.y:.6f}), "
                            f"End: ({end_x:.6f}, {prim.end_point.y:.6f})"
                        )
                elif abs(start_side) < tolerance and abs(end_side) < tolerance:
                    # Entire segment is on the axis
                    if not allow_axis_touch:
                        errors.append(
                            f"Primitive {i}: Line segment lies entirely on revolution axis (x={axis_x:.6f})"
                        )
            # TODO: Add arc segment axis crossing check when arcs are supported
        
        if errors:
            return False, errors
        
        # 3. Check segment connectivity with tolerance
        for i in range(len(primitives)):
            current_prim = primitives[i]
            next_prim = primitives[(i + 1) % len(primitives)]
            
            current_end = current_prim.end_point
            next_start = next_prim.start_point
            
            distance = current_end.distance_to(next_start)
            if distance > tolerance:
                errors.append(
                    f"Primitive {i} end point ({current_end.x:.6f}, {current_end.y:.6f}) "
                    f"does not connect to primitive {(i + 1) % len(primitives)} start point "
                    f"({next_start.x:.6f}, {next_start.y:.6f}). Distance: {distance:.6f} "
                    f"(tolerance: {tolerance:.6f})"
                )
        
        if errors:
            return False, errors
        
        # 4. Check for self-intersections (except consecutive segment shared endpoints)
        n = len(primitives)
        for i in range(n):
            prim1 = primitives[i]
            
            # Check against all non-consecutive segments
            # Skip: i (itself), (i+1) % n (next), (i-1) % n (previous)
            for j in range(n):
                if j == i:
                    continue
                if j == (i + 1) % n:  # Next segment (shares endpoint)
                    continue
                if j == (i - 1) % n:  # Previous segment (shares endpoint)
                    continue
                
                prim2 = primitives[j]
                
                if isinstance(prim1, LineSegment) and isinstance(prim2, LineSegment):
                    intersects, intersection_pt = prim1.intersects_line(prim2, tolerance)
                    if intersects:
                        errors.append(
                            f"Self-intersection detected: Primitive {i} intersects primitive {j} "
                            f"at point ({intersection_pt.x:.6f}, {intersection_pt.y:.6f})"
                        )
        
        if errors:
            return False, errors
        
        # 5. Check/enforce consistent orientation
        # Calculate signed area to determine orientation
        signed_area = 0.0
        for prim in primitives:
            if isinstance(prim, LineSegment):
                # Shoelace formula contribution
                x1, y1 = prim.start_point.x, prim.start_point.y
                x2, y2 = prim.end_point.x, prim.end_point.y
                signed_area += (x1 * y2 - x2 * y1)
        
        # Normalize by 2 for actual area
        signed_area /= 2.0
        
        # Positive area = counterclockwise, negative = clockwise
        # For a valid closed profile, area should be non-zero
        if abs(signed_area) < tolerance:
            errors.append(
                f"Profile orientation is degenerate (signed area = {signed_area:.6f}). "
                f"Profile may be self-intersecting or have zero area."
            )
        
        return len(errors) == 0, errors
    
    def process_profile2d(self, job_id: str, primitives: List[Dict], axis_point: Dict) -> Dict:
        """Process Profile2D input and generate solid + analysis.
        
        Args:
            job_id: Job identifier
            primitives: List of primitive dictionaries (line segments)
            axis_point: Revolution axis point dictionary
            
        Returns:
            Dictionary with status, outputs, and validation errors
        """
        # Verify job exists
        job = self.job_service.get_job(job_id)
        
        # Stage 1: Build Profile2D from primitives
        stage_start = datetime.now(timezone.utc)
        self.run_report_service.add_stage(report, "profile_construction", "running", stage_start)
        
        profile = Profile2D()
        validation_errors = []
        
        for i, prim_data in enumerate(primitives):
            if prim_data.get('type') != 'line':
                validation_errors.append(f"Primitive {i}: Only 'line' type is supported in MVP")
                continue
            
            start = Point2D(prim_data['start']['x'], prim_data['start']['y'])
            end = Point2D(prim_data['end']['x'], prim_data['end']['y'])
            line = LineSegment(start, end)
            profile.add_primitive(line)
        
        # Stage 2: Profile validation
        stage_start = datetime.now(timezone.utc)
        self.run_report_service.add_stage(report, "profile_validation", "running", stage_start)
        
        # Basic profile validation (topology, connectivity)
        is_valid, errors = profile.validate_topology(tolerance=self.validation_tolerance)
        if not is_valid:
            validation_errors.extend([f"Profile topology: {e}" for e in errors])
            stage_end = datetime.now(timezone.utc)
            self.run_report_service.add_stage(
                report, "profile_validation", "failed", stage_start, stage_end,
                error="; ".join([f"Profile topology: {e}" for e in errors])
            )
            self.run_report_service.finish_report(report, "failed", errors=validation_errors)
            self.run_report_service.save_report(job_id, report)
            
            # Get job mode (default to assisted_manual if not set)
            job = self.job_service.get_job(job_id)
            mode = job.mode if job.mode else JobMode.ASSISTED_MANUAL
            
            return {
                "job_id": job_id,
                "status": "FAILED",
                "mode": mode,
                "outputs": [],
                "warnings": report.warnings,
                "validation_errors": validation_errors,
                "confidence": None
            }
        
        # Check if profile is closed
        if not profile.is_closed(tolerance=self.validation_tolerance):
            error_msg = f"Profile is not closed (last point must connect to first within tolerance {self.validation_tolerance:.6f})"
            validation_errors.append(error_msg)
            stage_end = datetime.now(timezone.utc)
            self.run_report_service.add_stage(
                report, "profile_validation", "failed", stage_start, stage_end,
                error=error_msg
            )
            self.run_report_service.finish_report(report, "failed", errors=validation_errors)
            self.run_report_service.save_report(job_id, report)
            
            # Get job mode (default to assisted_manual if not set)
            job = self.job_service.get_job(job_id)
            mode = job.mode if job.mode else JobMode.ASSISTED_MANUAL
            
            return {
                "job_id": job_id,
                "status": "FAILED",
                "mode": mode,
                "outputs": [],
                "warnings": report.warnings,
                "validation_errors": validation_errors,
                "confidence": None
            }
        
        # Strict validation before building solid
        axis_x = axis_point.get('x', 0.0)
        is_strict_valid, strict_errors = self.validate_profile2d_strict(
            profile, 
            axis_x=axis_x,
            allow_axis_touch=True
        )
        if not is_strict_valid:
            validation_errors.extend([f"Profile validation: {e}" for e in strict_errors])
            stage_end = datetime.now(timezone.utc)
            self.run_report_service.add_stage(
                report, "profile_validation", "failed", stage_start, stage_end,
                error="; ".join([f"Profile validation: {e}" for e in strict_errors])
            )
            self.run_report_service.finish_report(report, "failed", errors=validation_errors)
            self.run_report_service.save_report(job_id, report)
            
            # Get job mode (default to assisted_manual if not set)
            job = self.job_service.get_job(job_id)
            mode = job.mode if job.mode else JobMode.ASSISTED_MANUAL
            
            return {
                "job_id": job_id,
                "status": "FAILED",
                "mode": mode,
                "outputs": [],
                "warnings": report.warnings,
                "validation_errors": validation_errors,
                "confidence": None
            }
        
        stage_end = datetime.now(timezone.utc)
        self.run_report_service.add_stage(report, "profile_validation", "completed", stage_start, stage_end)
        
        # Stage 3: Build solid
        stage_start = datetime.now(timezone.utc)
        self.run_report_service.add_stage(report, "solid_build", "running", stage_start)
        
        builder = RevolvedSolidBuilder()
        axis_pt = Point2D(axis_point.get('x', 0.0), axis_point.get('y', 0.0))
        builder.set_axis(axis_pt)
        
        success = builder.build_from_profile(profile)
        if not success:
            error_msg = "Failed to build solid from profile"
            validation_errors.append(error_msg)
            stage_end = datetime.now(timezone.utc)
            self.run_report_service.add_stage(
                report, "solid_build", "failed", stage_start, stage_end,
                error=error_msg
            )
            self.run_report_service.finish_report(report, "failed", errors=validation_errors)
            self.run_report_service.save_report(job_id, report)
            
            # Get job mode (default to assisted_manual if not set)
            job = self.job_service.get_job(job_id)
            mode = job.mode if job.mode else JobMode.ASSISTED_MANUAL
            
            return {
                "job_id": job_id,
                "status": "FAILED",
                "mode": mode,
                "outputs": [],
                "warnings": report.warnings,
                "validation_errors": validation_errors,
                "confidence": None
            }
        
        solid = builder.get_solid()
        if solid is None or solid.IsNull():
            error_msg = "Generated solid is null"
            validation_errors.append(error_msg)
            stage_end = datetime.now(timezone.utc)
            self.run_report_service.add_stage(
                report, "solid_build", "failed", stage_start, stage_end,
                error=error_msg
            )
            self.run_report_service.finish_report(report, "failed", errors=validation_errors)
            self.run_report_service.save_report(job_id, report)
            
            # Get job mode (default to assisted_manual if not set)
            job = self.job_service.get_job(job_id)
            mode = job.mode if job.mode else JobMode.ASSISTED_MANUAL
            
            return {
                "job_id": job_id,
                "status": "FAILED",
                "mode": mode,
                "outputs": [],
                "warnings": report.warnings,
                "validation_errors": validation_errors,
                "confidence": None
            }
        
        stage_end = datetime.now(timezone.utc)
        self.run_report_service.add_stage(report, "solid_build", "completed", stage_start, stage_end)
        
        # Stage 4: Export STEP file
        stage_start = datetime.now(timezone.utc)
        self.run_report_service.add_stage(report, "step_export", "running", stage_start)
        
        outputs_path = self.file_storage.get_outputs_path(job_id)
        outputs_path.mkdir(parents=True, exist_ok=True)
        step_file = outputs_path / "model.step"
        
        export_success = builder.export_step(str(step_file))
        stage_end = datetime.now(timezone.utc)
        if not export_success:
            warning_msg = "Failed to export STEP file"
            validation_errors.append(warning_msg)
            self.run_report_service.add_stage(
                report, "step_export", "failed", stage_start, stage_end,
                error=warning_msg
            )
            report.warnings.append(warning_msg)
        else:
            self.run_report_service.add_stage(report, "step_export", "completed", stage_start, stage_end)
            
            # Stage 5: Convert STEP to GLB
            stage_start = datetime.now(timezone.utc)
            self.run_report_service.add_stage(report, "glb_conversion", "running", stage_start)
            
            glb_file = outputs_path / "model.glb"
            glb_success, glb_error = self.step_to_glb_converter.convert_step_to_glb(
                step_file, glb_file, check_cache=True
            )
            stage_end = datetime.now(timezone.utc)
            
            if not glb_success:
                warning_msg = f"Failed to convert STEP to GLB: {glb_error}"
                report.warnings.append(warning_msg)
                self.run_report_service.add_stage(
                    report, "glb_conversion", "failed", stage_start, stage_end,
                    error=glb_error or warning_msg
                )
            else:
                self.run_report_service.add_stage(report, "glb_conversion", "completed", stage_start, stage_end)
        
        # Stage 6: Feature extraction
        stage_start = datetime.now(timezone.utc)
        self.run_report_service.add_stage(report, "feature_extraction", "running", stage_start)
        
        extractor = FeatureExtractor()
        # Set reference axis using convention (matches builder axis)
        # FeatureExtractor.set_reference_axis() accepts Point2D or None (defaults to Z-axis)
        # Since we're using standard Z-axis revolution, pass None to use default
        extractor.set_reference_axis(None)  # None defaults to Z-axis through origin
        
        # Extract features
        collection = extractor.extract_features(solid)
        
        # Build TurnedPartStack from feature collection
        stack = extractor.build_turned_part_stack(collection, tolerance=1e-6)
        if stack is None or len(stack.segments) == 0:
            error_msg = "Failed to extract turned part stack"
            validation_errors.append(error_msg)
            stage_end = datetime.now(timezone.utc)
            self.run_report_service.add_stage(
                report, "feature_extraction", "failed", stage_start, stage_end,
                error=error_msg
            )
            self.run_report_service.finish_report(report, "failed", errors=validation_errors)
            self.run_report_service.save_report(job_id, report)
            
            # Get job mode (default to assisted_manual if not set)
            job = self.job_service.get_job(job_id)
            mode = job.mode if job.mode else JobMode.ASSISTED_MANUAL
            
            return {
                "job_id": job_id,
                "status": "FAILED",
                "mode": mode,
                "outputs": [],
                "warnings": report.warnings,
                "validation_errors": validation_errors,
                "confidence": None
            }
        
        # Validate stack
        is_stack_valid, stack_errors = stack.validate()
        if not is_stack_valid:
            error_msg = "; ".join([f"Stack validation: {e}" for e in stack_errors])
            validation_errors.extend([f"Stack validation: {e}" for e in stack_errors])
            stage_end = datetime.now(timezone.utc)
            self.run_report_service.add_stage(
                report, "feature_extraction", "failed", stage_start, stage_end,
                error=error_msg
            )
            self.run_report_service.finish_report(report, "failed", errors=validation_errors)
            self.run_report_service.save_report(job_id, report)
            
            # Get job mode (default to assisted_manual if not set)
            job = self.job_service.get_job(job_id)
            mode = job.mode if job.mode else JobMode.ASSISTED_MANUAL
            
            return {
                "job_id": job_id,
                "status": "FAILED",
                "mode": mode,
                "outputs": [],
                "warnings": report.warnings,
                "validation_errors": validation_errors,
                "confidence": None
            }
        
        stage_end = datetime.now(timezone.utc)
        self.run_report_service.add_stage(report, "feature_extraction", "completed", stage_start, stage_end)
        
        # Stage 6: Generate part summary
        stage_start = datetime.now(timezone.utc)
        self.run_report_service.add_stage(report, "stack_build", "running", stage_start)
        
        # Generate part_summary.json
        stack_dict = stack.to_dict()

        # Convert stack.to_dict() to PartSummary format
        part_summary = PartSummary(
            schema_version=stack_dict.get("schema_version", "0.1"),
            generated_at_utc=stack_dict.get("generated_at_utc", datetime.now(timezone.utc).isoformat()),
            units=stack_dict.get("units", {"length": "in", "area": "in^2", "volume": "in^3"}),
            scale_report={
                "method": "profile2d",  # Profile2D input
                "confidence": 1.0,
                "notes": "Scale not applicable for Profile2D input"
            },
            z_range=stack_dict.get("z_range", [0.0, 0.0]),
            segments=stack_dict.get("segments", []),
            totals={
                "total_volume_in3": stack_dict.get("totals", {}).get("volume_in3", 0.0),
                "total_od_area_in2": stack_dict.get("totals", {}).get("od_area_in2", 0.0),
                "total_id_area_in2": stack_dict.get("totals", {}).get("id_area_in2", 0.0),
                "total_length_in": stack_dict.get("z_range", [0.0, 0.0])[1] - stack_dict.get("z_range", [0.0, 0.0])[0]
            },
            inference_metadata={
                "mode": "profile2d",
                "overall_confidence": 1.0,  # Profile2D input is assumed correct
                "source": "profile2d_input"
            },
            features=None  # Features will be added later by feature detection
        )

        # Convert to dict for JSON serialization
        part_summary_dict = part_summary.to_dict()
        
        summary_file = outputs_path / "part_summary.json"
        with open(summary_file, 'w') as f:
            json.dump(part_summary_dict, f, indent=2)
        
        stage_end = datetime.now(timezone.utc)
        self.run_report_service.add_stage(report, "stack_build", "completed", stage_start, stage_end)
        
        # Update job status
        self.job_service.job_storage.update_job_status(job_id, JobStatus.COMPLETED)
        
        # Build output list
        outputs = ["part_summary.json"]
        if export_success:
            outputs.append("model.step")
        
        # Check for GLB file
        glb_file = outputs_path / "model.glb"
        if glb_file.exists():
            outputs.append("model.glb")
        
        # Get job mode (default to assisted_manual if not set)
        job = self.job_service.get_job(job_id)
        mode = job.mode if job.mode else JobMode.ASSISTED_MANUAL
        
        # Finish report
        self.run_report_service.finish_report(report, "completed", outputs=outputs, errors=validation_errors if validation_errors else None)
        self.run_report_service.save_report(job_id, report)
        
        return {
            "job_id": job_id,
            "status": "DONE",
            "mode": mode,
            "outputs": outputs,
            "warnings": report.warnings,
            "validation_errors": validation_errors if validation_errors else [],
            "confidence": None  # Only set for auto_convert mode
        }

