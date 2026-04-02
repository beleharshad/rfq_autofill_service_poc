"""Service for generating STEP file from inferred stack."""

import json
import logging
import os
from pathlib import Path
from typing import Dict, Tuple, Optional, List

# Add project root to path
import sys
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from app.services.stack_to_profile_service import StackToProfileService
from app.utils.occ_available import occ_available, get_occ_error
from app.storage.file_storage import FileStorage

logger = logging.getLogger(__name__)


class StepFromStackService:
    """Service for generating STEP file from inferred stack."""
    
    def __init__(self):
        """Initialize step from stack service."""
        self.file_storage = FileStorage()
        self.stack_to_profile = StackToProfileService()
    
    def generate_step_from_inferred_stack(self, job_id: str, bore_diameter: float = 0.0) -> Dict:
        """Generate STEP file from inferred stack.
        
        Args:
            job_id: Job identifier
            bore_diameter: When >0, override all segment id_diameter values with this value
                           (inches) so the STEP/GLB renders as a hollow bore part.
            
        Returns:
            Dictionary with:
                - status: "OK" | "FAILED" | "UNAVAILABLE"
                - output_step_path: "outputs/model.step" (if OK)
                - message: Human-readable message
                - debug: Optional details dictionary
        """
        logger.info(f"[StepFromStack] Starting STEP generation for job_id: {job_id}")
        
        outputs_path = self.file_storage.get_outputs_path(job_id)
        inferred_stack_file = outputs_path / "inferred_stack.json"
        
        logger.info(f"[StepFromStack] FileStorage base_path: {self.file_storage.base_path}")
        logger.info(f"[StepFromStack] FileStorage base_path absolute: {self.file_storage.base_path.absolute()}")
        logger.info(f"[StepFromStack] outputs_path: {outputs_path}")
        logger.info(f"[StepFromStack] outputs_path absolute: {outputs_path.absolute()}")
        logger.info(f"[StepFromStack] inferred_stack_file: {inferred_stack_file}")
        logger.info(f"[StepFromStack] inferred_stack_file absolute: {inferred_stack_file.absolute()}")
        logger.info(f"[StepFromStack] inferred_stack_file exists: {inferred_stack_file.exists()}")
        
        if not inferred_stack_file.exists():
            return {
                "status": "FAILED",
                "message": "inferred_stack.json not found. Run auto-detect inference first.",
                "debug": {
                    "error": "inferred_stack.json not found",
                    "job_id": job_id
                }
            }
        
        # Check OCC availability
        if not occ_available():
            error_msg = get_occ_error() or "OCC not installed"
            return {
                "status": "UNAVAILABLE",
                "message": f"OCC (OpenCASCADE) is not installed. {error_msg}",
                "debug": {
                    "error": error_msg,
                    "job_id": job_id
                }
            }
        
        try:
            # Read inferred_stack.json
            with open(inferred_stack_file, 'r') as f:
                stack_data = json.load(f)
            
            segments = stack_data.get('segments', [])
            if not segments:
                return {
                    "status": "FAILED",
                    "message": "No segments found in inferred_stack.json",
                    "debug": {
                        "error": "No segments",
                        "job_id": job_id
                    }
                }

            # --- LLM-based geometry calibration ---
            # Scale all segment dimensions so the 3D model matches the LLM-extracted
            # od_in and length_in values (LLM is the authoritative source for dimensions).
            try:
                llm_analysis_file = outputs_path / "llm_analysis.json"
                if llm_analysis_file.exists():
                    llm_data = json.loads(llm_analysis_file.read_text(encoding="utf-8"))
                    llm_od = llm_data.get("extracted", {}).get("od_in")
                    llm_len = llm_data.get("extracted", {}).get("length_in")
                    if llm_od and llm_len and float(llm_od) > 0 and float(llm_len) > 0:
                        geom_max_od = max((float(s.get("od_diameter") or 0) for s in segments), default=0.0)
                        z_vals = (
                            [float(s.get("z_start") or 0) for s in segments]
                            + [float(s.get("z_end") or 0) for s in segments]
                        )
                        geom_total_len = (max(z_vals) - min(z_vals)) if z_vals else 0.0
                        if geom_max_od > 0 and geom_total_len > 0:
                            xy_scale = float(llm_od) / geom_max_od
                            z_min = min(z_vals)
                            z_scale = float(llm_len) / geom_total_len
                            calibrated = []
                            for s in segments:
                                ns = dict(s)
                                z0 = float(s.get("z_start") or 0)
                                z1 = float(s.get("z_end") or 0)
                                ns["z_start"] = round((z0 - z_min) * z_scale, 6)
                                ns["z_end"]   = round((z1 - z_min) * z_scale, 6)
                                ns["od_diameter"] = round(float(s.get("od_diameter") or 0) * xy_scale, 6)
                                if s.get("id_diameter") and float(s["id_diameter"]) > 0:
                                    ns["id_diameter"] = round(float(s["id_diameter"]) * xy_scale, 6)
                                if s.get("wall_thickness") and float(s.get("wall_thickness") or 0) > 0:
                                    ns["wall_thickness"] = round(float(s["wall_thickness"]) * xy_scale, 6)
                                calibrated.append(ns)
                            segments = calibrated
                            logger.info(
                                "[StepFromStack] LLM calibration: xy_scale=%.4f "
                                "(llm_od=%.4f / geom_od=%.4f), z_scale=%.4f "
                                "(llm_len=%.4f / geom_len=%.4f)",
                                xy_scale, float(llm_od), geom_max_od,
                                z_scale, float(llm_len), geom_total_len,
                            )
                        else:
                            logger.info("[StepFromStack] LLM calibration skipped: zero geometry dimensions")
                    else:
                        logger.info("[StepFromStack] LLM calibration skipped: LLM od_in or length_in not available")
            except Exception as _llm_cal_err:
                logger.debug("[StepFromStack] LLM-based calibration failed (non-fatal): %s", _llm_cal_err)

            # Apply bore diameter override — when the LLM knows the real ID but the
            # inferred_stack has id_diameter≈0 for most segments, this propagates the
            # correct bore so the solid is rendered hollow.
            if bore_diameter > 0.001:
                logger.info(f"[StepFromStack] Applying bore_diameter override: {bore_diameter:.4f} in")
                segments = [
                    {**s, "id_diameter": max(float(s.get("id_diameter") or 0), bore_diameter)}
                    for s in segments
                ]
            
            # Convert stack to Profile2D
            logger.info(f"[StepFromStack] Converting {len(segments)} segments to Profile2D")
            profile = self.stack_to_profile.build_profile2d_from_stack(segments)
            
            # Validate profile topology
            is_valid, validation_errors = profile.validate_topology(tolerance=1e-6)
            if not is_valid:
                return {
                    "status": "FAILED",
                    "message": f"Profile validation failed: {', '.join(validation_errors)}",
                    "debug": {
                        "error": "Profile validation failed",
                        "validation_errors": validation_errors,
                        "job_id": job_id
                    }
                }
            
            # Build OCC solid
            from app.geometry.revolved_solid_builder import RevolvedSolidBuilder, _STEP_EXPORT_AVAILABLE
            from app.geometry.geometry_2d import Point2D
            
            logger.info(f"[StepFromStack] Building OCC solid from profile")
            logger.info(f"[StepFromStack] STEP export available: {_STEP_EXPORT_AVAILABLE}")
            
            builder = RevolvedSolidBuilder()
            axis_pt = Point2D(0.0, 0.0)  # Default axis at origin
            builder.set_axis(axis_pt)
            
            success = builder.build_from_profile(profile)
            if not success:
                # Get more details about why build failed
                solid = builder.get_solid()
                return {
                    "status": "FAILED",
                    "message": "Failed to build solid from profile",
                    "debug": {
                        "error": "build_from_profile returned False",
                        "solid_is_none": solid is None,
                        "profile_primitives_count": len(profile.get_primitives()),
                        "job_id": job_id
                    }
                }
            
            # Verify solid was created
            solid = builder.get_solid()
            if solid is None or solid.IsNull():
                return {
                    "status": "FAILED",
                    "message": "Solid is None or invalid after build_from_profile",
                    "debug": {
                        "error": "solid is None or IsNull()",
                        "job_id": job_id
                    }
                }
            
            logger.info(f"[StepFromStack] Solid built successfully, solid is valid: {not solid.IsNull()}")
            
            # Export STEP
            step_file = outputs_path / "model.step"
            logger.info(f"[StepFromStack] Exporting STEP to {step_file}")
            logger.info(f"[StepFromStack] outputs_path exists: {outputs_path.exists()}")
            logger.info(f"[StepFromStack] outputs_path absolute: {outputs_path.absolute()}")
            
            # Ensure outputs directory exists
            outputs_path.mkdir(parents=True, exist_ok=True)
            
            try:
                # Verify solid exists before export
                solid = builder.get_solid()
                if solid is None or solid.IsNull():
                    raise Exception("Cannot export STEP: solid is None or invalid. build_from_profile may have failed.")
                
                # Check if STEP export is available - try to import directly to get better error message
                if not _STEP_EXPORT_AVAILABLE:
                    # Try to import directly to get the actual error
                    try:
                        from OCC.Core.STEPControl import STEPControl_Writer
                        from OCC.Core.Interface import Interface_Static
                        logger.info("[StepFromStack] STEP export modules imported successfully")
                    except ImportError as import_err:
                        error_msg = f"STEP export is not available. Failed to import OCC STEPControl_Writer: {import_err}. Check pythonocc-core installation."
                        logger.error(f"[StepFromStack] {error_msg}")
                        raise Exception(error_msg) from import_err
                    except Exception as import_err:
                        error_msg = f"STEP export is not available. Error importing OCC STEPControl_Writer: {import_err}. Check pythonocc-core installation."
                        logger.error(f"[StepFromStack] {error_msg}")
                        raise Exception(error_msg) from import_err
                
                # Export STEP file - check return value
                logger.info(f"[StepFromStack] Attempting to export STEP to: {step_file}")
                export_success = builder.export_step(str(step_file))
                
                if not export_success:
                    raise Exception(f"STEP export failed. writer.Transfer() or writer.Write() returned failure. Check OCC installation and file permissions.")
                
                # Verify file was actually created
                if not step_file.exists():
                    raise Exception(f"STEP file was not created at {step_file.absolute()} even though export_step returned True")
                
                file_size = step_file.stat().st_size
                logger.info(f"[StepFromStack] STEP file created successfully: {step_file.absolute()}, size: {file_size} bytes")
                
                if file_size == 0:
                    raise Exception("STEP file is empty (0 bytes)")
                
                # Update part_summary.json to indicate STEP was generated
                part_summary_file = outputs_path / "part_summary.json"
                if part_summary_file.exists():
                    with open(part_summary_file, 'r') as f:
                        part_summary = json.load(f)
                    
                    # Update inference_metadata source
                    if "inference_metadata" not in part_summary:
                        part_summary["inference_metadata"] = {}
                    part_summary["inference_metadata"]["source"] = "auto_convert_step"
                    
                    with open(part_summary_file, 'w') as f:
                        json.dump(part_summary, f, indent=2)
                
                # Verify file is accessible via file_storage
                try:
                    file_info = self.file_storage.get_file_info(job_id, "outputs/model.step")
                    logger.info(f"[StepFromStack] File verified via file_storage: {file_info}")
                except Exception as e:
                    logger.warning(f"[StepFromStack] Could not verify file via file_storage (may be timing issue): {e}")
                
                # Try to generate GLB if converter is available
                glb_file = outputs_path / "model.glb"
                glb_generated = False
                try:
                    from app.services.step_to_glb_converter import StepToGlbConverter
                    converter = StepToGlbConverter()
                    if converter.available:
                        logger.info(f"[StepFromStack] Converting STEP to GLB: {glb_file}")
                        glb_success, glb_error = converter.convert_step_to_glb(step_file, glb_file, check_cache=False)
                        if glb_success:
                            glb_generated = True
                            logger.info(f"[StepFromStack] GLB file generated successfully: {glb_file}")
                        else:
                            logger.warning(f"[StepFromStack] GLB conversion failed: {glb_error}")
                    else:
                        logger.info(f"[StepFromStack] GLB converter not available (trimesh/cascadio not installed)")
                except Exception as glb_ex:
                    # GLB conversion is optional, don't fail if it doesn't work
                    logger.warning(f"[StepFromStack] GLB conversion error (non-fatal): {glb_ex}")
                
                return {
                    "status": "OK",
                    "output_step_path": "outputs/model.step",
                    "message": "STEP file generated successfully",
                    "debug": {
                        "step_file": str(step_file),
                        "step_file_absolute": str(step_file.absolute()),
                        "step_file_exists": step_file.exists(),
                        "step_file_size": file_size,
                        "glb_generated": glb_generated,
                        "glb_file": str(glb_file) if glb_generated else None,
                        "segments_count": len(segments),
                        "job_id": job_id
                    }
                }
            except Exception as e:
                logger.error(f"[StepFromStack] Failed to export STEP: {e}", exc_info=True)
                return {
                    "status": "FAILED",
                    "message": f"Failed to export STEP: {str(e)}",
                    "debug": {
                        "error": str(e),
                        "error_type": type(e).__name__,
                        "job_id": job_id
                    }
                }
        except Exception as e:
            logger.error(f"[StepFromStack] Error generating STEP from stack: {e}", exc_info=True)
            return {
                "status": "FAILED",
                "message": f"STEP generation error: {str(e)}",
                "debug": {
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "job_id": job_id
                }
            }

