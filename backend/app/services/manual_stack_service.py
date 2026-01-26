"""Service for processing manual turned stack input."""

import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone

# Add project root to path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from feature_extractor import TurnedPartStack, TurnedPartSegment
from geometry_2d import Profile2D, LineSegment, Point2D
from revolved_solid_builder import RevolvedSolidBuilder
from feature_extractor import FeatureExtractor
from app.storage.file_storage import FileStorage
from app.services.job_service import JobService
from app.models.job import JobStatus
from app.models.part_summary import PartSummary


class ManualStackService:
    """Service for processing manual turned stack input."""
    
    def __init__(self, min_wall_thickness: float = 0.001):
        """Initialize manual stack service.
        
        Args:
            min_wall_thickness: Minimum wall thickness threshold (default: 0.001 inches)
        """
        self.file_storage = FileStorage()
        self.job_service = JobService()
        self.min_wall_thickness = min_wall_thickness
    
    def validate_segments(
        self,
        segments_data: List[Dict],
        units: str
    ) -> Tuple[bool, List[str], List[str]]:
        """Validate segment data.
        
        Args:
            segments_data: List of segment dictionaries
            units: Units string
            
        Returns:
            Tuple of (is_valid, errors, warnings)
        """
        errors = []
        warnings = []
        
        if not segments_data:
            errors.append("At least one segment is required")
            return False, errors, warnings
        
        # Sort segments by z_start
        sorted_segments = sorted(segments_data, key=lambda s: s['z_start'])
        
        # Validate each segment
        for i, seg in enumerate(sorted_segments):
            z_start = seg.get('z_start')
            z_end = seg.get('z_end')
            od_diameter = seg.get('od_diameter')
            id_diameter = seg.get('id_diameter', 0.0)
            
            # Basic validation
            if z_start is None or z_end is None:
                errors.append(f"Segment {i}: z_start and z_end are required")
                continue
            
            if z_start >= z_end:
                errors.append(f"Segment {i}: z_start ({z_start}) must be less than z_end ({z_end})")
            
            if od_diameter is None or od_diameter <= 0:
                errors.append(f"Segment {i}: od_diameter must be greater than 0")
            
            if id_diameter is None or id_diameter < 0:
                errors.append(f"Segment {i}: id_diameter cannot be negative")
            
            if id_diameter > od_diameter:
                errors.append(
                    f"Segment {i}: id_diameter ({id_diameter}) cannot be greater than od_diameter ({od_diameter})"
                )
            
            # Wall thickness validation
            wall_thickness = (od_diameter - id_diameter) / 2.0
            if wall_thickness < self.min_wall_thickness:
                warnings.append(
                    f"Segment {i}: Wall thickness ({wall_thickness:.6f} {units}) is below minimum threshold "
                    f"({self.min_wall_thickness} {units})"
                )
        
        # Validate z ranges are contiguous and increasing
        for i in range(len(sorted_segments) - 1):
            current = sorted_segments[i]
            next_seg = sorted_segments[i + 1]
            
            current_z_end = current.get('z_end')
            next_z_start = next_seg.get('z_start')
            
            if current_z_end is None or next_z_start is None:
                continue  # Already caught in segment validation
            
            tolerance = 1e-6
            if abs(current_z_end - next_z_start) > tolerance:
                errors.append(
                    f"Segments {i} and {i+1}: z ranges are not contiguous. "
                    f"Segment {i} ends at {current_z_end}, segment {i+1} starts at {next_z_start}"
                )
        
        is_valid = len(errors) == 0
        return is_valid, errors, warnings
    
    def segments_to_profile2d(self, segments_data: List[Dict]) -> Profile2D:
        """Convert turned stack segments to a Profile2D rectangular step profile.
        
        Creates a closed profile with:
        - Bottom edge (ID radius to OD radius at z_start of first segment)
        - Right edge (OD profile: vertical steps at each segment boundary)
        - Top edge (OD radius to ID radius at z_end of last segment)
        - Left edge (ID profile: vertical steps at each segment boundary)
        
        Args:
            segments_data: List of segment dictionaries (must be sorted by z_start)
            
        Returns:
            Profile2D object representing the rectangular step profile
        """
        if not segments_data:
            return Profile2D()
        
        # Sort segments by z_start
        sorted_segments = sorted(segments_data, key=lambda s: s['z_start'])
        
        # Get z range
        min_z = sorted_segments[0]['z_start']
        max_z = sorted_segments[-1]['z_end']
        
        # Get first and last segment radii
        first_seg = sorted_segments[0]
        last_seg = sorted_segments[-1]
        first_id_radius = first_seg.get('id_diameter', 0.0) / 2.0
        first_od_radius = first_seg.get('od_diameter', 0.0) / 2.0
        last_id_radius = last_seg.get('id_diameter', 0.0) / 2.0
        last_od_radius = last_seg.get('od_diameter', 0.0) / 2.0
        
        profile = Profile2D()
        
        # Build profile in clockwise order (starting from bottom-left, going around)
        # Convention: x = radius, y = axial (Z coordinate)
        
        # 1. Bottom edge: from (first_id_radius, min_z) to (first_od_radius, min_z)
        if first_id_radius > 0:
            profile.add_primitive(LineSegment(
                Point2D(first_id_radius, min_z),
                Point2D(first_od_radius, min_z)
            ))
        else:
            # Start at axis (0, min_z)
            profile.add_primitive(LineSegment(
                Point2D(0.0, min_z),
                Point2D(first_od_radius, min_z)
            ))
        
        # 2. Right edge (OD profile): vertical steps
        # Draw vertical lines for each segment, with horizontal transitions when OD changes
        # Start from first_od_radius at min_z
        current_od_radius = first_od_radius
        current_z = min_z
        
        for i, seg in enumerate(sorted_segments):
            od_radius = seg.get('od_diameter', 0.0) / 2.0
            z_start = seg.get('z_start')
            z_end = seg.get('z_end')
            
            # If OD changed from previous segment, draw horizontal transition
            if i > 0 and abs(od_radius - current_od_radius) > 1e-6:
                prev_seg = sorted_segments[i - 1]
                prev_z_end = prev_seg.get('z_end')
                profile.add_primitive(LineSegment(
                    Point2D(current_od_radius, prev_z_end),
                    Point2D(od_radius, z_start)
                ))
            
            # Vertical line at this OD radius
            profile.add_primitive(LineSegment(
                Point2D(od_radius, z_start),
                Point2D(od_radius, z_end)
            ))
            current_od_radius = od_radius
            current_z = z_end
        
        # 3. Top edge: from (last_od_radius, max_z) to (last_id_radius, max_z)
        if last_id_radius > 0:
            profile.add_primitive(LineSegment(
                Point2D(last_od_radius, max_z),
                Point2D(last_id_radius, max_z)
            ))
        else:
            # End at axis (0, max_z)
            profile.add_primitive(LineSegment(
                Point2D(last_od_radius, max_z),
                Point2D(0.0, max_z)
            ))
        
        # 4. Left edge (ID profile): vertical steps (going backwards)
        # Draw vertical lines for each segment in reverse, with horizontal transitions when ID changes
        # Start from last_id_radius at max_z
        current_id_radius = last_id_radius
        current_z = max_z
        
        for i in range(len(sorted_segments) - 1, -1, -1):
            seg = sorted_segments[i]
            id_radius = seg.get('id_diameter', 0.0) / 2.0
            z_start = seg.get('z_start')
            z_end = seg.get('z_end')
            
            # If ID changed from next segment (in forward order), draw horizontal transition
            if i < len(sorted_segments) - 1 and abs(id_radius - current_id_radius) > 1e-6:
                next_seg = sorted_segments[i + 1]
                next_z_start = next_seg.get('z_start')
                profile.add_primitive(LineSegment(
                    Point2D(current_id_radius, next_z_start),
                    Point2D(id_radius, z_end)
                ))
            
            # Vertical line at this ID radius (going down)
            if id_radius > 0:
                profile.add_primitive(LineSegment(
                    Point2D(id_radius, z_end),
                    Point2D(id_radius, z_start)
                ))
            else:
                # ID is 0, connect to axis
                profile.add_primitive(LineSegment(
                    Point2D(0.0, z_end),
                    Point2D(0.0, z_start)
                ))
            current_id_radius = id_radius
            current_z = z_start
        
        return profile
    
    def generate_step_from_stack(self, job_id: str) -> Dict:
        """Generate STEP file from existing turned stack.
        
        Args:
            job_id: Job identifier
            
        Returns:
            Dictionary with status, outputs, warnings, errors
        """
        outputs_path = self.file_storage.get_outputs_path(job_id)
        turned_stack_file = outputs_path / "turned_stack.json"
        
        if not turned_stack_file.exists():
            return {
                "job_id": job_id,
                "status": "FAILED",
                "error": "turned_stack.json not found. Please create a stack first.",
                "outputs": []
            }
        
        # Read turned stack
        with open(turned_stack_file, 'r') as f:
            stack_data = json.load(f)
        
        segments_data = stack_data.get('segments', [])
        if not segments_data:
            return {
                "job_id": job_id,
                "status": "FAILED",
                "error": "No segments found in turned_stack.json",
                "outputs": []
            }
        
        # Convert segments to Profile2D
        try:
            profile = self.segments_to_profile2d(segments_data)
        except Exception as e:
            return {
                "job_id": job_id,
                "status": "FAILED",
                "error": f"Failed to convert segments to Profile2D: {str(e)}",
                "outputs": []
            }
        
        # Validate profile
        is_valid, errors = profile.validate_topology(tolerance=1e-6)
        if not is_valid:
            return {
                "job_id": job_id,
                "status": "FAILED",
                "error": f"Profile validation failed: {', '.join(errors)}",
                "outputs": []
            }
        
        # Build solid using RevolvedSolidBuilder
        builder = RevolvedSolidBuilder()
        success = builder.build_from_profile(profile)
        if not success:
            return {
                "job_id": job_id,
                "status": "FAILED",
                "error": "Failed to build solid from profile",
                "outputs": []
            }
        
        solid = builder.get_solid()
        if solid is None:
            return {
                "job_id": job_id,
                "status": "FAILED",
                "error": "Solid is None after building",
                "outputs": []
            }
        
        # Export STEP
        step_file = outputs_path / "model.step"
        try:
            builder.export_step(str(step_file))
        except Exception as e:
            return {
                "job_id": job_id,
                "status": "FAILED",
                "error": f"Failed to export STEP: {str(e)}",
                "outputs": []
            }
        
        # Run FeatureExtractor to regenerate part_summary.json with feature counts
        try:
            extractor = FeatureExtractor()
            # FeatureExtractor.set_reference_axis() accepts Point2D or None (defaults to Z-axis)
            # Since we're using standard Z-axis revolution, pass None to use default
            extractor.set_reference_axis(None)  # None defaults to Z-axis through origin
            collection = extractor.extract_features(solid)
            
            # Build turned part stack from feature collection
            turned_stack = extractor.build_turned_part_stack(collection, tolerance=1e-6)
            if turned_stack is None or len(turned_stack.segments) == 0:
                return {
                    "job_id": job_id,
                    "status": "FAILED",
                    "error": "Failed to extract turned part stack",
                    "outputs": []
                }
            
            # Compute z_range
            if turned_stack.segments:
                min_z = min(seg.z_start for seg in turned_stack.segments)
                max_z = max(seg.z_end for seg in turned_stack.segments)
                z_range = [min_z, max_z]
            else:
                z_range = [0.0, 0.0]
            
            # Build segments list for JSON
            segments_list = []
            for seg in turned_stack.segments:
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
            
            # Get feature counts from FeatureCollection
            # CylinderFeature has is_external attribute (not is_internal)
            external_cylinders = len([c for c in collection.cylinders if c.is_external])
            internal_bores = len(collection.holes) + len([c for c in collection.cylinders if not c.is_external])
            planar_faces = len(collection.planar_faces)
            # Count total faces from solid
            from OCC.Core.TopExp import TopExp_Explorer
            from OCC.Core.TopAbs import TopAbs_FACE
            face_explorer = TopExp_Explorer(solid, TopAbs_FACE)
            total_faces = 0
            while face_explorer.More():
                total_faces += 1
                face_explorer.Next()
            
            feature_counts = {
                "external_cylinders": external_cylinders,
                "internal_bores": internal_bores,
                "planar_faces": planar_faces,
                "total_faces": total_faces
            }
            
            # Compute totals
            totals = {
                "volume_in3": turned_stack.total_volume(),
                "od_area_in2": turned_stack.total_od_surface_area(),
                "id_area_in2": turned_stack.total_id_surface_area(),
                "end_face_area_start_in2": turned_stack.end_face_area_start(),
                "end_face_area_end_in2": turned_stack.end_face_area_end(),
                "od_shoulder_area_in2": turned_stack.od_shoulder_area(),
                "id_shoulder_area_in2": turned_stack.id_shoulder_area(),
                "planar_ring_area_in2": turned_stack.total_planar_ring_area(),
                "total_surface_area_in2": turned_stack.total_surface_area()
            }
            
            # Generate part summary JSON
            units = stack_data.get('units', 'in')
            generated_at_utc = datetime.now(timezone.utc).isoformat()
            
            part_summary = {
                "schema_version": "0.1",
                "generated_at_utc": generated_at_utc,
                "units": {
                    "length": units,
                    "area": f"{units}^2",
                    "volume": f"{units}^3"
                },
                "z_range": z_range,
                "segments": segments_list,
                "totals": totals,
                "feature_counts": feature_counts,  # Populated from FeatureExtractor (OCC solid was generated)
                "inference_metadata": {
                    "mode": "reference_only",  # Manual input mode - PDF shown as reference only
                    "source": "reference_only_step"  # OCC solid was generated from manual stack
                }
            }
            
            # Write part_summary.json
            summary_file = outputs_path / "part_summary.json"
            with open(summary_file, 'w') as f:
                json.dump(part_summary, f, indent=2)
            
            # Try to generate GLB if converter is available
            glb_file = outputs_path / "model.glb"
            try:
                from app.services.step_to_glb_converter import StepToGlbConverter
                converter = StepToGlbConverter()
                if converter.available:
                    converter.convert_step_to_glb(step_file, glb_file, check_cache=False)
            except Exception:
                # GLB conversion is optional, don't fail if it doesn't work
                pass
            
            outputs = ["model.step", "part_summary.json"]
            if glb_file.exists():
                outputs.append("model.glb")
            
            # Generate human-readable explanation
            from app.utils.stack_explanation import generate_stack_explanation
            explanation = generate_stack_explanation(
                segments_list,
                units=units,
                overall_confidence=None  # Manual input has no confidence score
            )
            
            return {
                "job_id": job_id,
                "status": "DONE",
                "outputs": outputs,
                "warnings": [],
                "explanation": explanation
            }
            
        except Exception as e:
            return {
                "job_id": job_id,
                "status": "FAILED",
                "error": f"Failed to extract features: {str(e)}",
                "outputs": []
            }
    
    def process_turned_stack(
        self,
        job_id: str,
        units: str,
        segments_data: List[Dict],
        notes: Optional[str] = None
    ) -> Dict:
        """Process turned stack input and generate part summary.
        
        Args:
            job_id: Job identifier
            units: Units string
            segments_data: List of segment dictionaries
            notes: Optional notes
            
        Returns:
            Dictionary with status, summary, totals, warnings, outputs
        """
        # Validate segments
        is_valid, errors, warnings = self.validate_segments(segments_data, units)
        
        if not is_valid:
            return {
                "job_id": job_id,
                "status": "FAILED",
                "summary": {},
                "totals": {},
                "warnings": warnings,
                "errors": errors,
                "outputs": []
            }
        
        # Build TurnedPartStack
        segments = []
        for seg_data in segments_data:
            segment = TurnedPartSegment(
                z_start=seg_data['z_start'],
                z_end=seg_data['z_end'],
                od_diameter=seg_data['od_diameter'],
                id_diameter=seg_data.get('id_diameter', 0.0)
            )
            segments.append(segment)
        
        stack = TurnedPartStack(segments=segments)
        
        # Validate stack (additional validation from TurnedPartStack)
        stack_is_valid, stack_errors = stack.validate(tolerance=1e-6)
        if not stack_is_valid:
            errors.extend([f"Stack validation: {e}" for e in stack_errors])
            return {
                "job_id": job_id,
                "status": "FAILED",
                "summary": {},
                "totals": {},
                "warnings": warnings,
                "errors": errors,
                "outputs": []
            }
        
        # Save turned_stack.json
        outputs_path = self.file_storage.get_outputs_path(job_id)
        outputs_path.mkdir(parents=True, exist_ok=True)
        
        turned_stack_file = outputs_path / "turned_stack.json"
        turned_stack_data = {
            "units": units,
            "segments": segments_data,
            "notes": notes,
            "created_at_utc": datetime.now(timezone.utc).isoformat()
        }
        
        with open(turned_stack_file, 'w') as f:
            json.dump(turned_stack_data, f, indent=2)
        
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
        
        # Convert totals to expected format (manual stack service has detailed totals)
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
                "method": "manual",  # Manual stack input
                "confidence": 1.0,
                "notes": "Scale not applicable for manual stack input"
            },
            z_range=z_range,
            segments=segments_list,
            totals=totals_dict,
            inference_metadata={
                "mode": "reference_only",  # Manual input mode - PDF shown as reference only
                "overall_confidence": 1.0,  # Manual input is assumed correct
                "source": "math_stack_only"  # Math-only path, no OCC solid generated
            },
            features=None  # Features will be added later by feature detection
        )

        # Convert to dict for JSON serialization
        part_summary_dict = part_summary.to_dict()
        
        # Write part_summary.json
        summary_file = outputs_path / "part_summary.json"
        with open(summary_file, 'w') as f:
            json.dump(part_summary_dict, f, indent=2)
        
        # Generate human-readable explanation
        from app.utils.stack_explanation import generate_stack_explanation
        explanation = generate_stack_explanation(
            segments_list,
            units=units,
            overall_confidence=None  # Manual input has no confidence score
        )
        
        # Update job status
        self.job_service.job_storage.update_job_status(job_id, JobStatus.COMPLETED)
        
        return {
            "job_id": job_id,
            "status": "DONE",
            "summary": part_summary,
            "totals": totals,
            "warnings": warnings,
            "explanation": explanation,
            "outputs": ["turned_stack.json", "part_summary.json"]
        }
