"""Service for converting STEP files to GLB format."""

import sys
from pathlib import Path
from typing import Optional, Tuple

# Add project root to path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

try:
    import trimesh
    _TRIMESH_AVAILABLE = True
except ImportError:
    _TRIMESH_AVAILABLE = False
    trimesh = None

try:
    import cascadio
    _CASCADIO_AVAILABLE = True
except ImportError:
    _CASCADIO_AVAILABLE = False
    cascadio = None


class StepToGlbConverter:
    """Converter for STEP to GLB format."""
    
    def __init__(self):
        """Initialize converter."""
        self.available = _TRIMESH_AVAILABLE or _CASCADIO_AVAILABLE
    
    def convert_step_to_glb(
        self,
        step_file: Path,
        glb_file: Path,
        check_cache: bool = True
    ) -> Tuple[bool, Optional[str]]:
        """Convert STEP file to GLB format.
        
        Args:
            step_file: Path to input STEP file
            glb_file: Path to output GLB file
            check_cache: If True, skip conversion if GLB is newer than STEP
            
        Returns:
            Tuple of (success, error_message)
        """
        if not self.available:
            return False, "No STEP to GLB converter available. Install 'trimesh' or 'cascadio'."
        
        if not step_file.exists():
            return False, f"STEP file not found: {step_file}"
        
        # Check cache
        if check_cache and glb_file.exists():
            step_mtime = step_file.stat().st_mtime
            glb_mtime = glb_file.stat().st_mtime
            if glb_mtime >= step_mtime:
                # GLB is up to date
                return True, None
        
        try:
            # Ensure output directory exists
            glb_file.parent.mkdir(parents=True, exist_ok=True)
            
            if _TRIMESH_AVAILABLE:
                # Use trimesh (preferred - more reliable)
                return self._convert_with_trimesh(step_file, glb_file)
            elif _CASCADIO_AVAILABLE:
                # Use cascadio as fallback
                return self._convert_with_cascadio(step_file, glb_file)
            else:
                return False, "No converter available"
        except Exception as e:
            return False, f"Conversion failed: {str(e)}"
    
    def _convert_with_trimesh(self, step_file: Path, glb_file: Path) -> Tuple[bool, Optional[str]]:
        """Convert using trimesh library.
        
        Args:
            step_file: Input STEP file
            glb_file: Output GLB file
            
        Returns:
            Tuple of (success, error_message)
        """
        try:
            # Load STEP file
            mesh = trimesh.load(str(step_file), file_type='step')
            
            # Handle different return types from trimesh.load
            if isinstance(mesh, trimesh.Scene):
                # If it's a scene, export the scene
                mesh.export(str(glb_file), file_type='glb')
            elif isinstance(mesh, trimesh.Trimesh):
                # If it's a single mesh, export it
                mesh.export(str(glb_file), file_type='glb')
            elif isinstance(mesh, list):
                # If it's a list of meshes, combine them
                combined = trimesh.util.concatenate(mesh)
                combined.export(str(glb_file), file_type='glb')
            else:
                return False, f"Unexpected mesh type: {type(mesh)}"
            
            return True, None
        except Exception as e:
            return False, f"Trimesh conversion failed: {str(e)}"
    
    def _convert_with_cascadio(self, step_file: Path, glb_file: Path) -> Tuple[bool, Optional[str]]:
        """Convert using cascadio library.
        
        Args:
            step_file: Input STEP file
            glb_file: Output GLB file
            
        Returns:
            Tuple of (success, error_message)
        """
        try:
            cascadio.convert(str(step_file), str(glb_file))
            return True, None
        except Exception as e:
            return False, f"Cascadio conversion failed: {str(e)}"





