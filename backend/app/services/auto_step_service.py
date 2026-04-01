"""Service for generating STEP from auto-converted inferred stack."""

import json
import sys
from pathlib import Path
from typing import Dict, Optional, List, Tuple
from datetime import datetime, timezone

# Add project root to path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from app.geometry.feature_extractor import TurnedPartStack, TurnedPartSegment, FeatureExtractor
from app.geometry.geometry_2d import Profile2D, LineSegment, Point2D
from app.geometry.revolved_solid_builder import RevolvedSolidBuilder
from app.storage.file_storage import FileStorage
from app.services.job_service import JobService
from app.models.job import JobStatus
from app.models.part_summary import PartSummary


class AutoStepService:
    """Service for generating STEP from auto-converted inferred stack."""
    
    def __init__(self):
        """Initialize auto step service."""
        self.file_storage = FileStorage()
        self.job_service = JobService()
    
    def validate_step_safety(self, stack_data: Dict, part_summary: Optional[Dict] = None) -> Tuple[bool, List[str], str]:
        """Validate safety gate before STEP generation.
        
        Rules:
        - Do NOT generate STEP automatically if:
          - overall_confidence < 0.75
          - Any segment has confidence < 0.5
          - More than 1 segment has "thin_wall" flag
        
        Args:
            stack_data: Inferred stack data from inferred_stack.json
            part_summary: Optional part_summary.json data (for overall_confidence)
            
        Returns:
            Tuple of (is_safe, reasons, status)
            - is_safe: True if safe to generate STEP
            - reasons: List of reasons why not safe (if any)
            - status: "approved" if safe, "needs_review" if not
        """
        reasons = []
        
        # Check overall confidence
        overall_confidence = stack_data.get("overall_confidence", 0.0)
        if part_summary and "inference_metadata" in part_summary:
            overall_confidence = part_summary["inference_metadata"].get("overall_confidence", overall_confidence)
        
        if overall_confidence < 0.75:
            reasons.append(f"Overall confidence ({overall_confidence:.2f}) is below threshold (0.75)")
        
        # Check segment confidence
        segments = stack_data.get("segments", [])
        low_confidence_segments = []
        for i, seg in enumerate(segments):
            seg_conf = seg.get("confidence", 1.0)
            if seg_conf < 0.5:
                low_confidence_segments.append(i + 1)
        
        if low_confidence_segments:
            reasons.append(f"Segments {low_confidence_segments} have confidence < 0.5")
        
        # Check thin_wall flags
        thin_wall_count = 0
        for seg in segments:
            flags = seg.get("flags", [])
            if "thin_wall" in flags:
                thin_wall_count += 1
        
        if thin_wall_count > 1:
            reasons.append(f"More than 1 segment ({thin_wall_count}) has 'thin_wall' flag")
        
        if reasons:
            return False, reasons, "needs_review"
        else:
            return True, [], "approved"
    
    def segments_to_profile2d(self, segments_data: List[Dict]) -> Profile2D:
        """Convert inferred segments to Profile2D rectangular step profile.
        
        Args:
            segments_data: List of segment dictionaries with z_start, z_end, od_diameter, id_diameter
            
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
        current_od_radius = first_od_radius
        
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
        current_id_radius = last_id_radius
        
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
        
        return profile
    
    def generate_step_from_inferred_stack(self, job_id: str) -> Dict:
        """Generate STEP file from inferred stack.
        
        Args:
            job_id: Job identifier
            
        Returns:
            Dictionary with status, outputs, warnings, errors
        """
        outputs_path = self.file_storage.get_outputs_path(job_id)
        inferred_stack_file = outputs_path / "inferred_stack.json"
        
        if not inferred_stack_file.exists():
            return {
                "job_id": job_id,
                "status": "FAILED",
                "error": "inferred_stack.json not found. Run infer_stack first.",
                "outputs": []
            }
        
        # Read inferred stack
        with open(inferred_stack_file, 'r') as f:
            stack_data = json.load(f)
        
        segments_data = stack_data.get('segments', [])
        if not segments_data:
            return {
                "job_id": job_id,
                "status": "FAILED",
                "error": "No segments found in inferred_stack.json",
                "outputs": []
            }
        
        # Check approval status
        approval_file = outputs_path / "step_approval.json"
        approval_status = None
        if approval_file.exists():
            try:
                with open(approval_file, 'r') as f:
                    approval_data = json.load(f)
                    approval_status = approval_data.get("status")
            except Exception:
                pass
        
        # If not approved, run safety gate validation
        if approval_status != "approved":
            # Load part_summary.json if available for overall_confidence
            part_summary = None
            part_summary_file = outputs_path / "part_summary.json"
            if part_summary_file.exists():
                try:
                    with open(part_summary_file, 'r') as f:
                        part_summary = json.load(f)
                except Exception:
                    pass
            
            # Run safety gate validation
            is_safe, reasons, status = self.validate_step_safety(stack_data, part_summary)
            
            if not is_safe:
                # Save approval status
                approval_data = {
                    "status": status,
                    "reasons": reasons,
                    "checked_at_utc": datetime.now(timezone.utc).isoformat()
                }
                with open(approval_file, 'w') as f:
                    json.dump(approval_data, f, indent=2)
                
                return {
                    "job_id": job_id,
                    "status": "needs_review",
                    "reasons": reasons,
                    "message": "STEP generation requires review. Please review the inferred stack and approve manually.",
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
            print(f"[AutoStepService] Exporting STEP to: {step_file}")
            builder.export_step(str(step_file))
            print(f"[AutoStepService] STEP export successful, file exists: {step_file.exists()}")
            if step_file.exists():
                import os
                file_size = os.path.getsize(step_file)
                print(f"[AutoStepService] STEP file size: {file_size} bytes")
        except Exception as e:
            import traceback
            error_msg = f"Failed to export STEP: {str(e)}"
            print(f"[AutoStepService] STEP export failed: {error_msg}")
            print(f"[AutoStepService] Traceback: {traceback.format_exc()}")
            return {
                "job_id": job_id,
                "status": "FAILED",
                "error": error_msg,
                "traceback": traceback.format_exc(),
                "outputs": []
            }
        
        # Run FeatureExtractor to regenerate part_summary.json with feature counts
        try:
            print(f"[AutoStepService] Running FeatureExtractor...")
            extractor = FeatureExtractor()
            # FeatureExtractor.set_reference_axis() accepts Point2D or None (defaults to Z-axis)
            # Since we're using standard Z-axis revolution, pass None to use default
            print(f"[AutoStepService] Setting reference axis to default Z-axis...")
            extractor.set_reference_axis(None)  # None defaults to Z-axis through origin
            print(f"[AutoStepService] Extracting features from solid...")
            collection = extractor.extract_features(solid)
            print(f"[AutoStepService] Feature extraction successful")
            
            # Build turned part stack from feature collection
            print(f"[AutoStepService] Building turned part stack from features...")
            turned_stack = extractor.build_turned_part_stack(collection, tolerance=1e-6)
            if turned_stack is None or len(turned_stack.segments) == 0:
                return {
                    "job_id": job_id,
                    "status": "FAILED",
                    "error": "Failed to build turned part stack from features",
                    "outputs": []
                }
            print(f"[AutoStepService] Turned part stack built with {len(turned_stack.segments)} segments")
            
            # Compute z_range
            if turned_stack.segments:
                min_z = min(seg.z_start for seg in turned_stack.segments)
                max_z = max(seg.z_end for seg in turned_stack.segments)
                z_range = [min_z, max_z]
            else:
                z_range = [0.0, 0.0]
            
            # Build segments list for JSON (preserve confidence from inference)
            segments_list = []
            for i, seg in enumerate(turned_stack.segments):
                # Try to match with original inferred segments to preserve confidence
                inferred_seg = None
                if i < len(segments_data):
                    inferred_seg = segments_data[i]
                
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
                
                # Preserve confidence if available
                if inferred_seg and "confidence" in inferred_seg:
                    seg_dict["confidence"] = inferred_seg["confidence"]
                
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
            # Note: inferred_stack.json doesn't have units, default to "in"
            units = "in"
            overall_confidence = stack_data.get("overall_confidence", 0.0)
            generated_at_utc = datetime.now(timezone.utc).isoformat()
            
            # Determine mode from part_summary.json if it exists, otherwise default to "auto_detect"
            mode = "auto_detect"
            existing_summary_file = outputs_path / "part_summary.json"
            if existing_summary_file.exists():
                try:
                    with open(existing_summary_file, 'r') as f:
                        existing_summary = json.load(f)
                        existing_metadata = existing_summary.get("inference_metadata", {})
                        mode = existing_metadata.get("mode", "auto_detect")
                except Exception:
                    pass  # Use default mode
            
            # Convert totals to expected format (auto step service has detailed totals)
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
                    "method": "estimated",  # From inferred stack
                    "confidence": float(overall_confidence),
                    "notes": f"Scale calibrated from inferred stack, mode: {mode}"
                },
                z_range=z_range,
                segments=segments_list,
                totals=totals_dict,
                inference_metadata={
                    "mode": mode,  # "reference_only" or "auto_detect"
                    "overall_confidence": float(overall_confidence),
                    "source": "auto_convert_step"
                },
                features=None  # Features will be added later by feature detection
            )

            # Convert to dict for JSON serialization
            part_summary_dict = part_summary.to_dict()
            
            # Write part_summary.json
            summary_file = outputs_path / "part_summary.json"
            with open(summary_file, 'w') as f:
                json.dump(part_summary_dict, f, indent=2)
            
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
            
            warnings = stack_data.get("warnings", [])
            
            # Mark as approved after successful generation
            approval_file = outputs_path / "step_approval.json"
            approval_data = {
                "status": "approved",
                "approved_at_utc": datetime.now(timezone.utc).isoformat(),
                "generated_at_utc": generated_at_utc
            }
            with open(approval_file, 'w') as f:
                json.dump(approval_data, f, indent=2)
            
            # Generate human-readable explanation
            from app.utils.stack_explanation import generate_stack_explanation
            explanation = generate_stack_explanation(
                segments_list,
                units=units,
                overall_confidence=overall_confidence
            )
            
            return {
                "job_id": job_id,
                "status": "DONE",
                "outputs": outputs,
                "warnings": warnings,
                "overall_confidence": float(overall_confidence),
                "explanation": explanation
            }
            
        except Exception as e:
            import traceback
            error_msg = f"Failed to extract features: {str(e)}"
            print(f"[AutoStepService] Exception during STEP generation: {type(e).__name__}: {e}")
            print(f"[AutoStepService] Traceback: {traceback.format_exc()}")
            return {
                "job_id": job_id,
                "status": "FAILED",
                "error": error_msg,
                "traceback": traceback.format_exc(),
                "outputs": []
            }

