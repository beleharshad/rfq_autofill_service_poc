"""
Phase 3.5: Feature Normalization Module

This module normalizes and consolidates extracted features from Phase 3
into canonical manufacturing features through pure data transformation.
"""

from typing import List, Optional, Dict, Set
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime
import math

from feature_extractor import (
    FeatureCollection,
    HoleFeature,
    CylinderFeature,
    PlanarFaceFeature,
    EdgeFeature
)


class NormalizedFeatureType(Enum):
    """Canonical feature types."""
    HOLE_THROUGH = "HOLE_THROUGH"
    HOLE_BLIND = "HOLE_BLIND"
    CYLINDER_BOSS = "CYLINDER_BOSS"
    CYLINDER_SHAFT = "CYLINDER_SHAFT"
    CYLINDER_PILLAR = "CYLINDER_PILLAR"
    PLANAR_SURFACE = "PLANAR_SURFACE"


class Completeness(Enum):
    """Feature completeness assessment."""
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    INCOMPLETE = "INCOMPLETE"


class Confidence(Enum):
    """Feature classification confidence."""
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class BottomType(Enum):
    """Hole bottom types."""
    FLAT = "FLAT"
    CONICAL = "CONICAL"
    SPHERICAL = "SPHERICAL"
    NONE = "NONE"
    UNKNOWN = "UNKNOWN"


class FaceOrientation(Enum):
    """Planar face orientation."""
    TOP = "TOP"
    BOTTOM = "BOTTOM"
    SIDE = "SIDE"
    UNKNOWN = "UNKNOWN"


@dataclass
class Point3D:
    """3D point representation."""
    x: float
    y: float
    z: float


@dataclass
class Direction3D:
    """3D direction vector (normalized)."""
    x: float
    y: float
    z: float
    
    def normalize(self) -> 'Direction3D':
        """Normalize the direction vector."""
        magnitude = math.sqrt(self.x**2 + self.y**2 + self.z**2)
        if magnitude < 1e-9:
            return Direction3D(0.0, 0.0, 1.0)  # Default to Z-axis
        return Direction3D(
            self.x / magnitude,
            self.y / magnitude,
            self.z / magnitude
        )


@dataclass
class NormalizedAxis:
    """Normalized axis representation."""
    origin: Point3D
    direction: Direction3D
    is_standardized: bool = True


@dataclass
class FaceReference:
    """Reference to a face (no geometry, just ID)."""
    face_id: str
    face_index: Optional[int] = None


@dataclass
class EdgeReference:
    """Reference to an edge."""
    edge_id: str
    edge_index: Optional[int] = None


@dataclass
class NormalizedHoleFeature:
    """Normalized hole feature."""
    id: str
    feature_type: NormalizedFeatureType
    axis: NormalizedAxis
    diameter: float
    radius: float
    depth: float  # math.inf for through holes
    bottom_type: BottomType
    entry_face: Optional[FaceReference] = None
    bottom_face: Optional[FaceReference] = None
    cylindrical_faces: List[FaceReference] = field(default_factory=list)
    metadata: Dict = field(default_factory=dict)
    manufacturing_attributes: Dict = field(default_factory=dict)
    source_features: List[str] = field(default_factory=list)
    
    def is_through(self) -> bool:
        """Check if this is a through hole."""
        return math.isinf(self.depth)


@dataclass
class NormalizedCylinderFeature:
    """Normalized cylinder feature."""
    id: str
    feature_type: NormalizedFeatureType
    axis: NormalizedAxis
    radius: float
    diameter: float
    height: float  # math.inf if extends beyond visible region
    cylindrical_face: FaceReference
    end_faces: List[FaceReference] = field(default_factory=list)
    metadata: Dict = field(default_factory=dict)
    manufacturing_attributes: Dict = field(default_factory=dict)
    source_features: List[str] = field(default_factory=list)


@dataclass
class NormalizedPlanarFeature:
    """Normalized planar surface feature."""
    id: str
    feature_type: NormalizedFeatureType
    plane_origin: Point3D
    plane_normal: Direction3D
    area: float
    boundary_edges: List[EdgeReference] = field(default_factory=list)
    merged_faces: List[FaceReference] = field(default_factory=list)
    orientation: FaceOrientation = FaceOrientation.UNKNOWN
    metadata: Dict = field(default_factory=dict)
    manufacturing_attributes: Dict = field(default_factory=dict)
    source_features: List[str] = field(default_factory=list)


@dataclass
class NormalizedFeatureCollection:
    """Collection of normalized features."""
    holes: List[NormalizedHoleFeature] = field(default_factory=list)
    cylinders: List[NormalizedCylinderFeature] = field(default_factory=list)
    planar_surfaces: List[NormalizedPlanarFeature] = field(default_factory=list)
    relationships: Dict[str, List[str]] = field(default_factory=dict)
    metadata: Dict = field(default_factory=dict)


class FeatureNormalizer:
    """Normalizes and consolidates extracted features."""
    
    def __init__(self, tolerance: float = 1e-6, shaft_height_threshold: float = 50.0):
        """Initialize the normalizer.
        
        Args:
            tolerance: Geometric tolerance for comparisons
            shaft_height_threshold: Height threshold for shaft classification (mm)
        """
        self.tolerance = tolerance
        self.shaft_height_threshold = shaft_height_threshold
    
    def normalize(self, collection: FeatureCollection) -> NormalizedFeatureCollection:
        """Normalize a feature collection.
        
        Args:
            collection: FeatureCollection from Phase 3
            
        Returns:
            NormalizedFeatureCollection with consolidated features
        """
        normalized = NormalizedFeatureCollection()
        
        # Step 1: Normalize and merge holes
        normalized.holes = self._normalize_holes(collection.holes)
        
        # Step 2: Normalize and merge cylinders
        normalized.cylinders = self._normalize_cylinders(collection.cylinders)
        
        # Step 3: Normalize and merge planar faces
        normalized.planar_surfaces = self._normalize_planar_faces(collection.planar_faces)
        
        # Step 4: Build relationships
        normalized.relationships = self._build_relationships(normalized)
        
        # Step 5: Add metadata
        normalized.metadata = {
            'total_features': len(normalized.holes) + len(normalized.cylinders) + len(normalized.planar_surfaces),
            'merged_count': len(collection.holes) + len(collection.cylinders) + len(collection.planar_faces) - 
                          (len(normalized.holes) + len(normalized.cylinders) + len(normalized.planar_surfaces)),
            'normalization_timestamp': datetime.now().isoformat()
        }
        
        return normalized
    
    def _normalize_holes(self, holes: List[HoleFeature]) -> List[NormalizedHoleFeature]:
        """Normalize and merge hole features."""
        if not holes:
            return []
        
        # Group coaxial holes
        groups = self._group_coaxial_holes(holes)
        
        normalized_holes = []
        for i, group in enumerate(groups):
            if not group:
                continue
            
            # Merge group into single feature
            merged = self._merge_hole_group(group, f"normalized_hole_{i}")
            if merged:
                normalized_holes.append(merged)
        
        return normalized_holes
    
    def _group_coaxial_holes(self, holes: List[HoleFeature]) -> List[List[HoleFeature]]:
        """Group holes by coaxiality."""
        groups = []
        processed: Set[int] = set()
        
        for i, hole in enumerate(holes):
            if i in processed:
                continue
            
            group = [hole]
            processed.add(i)
            axis1 = self._extract_axis_from_hole(hole)
            
            for j, other_hole in enumerate(holes):
                if j != i and j not in processed:
                    axis2 = self._extract_axis_from_hole(other_hole)
                    if self._are_coaxial(axis1, axis2):
                        group.append(other_hole)
                        processed.add(j)
            
            groups.append(group)
        
        return groups
    
    def _extract_axis_from_hole(self, hole: HoleFeature) -> NormalizedAxis:
        """Extract normalized axis from hole feature."""
        axis_occ = hole.axis
        origin = Point3D(
            axis_occ.Location().X(),
            axis_occ.Location().Y(),
            axis_occ.Location().Z()
        )
        dir_occ = axis_occ.Direction()
        direction = Direction3D(
            dir_occ.X(),
            dir_occ.Y(),
            dir_occ.Z()
        ).normalize()
        
        # Standardize direction (always point in positive Z if mostly vertical)
        if abs(direction.z) > 0.9:
            if direction.z < 0:
                direction = Direction3D(-direction.x, -direction.y, -direction.z)
        elif abs(direction.z) < 0.1:
            # Horizontal axis - standardize to positive X direction
            if direction.x < 0:
                direction = Direction3D(-direction.x, -direction.y, -direction.z)
        
        return NormalizedAxis(origin=origin, direction=direction, is_standardized=True)
    
    def _are_coaxial(self, axis1: NormalizedAxis, axis2: NormalizedAxis) -> bool:
        """Check if two axes are coaxial."""
        # Check if directions are parallel
        dir1 = axis1.direction
        dir2 = axis2.direction
        
        dot_product = abs(dir1.x * dir2.x + dir1.y * dir2.y + dir1.z * dir2.z)
        if dot_product < (1.0 - self.tolerance):
            return False  # Not parallel
        
        # Check if axes are close (distance between lines)
        p1 = axis1.origin
        p2 = axis2.origin
        
        # Vector from p1 to p2
        vec = Point3D(p2.x - p1.x, p2.y - p1.y, p2.z - p1.z)
        
        # Distance from p2 to axis1
        dir_vec = Direction3D(dir1.x, dir1.y, dir1.z)
        projection = vec.x * dir_vec.x + vec.y * dir_vec.y + vec.z * dir_vec.z
        perp = Point3D(
            vec.x - dir_vec.x * projection,
            vec.y - dir_vec.y * projection,
            vec.z - dir_vec.z * projection
        )
        distance = math.sqrt(perp.x**2 + perp.y**2 + perp.z**2)
        
        return distance <= self.tolerance
    
    def _merge_hole_group(self, group: List[HoleFeature], feature_id: str) -> Optional[NormalizedHoleFeature]:
        """Merge a group of coaxial holes into a single normalized feature."""
        if not group:
            return None
        
        # Use first hole as reference
        reference = group[0]
        axis = self._extract_axis_from_hole(reference)
        
        # Collect all cylindrical faces
        all_cylindrical_faces = []
        all_bottom_faces = []
        all_top_faces = []
        diameters = []
        depths = []
        bottom_types = []
        
        for hole in group:
            # Convert faces to references
            for face in hole.cylindrical_faces:
                all_cylindrical_faces.append(FaceReference(face_id=str(id(face))))
            
            if hole.bottom_face:
                all_bottom_faces.append(FaceReference(face_id=str(id(hole.bottom_face))))
                bottom_types.append(hole.bottom_type)
            
            if hole.top_face:
                all_top_faces.append(FaceReference(face_id=str(id(hole.top_face))))
            
            diameters.append(hole.diameter)
            if not math.isinf(hole.depth):
                depths.append(hole.depth)
        
        # Determine feature type
        if not all_bottom_faces:
            feature_type = NormalizedFeatureType.HOLE_THROUGH
            depth = math.inf
            bottom_type = BottomType.NONE
            bottom_face = None
        else:
            feature_type = NormalizedFeatureType.HOLE_BLIND
            depth = max(depths) if depths else reference.depth
            bottom_type = bottom_types[0] if bottom_types else reference.bottom_type
            bottom_face = all_bottom_faces[0] if all_bottom_faces else None
        
        # Normalize diameter (use average or most common)
        diameter = sum(diameters) / len(diameters) if diameters else reference.diameter
        radius = diameter / 2.0
        
        # Assess completeness
        completeness = self._assess_hole_completeness(
            all_cylindrical_faces, all_bottom_faces, feature_type
        )
        
        # Assess confidence
        confidence = self._assess_hole_confidence(group, feature_type)
        
        # Collect source feature IDs
        source_ids = [hole.id for hole in group]
        
        return NormalizedHoleFeature(
            id=feature_id,
            feature_type=feature_type,
            axis=axis,
            diameter=diameter,
            radius=radius,
            depth=depth,
            bottom_type=bottom_type,
            entry_face=all_top_faces[0] if all_top_faces else None,
            bottom_face=bottom_face,
            cylindrical_faces=all_cylindrical_faces,
            metadata={
                'completeness': completeness.value,
                'confidence': confidence.value
            },
            manufacturing_attributes={
                'is_threaded': False  # Default, can be extended
            },
            source_features=source_ids
        )
    
    def _assess_hole_completeness(self, cylindrical_faces: List[FaceReference], 
                                  bottom_faces: List[FaceReference],
                                  feature_type: NormalizedFeatureType) -> Completeness:
        """Assess completeness of a hole feature."""
        if not cylindrical_faces:
            return Completeness.INCOMPLETE
        
        if feature_type == NormalizedFeatureType.HOLE_THROUGH:
            # Through hole should have cylindrical faces
            return Completeness.COMPLETE if len(cylindrical_faces) >= 1 else Completeness.PARTIAL
        else:
            # Blind hole should have cylindrical faces and bottom face
            if len(cylindrical_faces) >= 1 and len(bottom_faces) >= 1:
                return Completeness.COMPLETE
            elif len(cylindrical_faces) >= 1:
                return Completeness.PARTIAL
            else:
                return Completeness.INCOMPLETE
    
    def _assess_hole_confidence(self, group: List[HoleFeature], 
                                feature_type: NormalizedFeatureType) -> Confidence:
        """Assess confidence in hole classification."""
        if len(group) == 1:
            # Single feature - confidence depends on clarity
            hole = group[0]
            if feature_type == NormalizedFeatureType.HOLE_THROUGH:
                return Confidence.HIGH if math.isinf(hole.depth) else Confidence.MEDIUM
            else:
                return Confidence.HIGH if hole.bottom_face else Confidence.MEDIUM
        else:
            # Merged features - higher confidence if consistent
            diameters = [h.diameter for h in group]
            diameter_variance = max(diameters) - min(diameters) if diameters else 0
            if diameter_variance < self.tolerance:
                return Confidence.HIGH
            else:
                return Confidence.MEDIUM
    
    def _normalize_cylinders(self, cylinders: List[CylinderFeature]) -> List[NormalizedCylinderFeature]:
        """Normalize and merge cylinder features."""
        if not cylinders:
            return []
        
        # Group coaxial cylinders
        groups = self._group_coaxial_cylinders(cylinders)
        
        normalized_cylinders = []
        for i, group in enumerate(groups):
            if not group:
                continue
            
            merged = self._merge_cylinder_group(group, f"normalized_cylinder_{i}")
            if merged:
                normalized_cylinders.append(merged)
        
        return normalized_cylinders
    
    def _group_coaxial_cylinders(self, cylinders: List[CylinderFeature]) -> List[List[CylinderFeature]]:
        """Group cylinders by coaxiality."""
        groups = []
        processed: Set[int] = set()
        
        for i, cylinder in enumerate(cylinders):
            if i in processed:
                continue
            
            group = [cylinder]
            processed.add(i)
            axis1 = self._extract_axis_from_cylinder(cylinder)
            
            for j, other_cylinder in enumerate(cylinders):
                if j != i and j not in processed:
                    axis2 = self._extract_axis_from_cylinder(other_cylinder)
                    if self._are_coaxial(axis1, axis2):
                        group.append(other_cylinder)
                        processed.add(j)
            
            groups.append(group)
        
        return groups
    
    def _extract_axis_from_cylinder(self, cylinder: CylinderFeature) -> NormalizedAxis:
        """Extract normalized axis from cylinder feature."""
        axis_occ = cylinder.axis
        origin = Point3D(
            axis_occ.Location().X(),
            axis_occ.Location().Y(),
            axis_occ.Location().Z()
        )
        dir_occ = axis_occ.Direction()
        direction = Direction3D(
            dir_occ.X(),
            dir_occ.Y(),
            dir_occ.Z()
        ).normalize()
        
        # Standardize direction
        if abs(direction.z) > 0.9:
            if direction.z < 0:
                direction = Direction3D(-direction.x, -direction.y, -direction.z)
        elif abs(direction.z) < 0.1:
            if direction.x < 0:
                direction = Direction3D(-direction.x, -direction.y, -direction.z)
        
        return NormalizedAxis(origin=origin, direction=direction, is_standardized=True)
    
    def _merge_cylinder_group(self, group: List[CylinderFeature], feature_id: str) -> Optional[NormalizedCylinderFeature]:
        """Merge a group of coaxial cylinders into a single normalized feature."""
        if not group:
            return None
        
        reference = group[0]
        axis = self._extract_axis_from_cylinder(reference)
        
        # Collect data
        radii = []
        heights = []
        all_end_faces = []
        
        for cylinder in group:
            radii.append(cylinder.radius)
            if not math.isinf(cylinder.height):
                heights.append(cylinder.height)
            
            for face in cylinder.end_faces:
                all_end_faces.append(FaceReference(face_id=str(id(face))))
        
        # Normalize dimensions
        radius = sum(radii) / len(radii) if radii else reference.radius
        diameter = radius * 2.0
        
        if heights:
            height = max(heights)  # Use maximum height from merged features
        else:
            height = math.inf
        
        # Classify feature type
        feature_type = self._classify_cylinder_type(axis, height, reference.feature_class)
        
        # Assess completeness and confidence
        completeness = self._assess_cylinder_completeness(reference.end_faces, height)
        confidence = self._assess_cylinder_confidence(group, feature_type)
        
        # Collect source IDs
        source_ids = [cyl.id for cyl in group]
        
        return NormalizedCylinderFeature(
            id=feature_id,
            feature_type=feature_type,
            axis=axis,
            radius=radius,
            diameter=diameter,
            height=height,
            cylindrical_face=FaceReference(face_id=str(id(reference.cylindrical_face))),
            end_faces=all_end_faces[:2] if all_end_faces else [],  # Top and bottom
            metadata={
                'completeness': completeness.value,
                'confidence': confidence.value
            },
            manufacturing_attributes={
                'is_external': reference.is_external,
                'orientation': 'VERTICAL' if abs(axis.direction.z) > 0.9 else 'HORIZONTAL'
            },
            source_features=source_ids
        )
    
    def _classify_cylinder_type(self, axis: NormalizedAxis, height: float, 
                                original_class) -> NormalizedFeatureType:
        """Classify cylinder feature type."""
        # Check orientation
        is_vertical = abs(axis.direction.z) > 0.9
        
        if is_vertical:
            if height > self.shaft_height_threshold or math.isinf(height):
                return NormalizedFeatureType.CYLINDER_SHAFT
            else:
                return NormalizedFeatureType.CYLINDER_PILLAR
        else:
            if height > self.shaft_height_threshold or math.isinf(height):
                return NormalizedFeatureType.CYLINDER_SHAFT
            else:
                return NormalizedFeatureType.CYLINDER_BOSS
    
    def _assess_cylinder_completeness(self, end_faces: List, height: float) -> Completeness:
        """Assess completeness of a cylinder feature."""
        if len(end_faces) >= 2:
            return Completeness.COMPLETE
        elif len(end_faces) == 1:
            return Completeness.PARTIAL
        else:
            return Completeness.INCOMPLETE if not math.isinf(height) else Completeness.COMPLETE
    
    def _assess_cylinder_confidence(self, group: List[CylinderFeature], 
                                    feature_type: NormalizedFeatureType) -> Confidence:
        """Assess confidence in cylinder classification."""
        if len(group) == 1:
            return Confidence.HIGH
        else:
            # Check consistency
            radii = [c.radius for c in group]
            radius_variance = max(radii) - min(radii) if radii else 0
            return Confidence.HIGH if radius_variance < self.tolerance else Confidence.MEDIUM
    
    def _normalize_planar_faces(self, planar_faces: List[PlanarFaceFeature]) -> List[NormalizedPlanarFeature]:
        """Normalize and merge planar face features."""
        if not planar_faces:
            return []
        
        # Group coplanar faces
        groups = self._group_coplanar_faces(planar_faces)
        
        normalized_faces = []
        for i, group in enumerate(groups):
            if not group:
                continue
            
            merged = self._merge_planar_group(group, f"normalized_planar_{i}")
            if merged:
                normalized_faces.append(merged)
        
        return normalized_faces
    
    def _group_coplanar_faces(self, faces: List[PlanarFaceFeature]) -> List[List[PlanarFaceFeature]]:
        """Group coplanar and adjacent faces."""
        groups = []
        processed: Set[int] = set()
        
        for i, face in enumerate(faces):
            if i in processed:
                continue
            
            group = [face]
            processed.add(i)
            plane1 = self._extract_plane_from_face(face)
            
            for j, other_face in enumerate(faces):
                if j != i and j not in processed:
                    plane2 = self._extract_plane_from_face(other_face)
                    if self._are_coplanar(plane1, plane2):
                        group.append(other_face)
                        processed.add(j)
            
            groups.append(group)
        
        return groups
    
    def _extract_plane_from_face(self, face: PlanarFaceFeature) -> tuple:
        """Extract plane representation from planar face."""
        normal_occ = face.normal
        normal = Direction3D(
            normal_occ.X(),
            normal_occ.Y(),
            normal_occ.Z()
        ).normalize()
        
        # Get a point on the plane (from plane equation or face center)
        plane_occ = face.plane
        origin = Point3D(
            plane_occ.Location().X(),
            plane_occ.Location().Y(),
            plane_occ.Location().Z()
        )
        
        return (origin, normal)
    
    def _are_coplanar(self, plane1: tuple, plane2: tuple) -> bool:
        """Check if two planes are coplanar."""
        origin1, normal1 = plane1
        origin2, normal2 = plane2
        
        # Check if normals are parallel (or anti-parallel)
        dot = abs(normal1.x * normal2.x + normal1.y * normal2.y + normal1.z * normal2.z)
        if dot < (1.0 - self.tolerance):
            return False
        
        # Check if distance between planes is small
        vec = Point3D(
            origin2.x - origin1.x,
            origin2.y - origin1.y,
            origin2.z - origin1.z
        )
        distance = abs(vec.x * normal1.x + vec.y * normal1.y + vec.z * normal1.z)
        
        return distance <= self.tolerance
    
    def _merge_planar_group(self, group: List[PlanarFaceFeature], feature_id: str) -> Optional[NormalizedPlanarFeature]:
        """Merge a group of coplanar faces into a single normalized feature."""
        if not group:
            return None
        
        reference = group[0]
        origin, normal = self._extract_plane_from_face(reference)
        
        # Merge areas
        total_area = sum(face.area for face in group)
        
        # Collect all faces and edges
        all_faces = []
        all_edges = []
        
        for face in group:
            all_faces.append(FaceReference(face_id=str(id(face.face))))
            for edge in face.boundary_edges:
                all_edges.append(EdgeReference(edge_id=str(id(edge))))
        
        # Determine orientation
        orientation = reference.orientation
        
        # Assess completeness and confidence
        completeness = Completeness.COMPLETE if all_faces else Completeness.INCOMPLETE
        confidence = Confidence.HIGH if len(group) == 1 else Confidence.MEDIUM
        
        # Collect source IDs
        source_ids = [face.id for face in group]
        
        return NormalizedPlanarFeature(
            id=feature_id,
            feature_type=NormalizedFeatureType.PLANAR_SURFACE,
            plane_origin=origin,
            plane_normal=normal,
            area=total_area,
            boundary_edges=all_edges,
            merged_faces=all_faces,
            orientation=orientation,
            metadata={
                'completeness': completeness.value,
                'confidence': confidence.value
            },
            manufacturing_attributes={},
            source_features=source_ids
        )
    
    def _build_relationships(self, collection: NormalizedFeatureCollection) -> Dict[str, List[str]]:
        """Build relationships between normalized features."""
        relationships: Dict[str, List[str]] = {}
        
        # For now, return empty relationships
        # Can be extended to find adjacent features based on source feature IDs
        
        return relationships








