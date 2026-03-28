"""Service for converting STEP files to GLB format."""

import sys
import tempfile
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

# Check OCC availability (for STEP → STL via tessellation)
try:
    from OCC.Core.STEPControl import STEPControl_Reader
    from OCC.Core.IFSelect import IFSelect_RetDone
    from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
    from OCC.Core.StlAPI import StlAPI_Writer
    from OCC.Core.TopExp import TopExp_Explorer
    from OCC.Core.TopAbs import TopAbs_SOLID
    _OCC_FOR_STEP = True
except ImportError:
    try:
        from OCC.Core.STEPControl import STEPControl_Reader   # type: ignore[assignment]
        _OCC_FOR_STEP = False
    except ImportError:
        _OCC_FOR_STEP = False


class StepToGlbConverter:
    """Converter for STEP to GLB format.

    Pipeline priority:
      1. OCC tessellation → STL → trimesh → GLB  (preferred: no cascadio needed)
      2. cascadio (if installed)
      3. trimesh direct STEP load (fallback, needs cascadio loader internally)
    """

    def __init__(self):
        """Initialize converter."""
        self.available = _TRIMESH_AVAILABLE or _CASCADIO_AVAILABLE or _OCC_FOR_STEP
    
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
            return False, "No STEP to GLB converter available. Install 'trimesh' or OCC (pythonocc)."
        
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
            
            # Priority 1: OCC tessellation → STL → trimesh → GLB (best quality)
            if _OCC_FOR_STEP and _TRIMESH_AVAILABLE:
                ok, err = self._convert_with_occ_tessellation(step_file, glb_file)
                if ok:
                    return True, None
                # Fall through to next method on failure

            # Priority 2: cascadio
            if _CASCADIO_AVAILABLE:
                return self._convert_with_cascadio(step_file, glb_file)

            # Priority 3: trimesh direct (requires cascadio loader — usually fails for STEP)
            if _TRIMESH_AVAILABLE:
                return self._convert_with_trimesh(step_file, glb_file)

            return False, "No converter available"
        except Exception as e:
            return False, f"Conversion failed: {str(e)}"

    def _convert_with_occ_tessellation(
        self, step_file: Path, glb_file: Path
    ) -> Tuple[bool, Optional[str]]:
        """Convert STEP → GLB via OCC tessellation → STL → trimesh.

        Uses BRepMesh_IncrementalMesh to triangulate the OCC B-Rep, writes an
        STL, then loads it with trimesh and exports to GLB.  No cascadio needed.
        """
        try:
            # 1. Read STEP into OCC shape
            reader = STEPControl_Reader()
            status = reader.ReadFile(str(step_file))
            if status != IFSelect_RetDone:
                return False, f"STEPControl_Reader failed with status {status}"
            reader.TransferRoots()
            shape = reader.OneShape()
            if shape is None or shape.IsNull():
                return False, "STEP file read produced a null shape"

            # 2. Tessellate
            mesh = BRepMesh_IncrementalMesh(shape, 0.01, False, 0.5, True)
            mesh.Perform()

            # 3. Write to STL (temporary file)
            with tempfile.NamedTemporaryFile(suffix=".stl", delete=False) as tmp:
                stl_path = Path(tmp.name)
            writer = StlAPI_Writer()
            writer.ASCIIMode = False  # binary STL
            ok = writer.Write(shape, str(stl_path))
            if not ok or not stl_path.exists() or stl_path.stat().st_size < 100:
                stl_path.unlink(missing_ok=True)
                return False, "StlAPI_Writer produced an empty/failed output"

            # 4. Load STL with trimesh and export to GLB
            tri = trimesh.load(str(stl_path), file_type="stl")
            stl_path.unlink(missing_ok=True)
            if isinstance(tri, trimesh.Scene):
                tri.export(str(glb_file), file_type="glb")
            elif isinstance(tri, trimesh.Trimesh):
                tri.export(str(glb_file), file_type="glb")
            else:
                return False, f"Unexpected trimesh type: {type(tri)}"

            return True, None
        except Exception as e:
            return False, f"OCC tessellation failed: {e}"
    
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








