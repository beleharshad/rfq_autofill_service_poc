"""
Phase 3: Feature Extraction Module

This module performs read-only analysis of TopoDS_Solid to extract
manufacturing-relevant features using deterministic geometric and topological rules.
"""

from typing import List, Optional, Dict, Set, Tuple
from enum import Enum
from dataclasses import dataclass, field
from OCC.Core.TopoDS import TopoDS_Solid, TopoDS_Shell, TopoDS_Face, TopoDS_Edge, TopoDS_Vertex
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_SHELL, TopAbs_FACE, TopAbs_EDGE, TopAbs_VERTEX, TopAbs_IN, TopAbs_OUT, TopAbs_ON
from OCC.Core.BRep import BRep_Tool
from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
from OCC.Core.GeomAbs import GeomAbs_Plane, GeomAbs_Cylinder, GeomAbs_Cone, GeomAbs_Sphere
from OCC.Core.gp import gp_Pnt, gp_Dir, gp_Ax1, gp_Pln, gp_Vec
from OCC.Core.Geom import Geom_Plane, Geom_CylindricalSurface, Geom_ConicalSurface
from OCC.Core.TopTools import TopTools_ListOfShape
from OCC.Core.BRepClass3d import BRepClass3d_SolidClassifier
from OCC.Core.GeomLProp import GeomLProp_SLProps
# Note: TopExp_MapShapesAndAncestors not available in this PythonOCC version
# from OCC.Core.TopExp import TopExp_MapShapesAndAncestors
import math
import json
import sys
from pathlib import Path
from datetime import datetime, timezone

# Try to import conventions module
backend_path = Path(__file__).parent / "backend"
if backend_path.exists():
    sys.path.insert(0, str(backend_path.parent))

try:
    from app.geometry.conventions import get_reference_axis, extract_axial_from_3d
    _CONVENTIONS_AVAILABLE = True
except ImportError:
    _CONVENTIONS_AVAILABLE = False


class SurfaceType(Enum):
    """Enumeration of surface types."""
    PLANE = "PLANE"
    CYLINDER = "CYLINDER"
    CONE = "CONE"
    SPHERE = "SPHERE"
    UNKNOWN = "UNKNOWN"


class BottomType(Enum):
    """Enumeration of hole bottom types."""
    FLAT = "FLAT"
    CONICAL = "CONICAL"
    SPHERICAL = "SPHERICAL"
    UNKNOWN = "UNKNOWN"
    NONE = "NONE"  # For through holes


class CylinderType(Enum):
    """Enumeration of cylinder feature types."""
    BOSS = "BOSS"
    SHAFT = "SHAFT"
    PILLAR = "PILLAR"
    UNKNOWN = "UNKNOWN"


class FaceOrientation(Enum):
    """Enumeration of planar face orientations."""
    TOP = "TOP"
    BOTTOM = "BOTTOM"
    SIDE = "SIDE"
    UNKNOWN = "UNKNOWN"


class CurveType(Enum):
    """Enumeration of curve types."""
    LINE = "LINE"
    CIRCLE = "CIRCLE"
    ARC = "ARC"
    SPLINE = "SPLINE"
    UNKNOWN = "UNKNOWN"


class EdgeType(Enum):
    """Enumeration of edge types."""
    SHARP = "SHARP"
    FILLET = "FILLET"
    CHAMFER = "CHAMFER"
    UNKNOWN = "UNKNOWN"


@dataclass
class HoleFeature:
    """Represents a hole feature (internal cylindrical void)."""
    axis: gp_Ax1
    diameter: float
    depth: float  # Use math.inf for through holes
    bottom_type: BottomType
    cylindrical_faces: List[TopoDS_Face] = field(default_factory=list)
    bottom_face: Optional[TopoDS_Face] = None
    top_face: Optional[TopoDS_Face] = None
    id: str = ""
    axial_extent: Optional[Tuple[float, float]] = None  # (t_min, t_max) along axis
    
    def is_through(self) -> bool:
        """Check if this is a through hole."""
        return math.isinf(self.depth)


@dataclass
class CylinderFeature:
    """Represents a cylindrical feature (boss, shaft, or pillar)."""
    axis: gp_Ax1
    radius: float
    height: float  # Use math.inf if extends beyond visible region
    cylindrical_face: TopoDS_Face
    end_faces: List[TopoDS_Face] = field(default_factory=list)
    feature_class: CylinderType = CylinderType.UNKNOWN
    is_external: bool = True
    id: str = ""
    axial_extent: Optional[Tuple[float, float]] = None  # (t_min, t_max) along axis


@dataclass
class PlanarFaceFeature:
    """Represents a planar (flat) face feature."""
    plane: gp_Pln
    face: TopoDS_Face
    normal: gp_Dir
    boundary_edges: List[TopoDS_Edge] = field(default_factory=list)
    area: float = 0.0
    orientation: FaceOrientation = FaceOrientation.UNKNOWN
    id: str = ""


@dataclass
class EdgeFeature:
    """Represents an edge feature."""
    edge: TopoDS_Edge
    curve_type: CurveType = CurveType.UNKNOWN
    adjacent_faces: List[TopoDS_Face] = field(default_factory=list)
    edge_type: EdgeType = EdgeType.UNKNOWN
    length: float = 0.0
    id: str = ""


@dataclass
class FeatureCollection:
    """Collection of extracted features."""
    holes: List[HoleFeature] = field(default_factory=list)
    cylinders: List[CylinderFeature] = field(default_factory=list)
    planar_faces: List[PlanarFaceFeature] = field(default_factory=list)
    edges: List[EdgeFeature] = field(default_factory=list)
    relationships: Dict[str, List[str]] = field(default_factory=dict)


@dataclass
class TurnedPartSegment:
    """Represents a single segment of a turned part stack."""
    z_start: float
    z_end: float
    od_diameter: float  # Outer diameter (0 if not defined)
    id_diameter: float  # Inner diameter (0 if no bore)
    wall_thickness: float  # (od_diameter - id_diameter) / 2.0
    flags: List[str] = field(default_factory=list)  # Quality flags: "auto_merged", "id_assumed_solid", "thin_wall", "short_segment", "low_confidence"
    
    def __post_init__(self):
        """Compute wall thickness after initialization."""
        if self.od_diameter > 0 and self.id_diameter > 0:
            self.wall_thickness = (self.od_diameter - self.id_diameter) / 2.0
        elif self.od_diameter > 0:
            self.wall_thickness = self.od_diameter / 2.0
        else:
            self.wall_thickness = 0.0
    
    def volume(self) -> float:
        """Compute volume of this segment.
        
        Volume = π * L * (ro² - ri²)
        where:
        - ro = od_diameter / 2 (outer radius)
        - ri = id_diameter / 2 (inner radius)
        - L = z_end - z_start (length)
        
        Returns:
            Volume in cubic inches
        """
        ro = self.od_diameter / 2.0
        ri = self.id_diameter / 2.0
        L = self.z_end - self.z_start
        return math.pi * L * (ro * ro - ri * ri)
    
    def od_surface_area(self) -> float:
        """Compute outer diameter (OD) cylindrical surface area of this segment.
        
        A_od = π * od_diameter * L
        where:
        - od_diameter = outer diameter
        - L = z_end - z_start (length)
        
        Returns:
            Outer diameter surface area in square inches
        """
        L = self.z_end - self.z_start
        return math.pi * self.od_diameter * L
    
    def id_surface_area(self) -> float:
        """Compute inner diameter (ID) cylindrical surface area of this segment.
        
        A_id = π * id_diameter * L (if id_diameter > 0 else 0)
        where:
        - id_diameter = inner diameter
        - L = z_end - z_start (length)
        
        Returns:
            Inner diameter surface area in square inches (0 if no bore)
        """
        if self.id_diameter > 0:
            L = self.z_end - self.z_start
            return math.pi * self.id_diameter * L
        else:
            return 0.0


@dataclass
class TurnedPartStack:
    """Represents a turned part as a stack of consecutive segments."""
    segments: List[TurnedPartSegment] = field(default_factory=list)
    
    def validate(self, tolerance: float = 1e-6) -> Tuple[bool, List[str]]:
        """Validate the stack structure.
        
        Args:
            tolerance: Numerical tolerance for comparisons
            
        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        errors = []
        
        if not self.segments:
            errors.append("Stack has no segments")
            return (False, errors)
        
        # Check: every segment has an OD
        for i, seg in enumerate(self.segments):
            if seg.od_diameter <= 0:
                errors.append(f"Segment {i} (Z=[{seg.z_start:.6f}, {seg.z_end:.6f}]) has no OD")
        
        # Check: ID <= OD for all segments
        for i, seg in enumerate(self.segments):
            if seg.id_diameter > seg.od_diameter + tolerance:
                errors.append(f"Segment {i} (Z=[{seg.z_start:.6f}, {seg.z_end:.6f}]) has ID ({seg.id_diameter:.6f}) > OD ({seg.od_diameter:.6f})")
        
        # Check: segments cover [minZ, maxZ] with no gaps
        if len(self.segments) > 0:
            min_z = min(seg.z_start for seg in self.segments)
            max_z = max(seg.z_end for seg in self.segments)
            
            # Sort segments by z_start
            sorted_segments = sorted(self.segments, key=lambda s: s.z_start)
            
            # Check continuity
            for i in range(len(sorted_segments) - 1):
                current_end = sorted_segments[i].z_end
                next_start = sorted_segments[i + 1].z_start
                if abs(current_end - next_start) > tolerance:
                    errors.append(f"Gap between segment {i} (ends at {current_end:.6f}) and segment {i+1} (starts at {next_start:.6f})")
            
            # Check first segment starts at minZ
            if abs(sorted_segments[0].z_start - min_z) > tolerance:
                errors.append(f"First segment does not start at minimum Z ({min_z:.6f})")
            
            # Check last segment ends at maxZ
            if abs(sorted_segments[-1].z_end - max_z) > tolerance:
                errors.append(f"Last segment does not end at maximum Z ({max_z:.6f})")
        
        return (len(errors) == 0, errors)
    
    def total_volume(self) -> float:
        """Compute total volume of all segments.
        
        Returns:
            Total volume in cubic inches
        """
        return sum(seg.volume() for seg in self.segments)
    
    def compute_weight(self, density_lb_per_in3: Optional[float] = None, density_g_per_cm3: Optional[float] = None) -> Optional[float]:
        """Compute weight from volume and material density.
        
        Args:
            density_lb_per_in3: Material density in pounds per cubic inch
            density_g_per_cm3: Material density in grams per cubic cubic centimeter
        
        Returns:
            Weight in pounds (if density_lb_per_in3 provided) or None
        
        Note:
            Only one density parameter should be provided. If both are provided,
            density_lb_per_in3 takes precedence.
        """
        if density_lb_per_in3 is not None:
            return self.total_volume() * density_lb_per_in3
        elif density_g_per_cm3 is not None:
            # Convert g/cm³ to lb/in³: 1 g/cm³ = 0.0361273 lb/in³
            density_lb_per_in3 = density_g_per_cm3 * 0.0361273
            return self.total_volume() * density_lb_per_in3
        else:
            return None
    
    def total_od_surface_area(self) -> float:
        """Compute total outer diameter (OD) surface area of all segments.
        
        Returns:
            Total OD surface area in square inches
        """
        return sum(seg.od_surface_area() for seg in self.segments)
    
    def total_id_surface_area(self) -> float:
        """Compute total inner diameter (ID) surface area of all segments.
        
        Returns:
            Total ID surface area in square inches
        """
        return sum(seg.id_surface_area() for seg in self.segments)
    
    def _compute_end_face_area(self, seg: 'TurnedPartSegment') -> float:
        """Compute end face area for a segment.
        
        A_end = π * (ro² - ri²)
        where:
        - ro = od_diameter / 2 (outer radius)
        - ri = id_diameter / 2 (inner radius)
        
        Args:
            seg: Segment to compute end face area for
        
        Returns:
            End face area in square inches
        """
        ro = seg.od_diameter / 2.0
        ri = seg.id_diameter / 2.0
        return math.pi * (ro * ro - ri * ri)
    
    def end_face_area_start(self) -> float:
        """Compute end face area at start (minZ) using first segment.
        
        Returns:
            End face area at start in square inches
        """
        if not self.segments:
            return 0.0
        sorted_segments = sorted(self.segments, key=lambda s: s.z_start)
        return self._compute_end_face_area(sorted_segments[0])
    
    def end_face_area_end(self) -> float:
        """Compute end face area at end (maxZ) using last segment.
        
        Returns:
            End face area at end in square inches
        """
        if not self.segments:
            return 0.0
        sorted_segments = sorted(self.segments, key=lambda s: s.z_start)
        return self._compute_end_face_area(sorted_segments[-1])
    
    def od_shoulder_area(self) -> float:
        """Compute total OD shoulder area at all internal boundaries.
        
        For each internal boundary between segment i and i+1:
        od_shoulder_area += π * abs(ro_i² - ro_{i+1}²)
        
        Returns:
            Total OD shoulder area in square inches
        """
        if len(self.segments) < 2:
            return 0.0
        
        sorted_segments = sorted(self.segments, key=lambda s: s.z_start)
        total_od_shoulder = 0.0
        
        for i in range(len(sorted_segments) - 1):
            seg_i = sorted_segments[i]
            seg_next = sorted_segments[i + 1]
            
            ro_i = seg_i.od_diameter / 2.0
            ro_next = seg_next.od_diameter / 2.0
            a_od_shoulder = math.pi * abs(ro_i * ro_i - ro_next * ro_next)
            total_od_shoulder += a_od_shoulder
        
        return total_od_shoulder
    
    def id_shoulder_area(self) -> float:
        """Compute total ID shoulder area at all internal boundaries.
        
        For each internal boundary between segment i and i+1:
        id_shoulder_area += π * abs(ri_i² - ri_{i+1}²)
        
        Returns:
            Total ID shoulder area in square inches
        """
        if len(self.segments) < 2:
            return 0.0
        
        sorted_segments = sorted(self.segments, key=lambda s: s.z_start)
        total_id_shoulder = 0.0
        
        for i in range(len(sorted_segments) - 1):
            seg_i = sorted_segments[i]
            seg_next = sorted_segments[i + 1]
            
            ri_i = seg_i.id_diameter / 2.0
            ri_next = seg_next.id_diameter / 2.0
            a_id_shoulder = math.pi * abs(ri_i * ri_i - ri_next * ri_next)
            total_id_shoulder += a_id_shoulder
        
        return total_id_shoulder
    
    def total_planar_ring_area(self) -> float:
        """Compute total planar ring area at all Z boundaries.
        
        Includes:
        - End face areas at start (minZ) and end (maxZ)
        - OD and ID shoulder areas at internal boundaries
        
        Returns:
            Total planar ring area in square inches
        """
        return self.end_face_area_start() + self.end_face_area_end() + self.od_shoulder_area() + self.id_shoulder_area()
    
    def total_surface_area(self) -> float:
        """Compute total surface area including cylindrical and planar ring areas.
        
        Total = OD_area + ID_area + planar_ring_area
        
        Returns:
            Total surface area in square inches
        """
        return self.total_od_surface_area() + self.total_id_surface_area() + self.total_planar_ring_area()
    
    def to_dict(self, collection: Optional['FeatureCollection'] = None) -> Dict:
        """Export stack data to a dictionary for JSON serialization.
        
        Args:
            collection: Optional FeatureCollection for feature counts. If None,
                       feature_counts will have zeros.
        
        Returns:
            Dictionary with units, z_range, segments, totals, and feature_counts
        """
        # Compute z_range
        if self.segments:
            min_z = min(seg.z_start for seg in self.segments)
            max_z = max(seg.z_end for seg in self.segments)
            z_range = [min_z, max_z]
        else:
            z_range = [0.0, 0.0]
        
        # Build segments list
        segments_list = []
        for seg in self.segments:
            seg_dict = {
                "z_start": seg.z_start,
                "z_end": seg.z_end,
                "od_diameter": seg.od_diameter,
                "id_diameter": seg.id_diameter,
                "wall_thickness": seg.wall_thickness,
                "volume_in3": seg.volume(),
                "od_area_in2": seg.od_surface_area(),
                "id_area_in2": seg.id_surface_area(),
                "flags": seg.flags if hasattr(seg, 'flags') else []
            }
            segments_list.append(seg_dict)
        
        # Compute totals
        totals = {
            "volume_in3": self.total_volume(),
            "od_area_in2": self.total_od_surface_area(),
            "id_area_in2": self.total_id_surface_area(),
            "end_face_area_start_in2": self.end_face_area_start(),
            "end_face_area_end_in2": self.end_face_area_end(),
            "od_shoulder_area_in2": self.od_shoulder_area(),
            "id_shoulder_area_in2": self.id_shoulder_area(),
            "planar_ring_area_in2": self.total_planar_ring_area(),
            "total_surface_area_in2": self.total_surface_area()
        }
        
        # Compute feature counts
        if collection is not None:
            external_cylinders = sum(1 for cyl in collection.cylinders if cyl.is_external)
            internal_bores = len(collection.holes)
            planar_faces = len(collection.planar_faces)
            total_faces = external_cylinders + internal_bores + planar_faces
        else:
            external_cylinders = 0
            internal_bores = 0
            planar_faces = 0
            total_faces = 0
        
        feature_counts = {
            "external_cylinders": external_cylinders,
            "internal_bores": internal_bores,
            "planar_faces": planar_faces,
            "total_faces": total_faces
        }
        
        # Generate ISO timestamp in UTC
        generated_at_utc = datetime.now(timezone.utc).isoformat()
        
        return {
            "schema_version": "0.1",
            "generated_at_utc": generated_at_utc,
            "units": {
                "length": "in",
                "area": "in^2",
                "volume": "in^3"
            },
            "z_range": z_range,
            "segments": segments_list,
            "totals": totals,
            "feature_counts": feature_counts
        }
    
    def export_json(self, path: str, collection: Optional['FeatureCollection'] = None) -> None:
        """Export stack data to a JSON file.
        
        Args:
            path: File path to write JSON to
            collection: Optional FeatureCollection for feature counts. If None,
                       feature_counts will have zeros.
        """
        data = self.to_dict(collection)
        with open(path, 'w') as f:
            json.dump(data, f, indent=2)


class FeatureExtractor:
    """Extracts manufacturing features from a TopoDS_Solid."""
    
    def __init__(self, tolerance: float = 1e-6, debug: bool = False, use_radius_fallback: bool = False):
        """Initialize the feature extractor.
        
        Args:
            tolerance: Geometric tolerance for comparisons
            debug: If True, enable debug output. Default False.
            use_radius_fallback: If True, use radius-based fallback for axisymmetric parts.
                                Default False (use point-in-solid classification).
        """
        self.tolerance = tolerance
        self._debug = debug
        self._use_radius_fallback = use_radius_fallback
        self._face_to_shell_map: Dict[TopoDS_Face, TopoDS_Shell] = {}
        self._edge_to_faces_map: Dict[TopoDS_Edge, List[TopoDS_Face]] = {}
        self._solid_bbox_cache: Optional[Dict[str, float]] = None  # Cache for bbox diagonal
        # Reference axis: Uses standard convention (Z-axis) via conventions module
        self._ref_axis: Optional[gp_Ax1] = None
        # Initialize with default axis
        self.set_reference_axis()
    
    def extract_features(self, solid: TopoDS_Solid) -> FeatureCollection:
        """Extract all features from a solid.
        
        Args:
            solid: TopoDS_Solid to analyze
            
        Returns:
            FeatureCollection containing all extracted features
        """
        # Clear bbox cache for this solid (in case extractor is reused)
        self._solid_bbox_cache = None
        
        collection = FeatureCollection()
        
        # Validate input
        if solid.IsNull():
            return collection
        
        # Extract all faces
        all_faces = self._extract_all_faces(solid)
        
        # Step 1: Debug counting of cylindrical faces directly
        debug_cyl_faces = []
        if self._debug:
            from OCC.Core.TopAbs import TopAbs_SOLID
            print(f"DEBUG: solid.ShapeType() = {solid.ShapeType()}")
            print(f"DEBUG: Faces extracted: {len(all_faces)}")
            
            # Face type histogram and collect cylindrical faces
            type_counts = {}
            type_names = {
                GeomAbs_Plane: "GeomAbs_Plane",
                GeomAbs_Cylinder: "GeomAbs_Cylinder",
                GeomAbs_Cone: "GeomAbs_Cone",
                GeomAbs_Sphere: "GeomAbs_Sphere",
            }
            for face in all_faces:
                adaptor = BRepAdaptor_Surface(face, True)
                surface_type = adaptor.GetType()
                type_name = type_names.get(surface_type, f"GeomAbs_Unknown({surface_type})")
                type_counts[type_name] = type_counts.get(type_name, 0) + 1
                
                # Collect cylindrical faces for debug
                if surface_type == GeomAbs_Cylinder:
                    debug_cyl_faces.append(face)
            
            print("DEBUG: Face type histogram:")
            for type_name, count in sorted(type_counts.items()):
                print(f"  {type_name}: {count}")
            print(f"DEBUG: Direct cylindrical face count: {len(debug_cyl_faces)}")
            print()
        
        # Build topology maps
        self._build_topology_maps(solid)
        
        # Extract and classify all faces
        classified_faces = self._classify_faces(all_faces)
        
        if self._debug:
            print(f"DEBUG: Classified faces - Planar: {len(classified_faces.planar)}, Cylindrical: {len(classified_faces.cylindrical)}")
            print(f"DEBUG: Expected cylindrical faces (direct count): {len(debug_cyl_faces)}")
            print(f"DEBUG: Classified cylindrical faces: {len(classified_faces.cylindrical)}")
            if len(debug_cyl_faces) != len(classified_faces.cylindrical):
                print(f"DEBUG: WARNING - Mismatch between direct count and classified count!")
            print()
        
        # Detect holes (internal cylindrical features)
        holes = self._detect_holes(solid, classified_faces.cylindrical, debug_cyl_faces if self._debug else [])
        collection.holes = holes
        
        # Detect cylinders (external cylindrical features)
        try:
            cylinders = self._detect_cylinders(solid, classified_faces.cylindrical, debug_cyl_faces if self._debug else [])
            if cylinders is None:
                cylinders = []
            collection.cylinders = cylinders
        except Exception as e:
            if self._debug:
                print(f"DEBUG: Exception in _detect_cylinders: {e}")
            collection.cylinders = []
        
        # Step 2: Compare counts
        if self._debug:
            total_collected = len(collection.cylinders) + len(collection.holes)
            print(f"DEBUG: Comparison - Direct count: {len(debug_cyl_faces)}, Collection count: {total_collected}")
            if len(debug_cyl_faces) != total_collected:
                print(f"DEBUG: MISSING {len(debug_cyl_faces) - total_collected} cylindrical faces!")
            print()
        
        # Extract planar faces
        planar_faces = self._detect_planar_faces(solid, classified_faces.planar)
        collection.planar_faces = planar_faces
        
        # Extract edges (optional, can be added if needed)
        # edges = self._detect_edges(solid)
        # collection.edges = edges
        
        # Build relationships
        collection.relationships = self._build_relationships(collection)
        
        return collection
    
    def _build_topology_maps(self, solid: TopoDS_Solid) -> None:
        """Build maps of topological relationships."""
        self._face_to_shell_map.clear()
        self._edge_to_faces_map.clear()
        
        # Map faces to shells
        shell_exp = TopExp_Explorer(solid, TopAbs_SHELL)
        while shell_exp.More():
            shell_shape = shell_exp.Current()
            shell = TopoDS_Shell()
            shell.TShape(shell_shape.TShape())
            shell.Location(shell_shape.Location())
            shell.Orientation(shell_shape.Orientation())
            face_exp = TopExp_Explorer(shell, TopAbs_FACE)
            while face_exp.More():
                face_shape = face_exp.Current()
                face = TopoDS_Face()
                face.TShape(face_shape.TShape())
                face.Location(face_shape.Location())
                face.Orientation(face_shape.Orientation())
                self._face_to_shell_map[face] = shell
                face_exp.Next()
            shell_exp.Next()
        
        # Map edges to faces
        # Note: TopExp_MapShapesAndAncestors not available in this PythonOCC version
        # This mapping is not currently implemented - edge_to_faces_map remains empty
        # face_list = TopTools_ListOfShape()
        # edge_list = TopTools_ListOfShape()
        # TopExp_MapShapesAndAncestors(solid, TopAbs_EDGE, TopAbs_FACE, face_list, edge_list)
    
    def _extract_all_faces(self, solid: TopoDS_Solid) -> List[TopoDS_Face]:
        """Extract all faces from a solid."""
        faces = []
        exp = TopExp_Explorer(solid, TopAbs_FACE)
        while exp.More():
            face_shape = exp.Current()
            face = TopoDS_Face()
            face.TShape(face_shape.TShape())
            face.Location(face_shape.Location())
            face.Orientation(face_shape.Orientation())
            faces.append(face)
            exp.Next()
        return faces
    
    def _classify_surface_type(self, face: TopoDS_Face) -> SurfaceType:
        """Classify the surface type of a face."""
        try:
            adaptor = BRepAdaptor_Surface(face, True)
            surface_type = adaptor.GetType()
            
            if surface_type == GeomAbs_Plane:
                return SurfaceType.PLANE
            elif surface_type == GeomAbs_Cylinder:
                return SurfaceType.CYLINDER
            elif surface_type == GeomAbs_Cone:
                return SurfaceType.CONE
            elif surface_type == GeomAbs_Sphere:
                return SurfaceType.SPHERE
            else:
                return SurfaceType.UNKNOWN
        except Exception:
            return SurfaceType.UNKNOWN
    
    @dataclass
    class ClassifiedFaces:
        """Container for classified faces."""
        planar: List[TopoDS_Face] = field(default_factory=list)
        cylindrical: List[TopoDS_Face] = field(default_factory=list)
        conical: List[TopoDS_Face] = field(default_factory=list)
        spherical: List[TopoDS_Face] = field(default_factory=list)
        other: List[TopoDS_Face] = field(default_factory=list)
    
    def _classify_faces(self, faces: List[TopoDS_Face]) -> ClassifiedFaces:
        """Classify faces by surface type."""
        result = self.ClassifiedFaces()
        
        for face in faces:
            surface_type = self._classify_surface_type(face)
            
            if surface_type == SurfaceType.PLANE:
                result.planar.append(face)
            elif surface_type == SurfaceType.CYLINDER:
                result.cylindrical.append(face)
            elif surface_type == SurfaceType.CONE:
                result.conical.append(face)
            elif surface_type == SurfaceType.SPHERE:
                result.spherical.append(face)
            else:
                result.other.append(face)
        
        return result
    
    def _get_model_scale(self, solid: TopoDS_Solid) -> float:
        """Compute model scale as bbox diagonal."""
        if self._solid_bbox_cache is None:
            try:
                from OCC.Core.Bnd import Bnd_Box
                from OCC.Core.BRepBndLib import brepbndlib_Add
                
                bbox = Bnd_Box()
                brepbndlib_Add(solid, bbox)
                x_min, y_min, z_min, x_max, y_max, z_max = bbox.Get()
                
                dx = x_max - x_min
                dy = y_max - y_min
                dz = z_max - z_min
                diagonal = math.sqrt(dx*dx + dy*dy + dz*dz)
                
                self._solid_bbox_cache = {'diagonal': diagonal}
            except Exception:
                # Fallback: use default scale
                self._solid_bbox_cache = {'diagonal': 10.0}
        
        return self._solid_bbox_cache['diagonal']
    
    def get_epsilon(self, solid: TopoDS_Solid) -> float:
        """Get the epsilon value used for point-in-solid classification.
        
        Args:
            solid: TopoDS_Solid to compute epsilon for
            
        Returns:
            Epsilon value (in inches) used for classification
        """
        model_scale = self._get_model_scale(solid)
        eps = 1e-6 * model_scale
        # Clamp epsilon to safe bounds (units: inches)
        eps = max(eps, 1e-8)
        eps = min(eps, 1e-3)
        return eps
    
    def _sample_face_point_and_normal(self, face: TopoDS_Face, u: Optional[float] = None, v: Optional[float] = None) -> Optional[Tuple[gp_Pnt, gp_Dir]]:
        """Sample a point and normal from face at specified UV coordinates.
        
        Args:
            face: Face to sample from
            u: U parameter (if None, uses midpoint)
            v: V parameter (if None, uses midpoint)
        
        Returns:
            Tuple of (point, normal) or None if sampling fails.
        """
        try:
            adaptor = BRepAdaptor_Surface(face, True)
            
            # Get UV parameter range
            u_first, u_last, v_first, v_last = adaptor.FirstUParameter(), adaptor.LastUParameter(), adaptor.FirstVParameter(), adaptor.LastVParameter()
            
            # Use provided UV or default to midpoint
            if u is None:
                u = (u_first + u_last) / 2.0
            if v is None:
                v = (v_first + v_last) / 2.0
            
            # Get surface from adaptor
            surface = adaptor.Surface().Surface()
            
            # Use GeomLProp_SLProps to get point and normal
            props = GeomLProp_SLProps(surface, u, v, 1, 1e-6)
            
            if not props.IsNormalDefined():
                return None
            
            point = props.Value()
            normal = props.Normal()
            
            return (point, normal)
        except Exception as e:
            if self._debug:
                print(f"DEBUG: _sample_face_point_and_normal failed: {e}")
            return None
    
    def _sample_multiple_uv_points(self, face: TopoDS_Face) -> List[Tuple[gp_Pnt, gp_Dir]]:
        """Sample 3 UV points from face: midpoint + two offset points inside UV bounds.
        
        Returns:
            List of (point, normal) tuples. May have fewer than 3 if sampling fails.
        """
        samples = []
        try:
            adaptor = BRepAdaptor_Surface(face, True)
            u_first, u_last, v_first, v_last = adaptor.FirstUParameter(), adaptor.LastUParameter(), adaptor.FirstVParameter(), adaptor.LastVParameter()
            
            # Midpoint
            u_mid = (u_first + u_last) / 2.0
            v_mid = (v_first + v_last) / 2.0
            
            # Two offset points (25% and 75% of range)
            u_offset1 = u_first + (u_last - u_first) * 0.25
            v_offset1 = v_first + (v_last - v_first) * 0.25
            u_offset2 = u_first + (u_last - u_first) * 0.75
            v_offset2 = v_first + (v_last - v_first) * 0.75
            
            # Sample all three points
            for u, v in [(u_mid, v_mid), (u_offset1, v_offset1), (u_offset2, v_offset2)]:
                sample = self._sample_face_point_and_normal(face, u, v)
                if sample is not None:
                    samples.append(sample)
        except Exception:
            pass
        
        return samples
    
    def _is_internal_face(self, face: TopoDS_Face, solid: TopoDS_Solid) -> bool:
        """Determine if a face is internal (belongs to a void/hole) using point-in-solid classification.
        
        Uses standard boundary test: sample point P with normal N, then classify P + eps*N and P - eps*N.
        If ambiguous, samples 3 UV points and uses majority vote. Falls back to radius-based
        classification for cylindrical faces if still ambiguous.
        
        Args:
            face: Face to classify
            solid: Solid containing the face
        
        Returns:
            True if internal, False if external. Always returns a bool (never None).
        """
        from OCC.Core.TopAbs import TopAbs_IN, TopAbs_OUT, TopAbs_ON
        
        # Check if this is a cylindrical face
        try:
            adaptor = BRepAdaptor_Surface(face, True)
            is_cylindrical = (adaptor.GetType() == GeomAbs_Cylinder)
        except Exception:
            is_cylindrical = False
        
        # If radius fallback is explicitly enabled, use it
        if self._use_radius_fallback:
            return self._is_internal_face_radius_fallback(face, solid)
        
        # Get epsilon (already clamped)
        eps = self.get_epsilon(solid)
        
        def classify_at_point(P: gp_Pnt, N: gp_Dir) -> Tuple[int, int]:
            """Classify points P + eps*N and P - eps*N.
            
            Returns:
                Tuple of (state_out, state_in) where each is TopAbs_IN, TopAbs_OUT, or TopAbs_ON
            """
            classifier = BRepClass3d_SolidClassifier(solid)
            
            # Normalize normal vector
            N_vec = gp_Vec(N)
            N_vec.Normalize()
            N_scaled = gp_Vec(
                N_vec.X() * eps,
                N_vec.Y() * eps,
                N_vec.Z() * eps
            )
            
            # Test points: P_out = P + eps*N, P_in = P - eps*N
            P_out = gp_Pnt(
                P.X() + N_scaled.X(),
                P.Y() + N_scaled.Y(),
                P.Z() + N_scaled.Z()
            )
            P_in = gp_Pnt(
                P.X() - N_scaled.X(),
                P.Y() - N_scaled.Y(),
                P.Z() - N_scaled.Z()
            )
            
            classifier.Perform(P_out, self.tolerance)
            state_out = classifier.State()
            
            classifier.Perform(P_in, self.tolerance)
            state_in = classifier.State()
            
            return (state_out, state_in)
        
        def interpret_classification(state_out: int, state_in: int) -> Optional[bool]:
            """Interpret classification results.
            
            Returns:
                True if internal, False if external, None if ambiguous
            """
            # Standard boundary test:
            # External: state_out == OUT and state_in == IN (normal points outward)
            # Internal: state_out == IN and state_in == OUT (normal points inward)
            if state_out == TopAbs_OUT and state_in == TopAbs_IN:
                return False  # External
            elif state_out == TopAbs_IN and state_in == TopAbs_OUT:
                return True  # Internal
            else:
                return None  # Ambiguous (OUT/OUT, IN/IN, or ON)
        
        # Try primary sample (midpoint)
        try:
            sample_result = self._sample_face_point_and_normal(face)
            if sample_result is None:
                if self._debug:
                    print("DEBUG: _is_internal_face: Failed to sample point/normal")
                # Fallback for cylindrical faces
                if is_cylindrical:
                    return self._is_internal_face_radius_fallback(face, solid)
                return False  # Default to external
            
            P, N = sample_result
            state_out, state_in = classify_at_point(P, N)
            
            if self._debug:
                state_names = {TopAbs_IN: "IN", TopAbs_OUT: "OUT", TopAbs_ON: "ON"}
                print(f"DEBUG: _is_internal_face: P_out={state_names.get(state_out, 'UNKNOWN')}, P_in={state_names.get(state_in, 'UNKNOWN')}")
            
            result = interpret_classification(state_out, state_in)
            
            # For cylindrical faces on axisymmetric parts, the standard boundary test
            # often gives incorrect results (all normals point toward axis).
            # Use radius fallback for cylindrical faces.
            if is_cylindrical:
                if self._debug:
                    state_names = {TopAbs_IN: "IN", TopAbs_OUT: "OUT", TopAbs_ON: "ON"}
                    print(f"DEBUG: _is_internal_face: Cylindrical face, standard test: P_out={state_names.get(state_out, 'UNKNOWN')}, P_in={state_names.get(state_in, 'UNKNOWN')}, using radius fallback")
                return self._is_internal_face_radius_fallback(face, solid)
            
            # For non-cylindrical faces, use standard test result
            if result is not None:
                return result
            
            # Ambiguous - try 3 UV points and majority vote
            if self._debug:
                print("DEBUG: _is_internal_face: Ambiguous, sampling 3 UV points for majority vote")
            
            samples = self._sample_multiple_uv_points(face)
            if len(samples) < 1:
                # No samples available - default to external
                return False
            
            # Classify each sample and collect votes
            votes_internal = 0
            votes_external = 0
            
            for P_sample, N_sample in samples:
                state_out_sample, state_in_sample = classify_at_point(P_sample, N_sample)
                result_sample = interpret_classification(state_out_sample, state_in_sample)
                
                if result_sample is True:
                    votes_internal += 1
                elif result_sample is False:
                    votes_external += 1
                # Ambiguous votes are ignored
            
            if self._debug:
                print(f"DEBUG: _is_internal_face: Majority vote - internal={votes_internal}, external={votes_external}")
            
            # Majority vote
            if votes_internal > votes_external:
                return True
            elif votes_external > votes_internal:
                return False
            else:
                # Tie or all ambiguous - default to external
                return False
        
        except Exception as e:
            if self._debug:
                print(f"DEBUG: _is_internal_face: Exception: {e}, using radius fallback")
            # Fallback for cylindrical faces
            if is_cylindrical:
                return self._is_internal_face_radius_fallback(face, solid)
            return False  # Default to external
    
    def _is_internal_face_radius_fallback(self, face: TopoDS_Face, solid: TopoDS_Solid) -> bool:
        """Fallback heuristic: classify face as internal if it's a cylinder with smaller radius.
        
        For axisymmetric parts: internal faces (holes/bores) have smaller radii than external faces.
        Uses a smarter approach: finds all distinct radii and classifies based on relative sizes.
        """
        try:
            # Only apply to cylindrical faces
            adaptor = BRepAdaptor_Surface(face, True)
            if adaptor.GetType() != GeomAbs_Cylinder:
                return False  # Non-cylindrical faces default to external
            
            # Get radius of this face
            gp_cyl = adaptor.Cylinder()
            face_radius = gp_cyl.Radius()
            
            # Get all cylindrical faces and collect all radii
            all_faces = self._extract_all_faces(solid)
            radii = []
            
            for other_face in all_faces:
                try:
                    other_adaptor = BRepAdaptor_Surface(other_face, True)
                    if other_adaptor.GetType() == GeomAbs_Cylinder:
                        other_gp_cyl = other_adaptor.Cylinder()
                        other_radius = other_gp_cyl.Radius()
                        radii.append(other_radius)
                except Exception:
                    continue
            
            if not radii:
                return False
            
            # Sort radii and find where this face fits
            radii = sorted(set(radii), reverse=True)  # Largest first, unique values
            max_radius = radii[0]
            
            # If this is the largest radius, it's definitely external
            if abs(face_radius - max_radius) < 1e-6:
                if self._debug:
                    print(f"DEBUG: radius_fallback: face_radius={face_radius:.6f} is max, EXTERNAL")
                return False
            
            # Find the next larger radius
            next_larger = None
            for r in radii:
                if r > face_radius + 1e-6:
                    next_larger = r
                    break
            
            # If there's a significantly larger radius, check the gap
            # For external faces, there might be a step (smaller external diameter)
            # For internal faces, the next larger should be an external face
            if next_larger is not None:
                gap_ratio = face_radius / next_larger
                next_to_max_ratio = next_larger / max_radius
                
                # Key insight: For axisymmetric parts, internal bores are typically
                # significantly smaller than external diameters. External steps are
                # usually closer in size to the main external diameter.
                
                # If the gap is large (< 0.65) AND next larger is close to max (> 0.85),
                # then this is likely a smaller external diameter (step)
                # If the gap is moderate (0.65-0.85), it could be either, but if next larger
                # is the max, it's more likely to be internal (bore)
                # If gap is small (> 0.85), likely both external (step)
                
                if gap_ratio < 0.65:
                    # Large gap - small radius compared to next larger
                    # Check if there are other radii between this and next_larger
                    # Find the closest radius larger than face_radius
                    medium_radii = [r for r in radii if face_radius < r < next_larger - 1e-6]
                    
                    if medium_radii:
                        # There's at least one medium radius between this and next_larger
                        closest_medium = min(medium_radii)  # Closest to face_radius
                        gap_to_medium = face_radius / closest_medium
                        
                        # If face_radius is very close to the medium radius (> 0.85),
                        # they're likely the same type (both external steps or both internal bores)
                        # If face_radius is much smaller than medium (< 0.70),
                        # it's likely an internal bore (smaller than another internal bore)
                        # If gap is moderate (0.70-0.85), check relative to max
                        if gap_to_medium > 0.85:
                            # Very close to medium - likely same type
                            # If medium is close to max, both external; otherwise need more logic
                            medium_to_max = closest_medium / max_radius
                            if medium_to_max > 0.85:
                                # Medium is close to max, so both are likely external
                                if self._debug:
                                    print(f"DEBUG: radius_fallback: face_radius={face_radius:.6f}, closest_medium={closest_medium:.6f}, gap_to_medium={gap_to_medium:.3f}, EXTERNAL (close to medium)")
                                return False
                            else:
                                # Medium is not close to max, so both likely internal
                                if self._debug:
                                    print(f"DEBUG: radius_fallback: face_radius={face_radius:.6f}, closest_medium={closest_medium:.6f}, gap_to_medium={gap_to_medium:.3f}, INTERNAL (close to medium)")
                                return True
                        elif gap_to_medium < 0.70:
                            # Much smaller than medium - likely internal bore
                            if self._debug:
                                print(f"DEBUG: radius_fallback: face_radius={face_radius:.6f}, closest_medium={closest_medium:.6f}, gap_to_medium={gap_to_medium:.3f}, INTERNAL (much smaller)")
                            return True
                        else:
                            # Moderate gap to medium (0.70-0.85)
                            # Check relative positions: if face_radius is closer to max than medium is,
                            # it's likely external; otherwise, check medium's position
                            medium_to_max = closest_medium / max_radius
                            face_to_max = face_radius / max_radius
                            
                            # If face_radius is relatively close to max (> 0.45 of max),
                            # and medium is not close to max (< 0.75), face_radius is likely external
                            # (e.g., 0.403 is 49.5% of 0.815, while 0.565 is 69.3% of 0.815)
                            if face_to_max > 0.45 and medium_to_max < 0.75:
                                # Face is relatively close to max, medium is not - likely external step
                                if self._debug:
                                    print(f"DEBUG: radius_fallback: face_radius={face_radius:.6f} ({face_to_max:.1%} of max), closest_medium={closest_medium:.6f} ({medium_to_max:.1%} of max), EXTERNAL (closer to max)")
                                return False
                            elif medium_to_max > 0.85:
                                # Medium is close to max, so face_radius is likely external step
                                if self._debug:
                                    print(f"DEBUG: radius_fallback: face_radius={face_radius:.6f}, closest_medium={closest_medium:.6f} (close to max), EXTERNAL (step)")
                                return False
                            else:
                                # Medium is not close to max, and face is not particularly close either
                                # This is likely internal
                                if self._debug:
                                    print(f"DEBUG: radius_fallback: face_radius={face_radius:.6f}, closest_medium={closest_medium:.6f}, INTERNAL")
                                return True
                    elif next_to_max_ratio > 0.85:
                        # No medium radius, and next larger is very close to max
                        # This is likely a smaller external diameter (step)
                        if self._debug:
                            print(f"DEBUG: radius_fallback: face_radius={face_radius:.6f}, next_larger={next_larger:.6f}, gap_ratio={gap_ratio:.3f}, EXTERNAL (small step)")
                        return False
                    else:
                        # No medium radius, and next larger is not close to max
                        # This is likely internal
                        if self._debug:
                            print(f"DEBUG: radius_fallback: face_radius={face_radius:.6f}, next_larger={next_larger:.6f}, gap_ratio={gap_ratio:.3f}, INTERNAL")
                        return True
                elif gap_ratio < 0.85:
                    # Moderate gap
                    if abs(next_larger - max_radius) < 1e-6:
                        # Next larger IS the max, so this is likely internal (bore)
                        if self._debug:
                            print(f"DEBUG: radius_fallback: face_radius={face_radius:.6f}, next_larger={next_larger:.6f} (max), gap_ratio={gap_ratio:.3f}, INTERNAL")
                        return True
                    else:
                        # Next larger is not max, likely external step
                        if self._debug:
                            print(f"DEBUG: radius_fallback: face_radius={face_radius:.6f}, next_larger={next_larger:.6f}, gap_ratio={gap_ratio:.3f}, EXTERNAL (step)")
                        return False
                else:
                    # Small gap, likely both external (step feature)
                    if self._debug:
                        print(f"DEBUG: radius_fallback: face_radius={face_radius:.6f}, next_larger={next_larger:.6f}, gap_ratio={gap_ratio:.3f}, EXTERNAL (step)")
                    return False
            else:
                # This is the smallest radius - check if it's much smaller than max
                if face_radius < max_radius * 0.70:
                    if self._debug:
                        print(f"DEBUG: radius_fallback: face_radius={face_radius:.6f} is smallest, < 70% of max, INTERNAL")
                    return True
                else:
                    if self._debug:
                        print(f"DEBUG: radius_fallback: face_radius={face_radius:.6f} is smallest but >= 70% of max, EXTERNAL")
                    return False
        except Exception as e:
            if self._debug:
                print(f"DEBUG: radius_fallback exception: {e}")
            return False  # Default to external on error
    
    def set_reference_axis(self, axis_point_2d=None):
        """Set the reference axis for extent computation.
        
        Convention: Uses standard Z-axis revolution axis.
        
        Args:
            axis_point_2d: Optional Point2D for axis position (x=radius, y=axial).
                          If None, uses origin (0, 0).
        """
        if _CONVENTIONS_AVAILABLE:
            from geometry_2d import Point2D
            if axis_point_2d is None:
                axis_point_2d = Point2D(0.0, 0.0)
            self._ref_axis = get_reference_axis(axis_point_2d)
        else:
            # Fallback
            self._ref_axis = gp_Ax1(gp_Pnt(0.0, 0.0, 0.0), gp_Dir(0.0, 0.0, 1.0))
    
    def _get_reference_axis(self) -> gp_Ax1:
        """Get the global reference axis for extent computation.
        
        Convention: Uses standard Z-axis revolution axis.
        This is the part's revolution axis (Z-axis).
        All extents are computed relative to this axis for consistency.
        
        Returns:
            Reference axis: Z-axis (standard convention)
        """
        if self._ref_axis is None:
            # Initialize with default axis
            self.set_reference_axis()
        return self._ref_axis
    
    def _project_point_to_axis(self, P: gp_Pnt, axis: gp_Ax1) -> float:
        """Project a point onto an axis and return the parameter value t.
        
        Args:
            P: Point to project
            axis: Axis to project onto
            
        Returns:
            Scalar parameter t such that axis.Location() + t * axis.Direction() is closest to P
        """
        axis_point = axis.Location()
        axis_dir = axis.Direction()
        vec = gp_Vec(axis_point, P)
        dir_vec = gp_Vec(axis_dir.XYZ())
        return vec.Dot(dir_vec)
    
    def _face_axis_extents(self, face: TopoDS_Face, ref_axis: Optional[gp_Ax1] = None) -> Tuple[float, float]:
        """Compute axial extents of a face by projecting all vertices onto the reference axis.
        
        Uses a global reference axis (Z-axis at origin) for consistent extent computation
        across all features, regardless of their individual axis locations.
        
        Args:
            face: Face to analyze
            ref_axis: Reference axis to project onto (defaults to Z-axis at origin)
            
        Returns:
            Tuple of (t_min, t_max) representing the extent along the reference axis
        """
        if ref_axis is None:
            ref_axis = self._get_reference_axis()
        
        t_values = []
        
        # Iterate vertices
        exp = TopExp_Explorer(face, TopAbs_VERTEX)
        while exp.More():
            vertex_shape = exp.Current()
            vertex = TopoDS_Vertex()
            vertex.TShape(vertex_shape.TShape())
            vertex.Location(vertex_shape.Location())
            vertex.Orientation(vertex_shape.Orientation())
            
            try:
                p = BRep_Tool.Pnt(vertex)
                t = self._project_point_to_axis(p, ref_axis)
                t_values.append(t)
            except Exception:
                pass
            
            exp.Next()
        
        if t_values:
            return (min(t_values), max(t_values))
        else:
            # Fallback: return (0, 0) if no vertices found
            return (0.0, 0.0)
    
    def _extract_axis_from_cylinder(self, face: TopoDS_Face) -> Optional[gp_Ax1]:
        """Extract axis from a cylindrical face."""
        try:
            adaptor = BRepAdaptor_Surface(face, True)
            if adaptor.GetType() == GeomAbs_Cylinder:
                gp_cyl = adaptor.Cylinder()  # returns gp_Cylinder
                return gp_cyl.Axis()
        except Exception:
            pass
        return None
    
    def _extract_radius_from_cylinder(self, face: TopoDS_Face) -> Optional[float]:
        """Extract radius from a cylindrical face."""
        try:
            adaptor = BRepAdaptor_Surface(face, True)
            if adaptor.GetType() == GeomAbs_Cylinder:
                gp_cyl = adaptor.Cylinder()  # returns gp_Cylinder
                return gp_cyl.Radius()
        except Exception:
            pass
        return None
    
    def _are_coaxial(self, axis1: gp_Ax1, axis2: gp_Ax1, tolerance: float) -> bool:
        """Check if two axes are coaxial (parallel and close)."""
        dir1 = axis1.Direction()
        dir2 = axis2.Direction()
        
        # Check if directions are parallel (or anti-parallel)
        dot_product = abs(dir1.Dot(dir2))
        if dot_product < (1.0 - tolerance):
            return False  # Not parallel
        
        # Check if axes are close to each other
        point1 = axis1.Location()
        point2 = axis2.Location()
        
        # Vector from point1 to point2
        vec = gp_Vec(point1, point2)
        
        # Distance from point2 to axis1
        dir_vec = gp_Vec(dir1)
        projection = vec.Dot(dir_vec)
        proj_vec = gp_Vec(
            dir_vec.X() * projection,
            dir_vec.Y() * projection,
            dir_vec.Z() * projection
        )
        perp_vec = vec.Subtracted(proj_vec)
        distance = perp_vec.Magnitude()
        
        return distance <= tolerance
    
    def _cluster_faces_by_radius(self, faces: List[TopoDS_Face], tolerance: float) -> List[List[TopoDS_Face]]:
        """Cluster cylindrical faces by radius within a group."""
        clusters = []
        processed: Set[int] = set()
        
        for i, face in enumerate(faces):
            if i in processed:
                continue
            
            radius_i = self._extract_radius_from_cylinder(face)
            if radius_i is None:
                continue
            
            cluster = [face]
            processed.add(i)
            
            for j, other_face in enumerate(faces):
                if j != i and j not in processed:
                    radius_j = self._extract_radius_from_cylinder(other_face)
                    if radius_j is not None:
                        if abs(radius_i - radius_j) <= tolerance:
                            cluster.append(other_face)
                            processed.add(j)
            
            clusters.append(cluster)
        
        return clusters
    
    def _group_coaxial_faces(self, faces: List[TopoDS_Face], tolerance: float) -> List[List[TopoDS_Face]]:
        """Group cylindrical faces by axis (coaxial grouping)."""
        groups = []
        processed: Set[int] = set()
        
        for i, face in enumerate(faces):
            if i in processed:
                continue
            
            axis = self._extract_axis_from_cylinder(face)
            if axis is None:
                continue
            
            group = [face]
            processed.add(i)
            
            for j, other_face in enumerate(faces):
                if j != i and j not in processed:
                    other_axis = self._extract_axis_from_cylinder(other_face)
                    if other_axis is not None:
                        if self._are_coaxial(axis, other_axis, tolerance):
                            group.append(other_face)
                            processed.add(j)
            
            groups.append(group)
        
        return groups
    
    def _find_end_faces(self, cylindrical_faces: List[TopoDS_Face], axis: gp_Ax1, solid: TopoDS_Solid) -> List[TopoDS_Face]:
        """Find end faces (caps) of a cylindrical feature."""
        end_faces = []
        all_faces = self._extract_all_faces(solid)
        
        axis_dir = axis.Direction()
        axis_point = axis.Location()
        
        for face in all_faces:
            if face in cylindrical_faces:
                continue
            
            # Check if face is perpendicular to axis
            try:
                adaptor = BRepAdaptor_Surface(face, True)
                if adaptor.GetType() == GeomAbs_Plane:
                    plane = adaptor.Plane()
                    normal = plane.Axis().Direction()
                    
                    # Check if normal is parallel to axis
                    dot = abs(normal.Dot(axis_dir))
                    if dot > (1.0 - self.tolerance):
                        # Face is perpendicular to axis
                        # Check if face center is on or near the axis
                        face_center = self._get_face_center(face)
                        vec_to_center = gp_Vec(axis_point, face_center)
                        perp_vec = vec_to_center.Subtracted(gp_Vec(axis_dir.Multiplied(vec_to_center.Dot(gp_Vec(axis_dir.XYZ())))))
                        
                        if perp_vec.Magnitude() < self.tolerance:
                            end_faces.append(face)
            except Exception:
                continue
        
        return end_faces
    
    def _get_face_center(self, face: TopoDS_Face) -> gp_Pnt:
        """Get approximate center point of a face."""
        try:
            # Simple approach: get center of bounding box
            from OCC.Core.Bnd import Bnd_Box
            from OCC.Core.BRepBndLib import BRepBndLib
            
            bbox = Bnd_Box()
            BRepBndLib.Add(face, bbox)
            
            x_min, y_min, z_min, x_max, y_max, z_max = bbox.Get()
            center = gp_Pnt(
                (x_min + x_max) / 2.0,
                (y_min + y_max) / 2.0,
                (z_min + z_max) / 2.0
            )
            return center
        except Exception:
            # Fallback: return origin
            return gp_Pnt(0.0, 0.0, 0.0)
    
    def _calculate_depth(self, cylindrical_faces: List[TopoDS_Face], end_faces: List[TopoDS_Face], axis: gp_Ax1) -> float:
        """Calculate depth of a hole or cylinder."""
        if not end_faces:
            return math.inf  # Through feature
        
        axis_dir = axis.Direction()
        axis_point = axis.Location()
        
        # Project end face centers onto axis
        positions = []
        for end_face in end_faces:
            center = self._get_face_center(end_face)
            vec = gp_Vec(axis_point, center)
            projection = vec.Dot(gp_Vec(axis_dir.XYZ()))
            positions.append(projection)
        
        if len(positions) >= 2:
            depth = abs(max(positions) - min(positions))
        elif len(positions) == 1:
            # Single end face - depth is distance from axis start
            depth = abs(positions[0])
        else:
            depth = math.inf
        
        return depth
    
    def _classify_bottom_type(self, bottom_face: TopoDS_Face) -> BottomType:
        """Classify the type of hole bottom."""
        surface_type = self._classify_surface_type(bottom_face)
        
        if surface_type == SurfaceType.PLANE:
            return BottomType.FLAT
        elif surface_type == SurfaceType.CONE:
            return BottomType.CONICAL
        elif surface_type == SurfaceType.SPHERE:
            return BottomType.SPHERICAL
        else:
            return BottomType.UNKNOWN
    
    def _detect_holes(self, solid: TopoDS_Solid, cylindrical_faces: List[TopoDS_Face], debug_all_cyl_faces: List[TopoDS_Face] = None) -> List[HoleFeature]:
        """Detect hole features (internal cylindrical voids)."""
        holes = []
        debug_all_cyl_faces = debug_all_cyl_faces or []
        
        # Filter internal cylindrical faces using point-in-solid classification
        internal_faces = [f for f in cylindrical_faces if self._is_internal_face(f, solid)]
        
        if self._debug:
            print(f"DEBUG _detect_holes: Processing cylindrical faces")
            print(f"  Input cylindrical_faces count: {len(cylindrical_faces)}")
            print(f"  After filtering (internal faces): {len(internal_faces)} faces")
        
        if not internal_faces:
            return holes
        
        # Group coaxial faces
        face_groups = self._group_coaxial_faces(internal_faces, self.tolerance)
        
        if self._debug:
            print(f"  Face groups (coaxial): {len(face_groups)}")
        
        # Create hole features - need to split by radius within each coaxial group
        feature_idx = 0
        for group in face_groups:
            if self._debug:
                print(f"  Processing coaxial group with {len(group)} faces")
            if not group:
                continue
            
            # Cluster faces by radius within this coaxial group
            radius_clusters = self._cluster_faces_by_radius(group, self.tolerance)
            
            if self._debug:
                print(f"  Coaxial group has {len(radius_clusters)} radius clusters")
            
            # Create a separate feature for each radius cluster
            for cluster in radius_clusters:
                if not cluster:
                    continue
                
                representative_face = cluster[0]
                axis = self._extract_axis_from_cylinder(representative_face)
                radius = self._extract_radius_from_cylinder(representative_face)
                
                if axis is None or radius is None:
                    if self._debug:
                        loc_str = "unknown"
                        dir_str = "unknown"
                        rad_str = "unknown"
                        try:
                            if axis is not None:
                                loc = axis.Location()
                                dir = axis.Direction()
                                loc_str = f"({loc.X():.4f}, {loc.Y():.4f}, {loc.Z():.4f})"
                                dir_str = f"({dir.X():.4f}, {dir.Y():.4f}, {dir.Z():.4f})"
                            if radius is not None:
                                rad_str = f"{radius:.4f}"
                        except:
                            pass
                        print(f"  DEBUG: Skipped cluster - axis={axis is not None}, radius={radius is not None}")
                    continue
                
                # Find end faces
                end_faces = self._find_end_faces(cluster, axis, solid)
                
                # Compute axial extents from all cylindrical faces in cluster
                # Use global reference axis (not the feature's axis) for consistent extents
                ref_axis = self._get_reference_axis()
                t_mins = []
                t_maxs = []
                for face in cluster:
                    t_min, t_max = self._face_axis_extents(face, ref_axis)
                    t_mins.append(t_min)
                    t_maxs.append(t_max)
                axial_extent = (min(t_mins), max(t_maxs)) if t_mins else None
                
                if not end_faces:
                    # Through hole
                    hole = HoleFeature(
                        axis=axis,
                        diameter=2.0 * radius,
                        depth=math.inf,
                        bottom_type=BottomType.NONE,
                        cylindrical_faces=cluster,
                        id=f"hole_{feature_idx}",
                        axial_extent=axial_extent
                    )
                else:
                    # Blind hole
                    depth = self._calculate_depth(cluster, end_faces, axis)
                    bottom_face = end_faces[0] if end_faces else None
                    bottom_type = self._classify_bottom_type(bottom_face) if bottom_face else BottomType.UNKNOWN
                    
                    hole = HoleFeature(
                        axis=axis,
                        diameter=2.0 * radius,
                        depth=depth,
                        bottom_type=bottom_type,
                        cylindrical_faces=cluster,
                        bottom_face=bottom_face,
                        id=f"hole_{feature_idx}",
                        axial_extent=axial_extent
                    )
                
                holes.append(hole)
                feature_idx += 1
                if self._debug:
                    print(f"  DEBUG: Added hole {feature_idx-1}, total now: {len(holes)}")
        
        if self._debug:
            print(f"  DEBUG: _detect_holes returning {len(holes)} holes")
        return holes
    
    def _detect_cylinders(self, solid: TopoDS_Solid, cylindrical_faces: List[TopoDS_Face], debug_all_cyl_faces: List[TopoDS_Face] = None) -> List[CylinderFeature]:
        """Detect cylinder features (external cylindrical protrusions)."""
        cylinders = []
        debug_all_cyl_faces = debug_all_cyl_faces or []
        
        # Filter external cylindrical faces using point-in-solid classification
        external_faces = [f for f in cylindrical_faces if not self._is_internal_face(f, solid)]
        
        if self._debug:
            print(f"DEBUG _detect_cylinders: Processing cylindrical faces")
            print(f"  Input cylindrical_faces count: {len(cylindrical_faces)}")
            print(f"  After filtering (external faces): {len(external_faces)} faces")
        
        if not external_faces:
            return cylinders
        
        # Group coaxial faces
        face_groups = self._group_coaxial_faces(external_faces, self.tolerance)
        
        if self._debug:
            print(f"  Face groups (coaxial): {len(face_groups)}")
        
        # Create cylinder features - need to split by radius within each coaxial group
        feature_idx = 0
        for group in face_groups:
            if not group:
                continue
            
            # Cluster faces by radius within this coaxial group
            radius_clusters = self._cluster_faces_by_radius(group, self.tolerance)
            
            if self._debug:
                print(f"  Coaxial group has {len(radius_clusters)} radius clusters")
            
            # Create a separate feature for each radius cluster
            for cluster in radius_clusters:
                if not cluster:
                    continue
                
                representative_face = cluster[0]
                axis = self._extract_axis_from_cylinder(representative_face)
                radius = self._extract_radius_from_cylinder(representative_face)
                
                if axis is None or radius is None:
                    if self._debug:
                        print(f"  DEBUG: Skipped cluster - axis={axis is not None}, radius={radius is not None}")
                    continue
                
                if self._debug:
                    print(f"  Creating cylinder feature {feature_idx}: radius={radius:.6f}")
                
                # Find end faces
                end_faces = self._find_end_faces(cluster, axis, solid)
                
                # Calculate height
                if len(end_faces) >= 2:
                    height = self._calculate_depth(cluster, end_faces, axis)
                else:
                    height = math.inf  # Extends beyond visible region
                
                # Classify feature type (simplified)
                feature_class = CylinderType.BOSS  # Default
                
                # Compute axial extents from all cylindrical faces in cluster
                # Use global reference axis (not the feature's axis) for consistent extents
                ref_axis = self._get_reference_axis()
                t_mins = []
                t_maxs = []
                for face in cluster:
                    t_min, t_max = self._face_axis_extents(face, ref_axis)
                    t_mins.append(t_min)
                    t_maxs.append(t_max)
                axial_extent = (min(t_mins), max(t_maxs)) if t_mins else None
                
                try:
                    cylinder = CylinderFeature(
                        axis=axis,
                        radius=radius,
                        height=height,
                        cylindrical_face=representative_face,
                        end_faces=end_faces,
                        feature_class=feature_class,
                        is_external=True,
                        id=f"cylinder_{feature_idx}",
                        axial_extent=axial_extent
                    )
                    
                    cylinders.append(cylinder)
                    feature_idx += 1
                except Exception as e:
                    if self._debug:
                        print(f"  DEBUG: Exception creating cylinder feature: {e}")
                    continue
        
        if self._debug:
            print(f"  DEBUG: _detect_cylinders returning {len(cylinders)} cylinders")
        return cylinders
    
    def _extract_plane_from_face(self, face: TopoDS_Face) -> Optional[gp_Pln]:
        """Extract plane from a planar face."""
        try:
            adaptor = BRepAdaptor_Surface(face, True)
            if adaptor.GetType() == GeomAbs_Plane:
                gp_pln = adaptor.Plane()  # returns gp_Pln
                return gp_pln
        except Exception:
            pass
        return None
    
    def _get_face_normal(self, face: TopoDS_Face) -> Optional[gp_Dir]:
        """Get normal direction of a face."""
        try:
            plane = self._extract_plane_from_face(face)
            if plane is not None:
                return plane.Axis().Direction()
        except Exception:
            pass
        return None
    
    def _calculate_face_area(self, face: TopoDS_Face) -> float:
        """Calculate area of a face."""
        try:
            from OCC.Core.GProp import GProp_GProps
            from OCC.Core.BRepGProp import BRepGProp
            
            props = GProp_GProps()
            BRepGProp.SurfaceProperties(face, props)
            return props.Mass()
        except Exception:
            return 0.0
    
    def build_turned_part_stack(self, collection: FeatureCollection, tolerance: float = 1e-6) -> TurnedPartStack:
        """Build a TurnedPartStack from extracted features.
        
        Phase 3.7: Collects all unique Z boundaries from OD and ID extents,
        creates consecutive segments, and assigns OD/ID radii to each segment.
        
        Args:
            collection: FeatureCollection with extracted cylinders and holes
            tolerance: Numerical tolerance for extent matching
            
        Returns:
            TurnedPartStack with segments covering the full axial range
        """
        # Step 1: Collect all unique Z boundaries from all extents (OD + ID)
        z_boundaries = set()
        
        # Collect from external cylinders (OD)
        for cylinder in collection.cylinders:
            if cylinder.is_external and cylinder.axial_extent is not None:
                z_min, z_max = cylinder.axial_extent
                # Ensure correct ordering (handle potential reversed extents)
                z_min, z_max = min(z_min, z_max), max(z_min, z_max)
                z_boundaries.add(z_min)
                z_boundaries.add(z_max)
        
        # Collect from internal cylinders/holes (ID)
        for hole in collection.holes:
            if hole.axial_extent is not None:
                z_min, z_max = hole.axial_extent
                # Ensure correct ordering (handle potential reversed extents)
                z_min, z_max = min(z_min, z_max), max(z_min, z_max)
                z_boundaries.add(z_min)
                z_boundaries.add(z_max)
        
        # Also check internal cylinders (if any are marked as internal)
        for cylinder in collection.cylinders:
            if not cylinder.is_external and cylinder.axial_extent is not None:
                z_min, z_max = cylinder.axial_extent
                # Ensure correct ordering (handle potential reversed extents)
                z_min, z_max = min(z_min, z_max), max(z_min, z_max)
                z_boundaries.add(z_min)
                z_boundaries.add(z_max)
        
        if not z_boundaries:
            # No extents found, return empty stack
            return TurnedPartStack(segments=[])
        
        # Step 2: Sort boundaries and build consecutive segments [Zi, Zi+1]
        sorted_boundaries = sorted(z_boundaries)
        segments = []
        
        for i in range(len(sorted_boundaries) - 1):
            z_start = sorted_boundaries[i]
            z_end = sorted_boundaries[i + 1]
            
            # Step 3: For each segment, assign OD and ID radii
            # Find OD: external cylinder whose extent fully covers the segment
            od_radius = 0.0
            for cylinder in collection.cylinders:
                if cylinder.is_external and cylinder.axial_extent is not None:
                    cyl_z_min, cyl_z_max = cylinder.axial_extent
                    # Ensure correct ordering (handle potential reversed extents)
                    cyl_z_min, cyl_z_max = min(cyl_z_min, cyl_z_max), max(cyl_z_min, cyl_z_max)
                    # Check if cylinder extent fully covers the segment (within tolerance)
                    if (cyl_z_min <= z_start + tolerance and cyl_z_max >= z_end - tolerance):
                        # Use the cylinder with the largest radius if multiple cover the segment
                        if cylinder.radius > od_radius:
                            od_radius = cylinder.radius
            
            # Find ID: internal cylinder/hole whose extent fully covers the segment
            id_radius = 0.0
            for hole in collection.holes:
                if hole.axial_extent is not None:
                    hole_z_min, hole_z_max = hole.axial_extent
                    # Ensure correct ordering (handle potential reversed extents)
                    hole_z_min, hole_z_max = min(hole_z_min, hole_z_max), max(hole_z_min, hole_z_max)
                    # Check if hole extent fully covers the segment (within tolerance)
                    if (hole_z_min <= z_start + tolerance and hole_z_max >= z_end - tolerance):
                        # Use the hole with the largest radius if multiple cover the segment
                        hole_radius = hole.diameter / 2.0
                        if hole_radius > id_radius:
                            id_radius = hole_radius
            
            # Also check internal cylinders
            for cylinder in collection.cylinders:
                if not cylinder.is_external and cylinder.axial_extent is not None:
                    cyl_z_min, cyl_z_max = cylinder.axial_extent
                    # Ensure correct ordering (handle potential reversed extents)
                    cyl_z_min, cyl_z_max = min(cyl_z_min, cyl_z_max), max(cyl_z_min, cyl_z_max)
                    # Check if cylinder extent fully covers the segment (within tolerance)
                    if (cyl_z_min <= z_start + tolerance and cyl_z_max >= z_end - tolerance):
                        # Use the cylinder with the largest radius if multiple cover the segment
                        if cylinder.radius > id_radius:
                            id_radius = cylinder.radius
            
            # Create segment
            segment = TurnedPartSegment(
                z_start=z_start,
                z_end=z_end,
                od_diameter=2.0 * od_radius if od_radius > 0 else 0.0,
                id_diameter=2.0 * id_radius if id_radius > 0 else 0.0,
                wall_thickness=0.0  # Will be computed in __post_init__
            )
            segments.append(segment)
        
        stack = TurnedPartStack(segments=segments)
        
        # Step 5: Validate
        is_valid, errors = stack.validate(tolerance)
        if not is_valid and self._debug:
            print(f"WARNING: TurnedPartStack validation failed:")
            for error in errors:
                print(f"  {error}")
        
        return stack
    
    def _classify_face_orientation(self, normal: gp_Dir) -> FaceOrientation:
        """Classify orientation of a planar face."""
        # Simplified: check if normal is mostly vertical (top/bottom) or horizontal (side)
        z_component = abs(normal.Z())
        
        if z_component > 0.9:  # Mostly vertical
            if normal.Z() > 0:
                return FaceOrientation.TOP
            else:
                return FaceOrientation.BOTTOM
        else:
            return FaceOrientation.SIDE
    
    def _extract_boundary_edges(self, face: TopoDS_Face) -> List[TopoDS_Edge]:
        """Extract boundary edges of a face."""
        edges = []
        exp = TopExp_Explorer(face, TopAbs_EDGE)
        while exp.More():
            edge_shape = exp.Current()
            edge = TopoDS_Edge()
            edge.TShape(edge_shape.TShape())
            edge.Location(edge_shape.Location())
            edge.Orientation(edge_shape.Orientation())
            edges.append(edge)
            exp.Next()
        return edges
    
    def _detect_planar_faces(self, solid: TopoDS_Solid, planar_faces: List[TopoDS_Face]) -> List[PlanarFaceFeature]:
        """Detect planar face features."""
        features = []
        
        skipped_count = 0
        for i, face in enumerate(planar_faces):
            plane = self._extract_plane_from_face(face)
            normal = self._get_face_normal(face)
            
            if plane is None or normal is None:
                skipped_count += 1
                if self._debug and skipped_count <= 3:
                    print(f"DEBUG: Face {i} skipped - plane={plane is not None}, normal={normal is not None}")
                continue
            
            boundary_edges = self._extract_boundary_edges(face)
            area = self._calculate_face_area(face)
            orientation = self._classify_face_orientation(normal)
            
            feature = PlanarFaceFeature(
                plane=plane,
                boundary_edges=boundary_edges,
                face=face,
                normal=normal,
                area=area,
                orientation=orientation,
                id=f"planar_face_{i}"
            )
            
            features.append(feature)
        
        if self._debug:
            if skipped_count > 0:
                print(f"DEBUG: _detect_planar_faces: {len(features)} features created, {skipped_count} skipped")
        
        return features
    
    def _build_relationships(self, collection: FeatureCollection) -> Dict[str, List[str]]:
        """Build relationships between features."""
        relationships: Dict[str, List[str]] = {}
        
        # For now, return empty relationships
        # Can be extended to find adjacent features, coaxial groups, etc.
        
        return relationships

