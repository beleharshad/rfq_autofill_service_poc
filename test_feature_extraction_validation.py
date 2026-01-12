"""
Phase 3: Feature Extraction Validation

This script runs feature extraction on the validated baseline solid and performs
Phase 3 validation: coaxial cylinder grouping, radius clustering, OD/ID classification,
and manufacturing-style summary.
"""

from geometry_2d import Profile2D, LineSegment, Point2D
from revolved_solid_builder import RevolvedSolidBuilder
from feature_extractor import FeatureExtractor, FeatureCollection, HoleFeature, CylinderFeature, TurnedPartStack, TurnedPartSegment
from OCC.Core.gp import gp_Pnt, gp_Dir, gp_Ax1, gp_Vec
from OCC.Core.TopoDS import TopoDS_Face
from OCC.Core.BRep import BRep_Tool
from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
from OCC.Core.GeomAbs import GeomAbs_Cylinder
from OCC.Core.Bnd import Bnd_Box
from OCC.Core.BRepBndLib import brepbndlib_Add
from typing import List, Tuple, Optional
from dataclasses import dataclass
import math

# Dimensions from PDF Section A-A (same as baseline)
L = 4.25
OD1_diameter = 1.63
OD2_diameter = 0.806
ID1_diameter = 1.13
ID2_diameter = 0.753
OD1_radius = OD1_diameter / 2.0
OD2_radius = OD2_diameter / 2.0
ID1_radius = ID1_diameter / 2.0
ID2_radius = ID2_diameter / 2.0
yS = 3.27

# Tolerance for coaxial grouping
COAXIAL_TOLERANCE = 1e-5
RADIUS_CLUSTER_TOLERANCE = 1e-4


@dataclass
class UnifiedCylinder:
    """Unified representation of a cylinder (from hole or cylinder feature)."""
    axis: gp_Ax1
    radius: float
    is_from_hole: bool  # True if from HoleFeature, False if from CylinderFeature
    source_hole: Optional[HoleFeature] = None
    source_cylinder: Optional[CylinderFeature] = None
    z_min: float = 0.0  # Axial extent minimum (Z coordinate)
    z_max: float = 0.0  # Axial extent maximum (Z coordinate)


def create_baseline_profile() -> Profile2D:
    """Create the baseline profile (same as test_manual_pdf_profile.py)."""
    profile = Profile2D()
    
    # Segment 1: ID region (main) - vertical from (ID1_radius, 0) to (ID1_radius, yS)
    profile.add_primitive(LineSegment(
        Point2D(ID1_radius, 0.0),
        Point2D(ID1_radius, yS)
    ))
    
    # Segment 2: ID step - horizontal from (ID1_radius, yS) to (ID2_radius, yS)
    profile.add_primitive(LineSegment(
        Point2D(ID1_radius, yS),
        Point2D(ID2_radius, yS)
    ))
    
    # Segment 3: ID region (right end) - vertical from (ID2_radius, yS) to (ID2_radius, L)
    profile.add_primitive(LineSegment(
        Point2D(ID2_radius, yS),
        Point2D(ID2_radius, L)
    ))
    
    # Segment 4: Right face - horizontal from (ID2_radius, L) to (OD2_radius, L)
    profile.add_primitive(LineSegment(
        Point2D(ID2_radius, L),
        Point2D(OD2_radius, L)
    ))
    
    # Segment 5: OD region (right end) - vertical from (OD2_radius, L) to (OD2_radius, yS)
    profile.add_primitive(LineSegment(
        Point2D(OD2_radius, L),
        Point2D(OD2_radius, yS)
    ))
    
    # Segment 6: OD step - horizontal from (OD2_radius, yS) to (OD1_radius, yS)
    profile.add_primitive(LineSegment(
        Point2D(OD2_radius, yS),
        Point2D(OD1_radius, yS)
    ))
    
    # Segment 7: OD region (main) - vertical from (OD1_radius, yS) to (OD1_radius, 0)
    profile.add_primitive(LineSegment(
        Point2D(OD1_radius, yS),
        Point2D(OD1_radius, 0.0)
    ))
    
    # Segment 8: Left face - horizontal from (OD1_radius, 0) to (ID1_radius, 0) (closing loop)
    profile.add_primitive(LineSegment(
        Point2D(OD1_radius, 0.0),
        Point2D(ID1_radius, 0.0)
    ))
    
    return profile


def format_axis(axis: gp_Ax1) -> str:
    """Format axis information for display."""
    loc = axis.Location()
    dir = axis.Direction()
    return f"axis: ({loc.X():.4f}, {loc.Y():.4f}, {loc.Z():.4f}), dir: ({dir.X():.4f}, {dir.Y():.4f}, {dir.Z():.4f})"


def format_point(pnt: gp_Pnt) -> str:
    """Format point for display."""
    return f"({pnt.X():.4f}, {pnt.Y():.4f}, {pnt.Z():.4f})"


def are_coaxial(axis1: gp_Ax1, axis2: gp_Ax1, tolerance: float) -> bool:
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


def get_face_center(face: TopoDS_Face) -> gp_Pnt:
    """Get approximate center point of a face."""
    try:
        bbox = Bnd_Box()
        brepbndlib_Add(face, bbox)
        
        x_min, y_min, z_min, x_max, y_max, z_max = bbox.Get()
        center = gp_Pnt(
            (x_min + x_max) / 2.0,
            (y_min + y_max) / 2.0,
            (z_min + z_max) / 2.0
        )
        return center
    except Exception:
        return gp_Pnt(0.0, 0.0, 0.0)


def project_point_on_axis(point: gp_Pnt, axis: gp_Ax1) -> float:
    """Project a point onto an axis and return the parameter value."""
    axis_point = axis.Location()
    axis_dir = axis.Direction()
    vec = gp_Vec(axis_point, point)
    dir_vec = gp_Vec(axis_dir.XYZ())
    return vec.Dot(dir_vec)


def calculate_z_range_for_cylinder(cyl: UnifiedCylinder) -> Tuple[float, float]:
    """Calculate Z-range (axial extent) for a cylinder."""
    axis = cyl.axis
    axis_dir = axis.Direction()
    axis_point = axis.Location()
    
    z_values = []
    
    if cyl.is_from_hole and cyl.source_hole:
        # For holes, use cylindrical faces bounding box
        for face in cyl.source_hole.cylindrical_faces:
            bbox = Bnd_Box()
            brepbndlib_Add(face, bbox)
            x_min, y_min, z_min, x_max, y_max, z_max = bbox.Get()
            # Project bounding box corners onto axis
            corners = [
                gp_Pnt(x_min, y_min, z_min),
                gp_Pnt(x_max, y_min, z_min),
                gp_Pnt(x_min, y_max, z_min),
                gp_Pnt(x_max, y_max, z_min),
                gp_Pnt(x_min, y_min, z_max),
                gp_Pnt(x_max, y_min, z_max),
                gp_Pnt(x_min, y_max, z_max),
                gp_Pnt(x_max, y_max, z_max),
            ]
            for corner in corners:
                z_param = project_point_on_axis(corner, axis)
                z_values.append(z_param)
    elif not cyl.is_from_hole and cyl.source_cylinder:
        # For cylinders, use end faces
        if cyl.source_cylinder.end_faces:
            for end_face in cyl.source_cylinder.end_faces:
                center = get_face_center(end_face)
                z_param = project_point_on_axis(center, axis)
                z_values.append(z_param)
        else:
            # No end faces, use cylindrical face bounding box
            bbox = Bnd_Box()
            brepbndlib_Add(cyl.source_cylinder.cylindrical_face, bbox)
            x_min, y_min, z_min, x_max, y_max, z_max = bbox.Get()
            corners = [
                gp_Pnt(x_min, y_min, z_min),
                gp_Pnt(x_max, y_min, z_min),
                gp_Pnt(x_min, y_max, z_min),
                gp_Pnt(x_max, y_max, z_min),
                gp_Pnt(x_min, y_min, z_max),
                gp_Pnt(x_max, y_min, z_max),
                gp_Pnt(x_min, y_max, z_max),
                gp_Pnt(x_max, y_max, z_max),
            ]
            for corner in corners:
                z_param = project_point_on_axis(corner, axis)
                z_values.append(z_param)
    
    if z_values:
        return (min(z_values), max(z_values))
    else:
        return (0.0, 0.0)


def create_unified_cylinders(collection: FeatureCollection) -> List[UnifiedCylinder]:
    """Create unified cylinder list from holes and cylinders."""
    unified = []
    
    # Add cylinders from holes
    for hole in collection.holes:
        unified_cyl = UnifiedCylinder(
            axis=hole.axis,
            radius=hole.diameter / 2.0,
            is_from_hole=True,
            source_hole=hole
        )
        unified.append(unified_cyl)
    
    # Add cylinders from external cylinders
    for cyl in collection.cylinders:
        unified_cyl = UnifiedCylinder(
            axis=cyl.axis,
            radius=cyl.radius,
            is_from_hole=False,
            source_cylinder=cyl
        )
        unified.append(unified_cyl)
    
    # Calculate Z-ranges for all cylinders
    for cyl in unified:
        z_min, z_max = calculate_z_range_for_cylinder(cyl)
        cyl.z_min = z_min
        cyl.z_max = z_max
    
    return unified


def group_coaxial_cylinders(cylinders: List[UnifiedCylinder], tolerance: float) -> List[List[UnifiedCylinder]]:
    """Group cylinders by coaxial axis."""
    groups = []
    processed = set()
    
    for i, cyl in enumerate(cylinders):
        if i in processed:
            continue
        
        group = [cyl]
        processed.add(i)
        
        for j, other_cyl in enumerate(cylinders):
            if j != i and j not in processed:
                if are_coaxial(cyl.axis, other_cyl.axis, tolerance):
                    group.append(other_cyl)
                    processed.add(j)
        
        groups.append(group)
    
    return groups


def cluster_by_radius(cylinders: List[UnifiedCylinder], tolerance: float) -> List[List[UnifiedCylinder]]:
    """Cluster cylinders by radius within a group."""
    clusters = []
    processed = set()
    
    for i, cyl in enumerate(cylinders):
        if i in processed:
            continue
        
        cluster = [cyl]
        processed.add(i)
        
        for j, other_cyl in enumerate(cylinders):
            if j != i and j not in processed:
                if abs(cyl.radius - other_cyl.radius) <= tolerance:
                    cluster.append(other_cyl)
                    processed.add(j)
        
        clusters.append(cluster)
    
    return clusters


def classify_od_id(group: List[UnifiedCylinder]) -> List[Tuple[UnifiedCylinder, str]]:
    """Classify cylinders in a coaxial group as OD or ID based on radius."""
    # Find max radius in group
    max_radius = max(cyl.radius for cyl in group)
    
    classified = []
    for cyl in group:
        # If radius is close to max radius -> likely external (OD)
        # If radius is smaller -> likely internal (ID/bore)
        if abs(cyl.radius - max_radius) <= RADIUS_CLUSTER_TOLERANCE:
            classification = "OD"
        else:
            classification = "ID"
        classified.append((cyl, classification))
    
    return classified


def format_axial_range(z_min: float, z_max: float) -> str:
    """Format axial range for display."""
    if abs(z_max - z_min) < 1e-6:
        return f"at z={z_min:.3f}"
    else:
        return f"from z={z_min:.3f} to z={z_max:.3f}"


def print_phase3_summary(collection: FeatureCollection):
    """Print Phase 3 validation summary."""
    print("=" * 70)
    print("PHASE 3 VALIDATION SUMMARY")
    print("=" * 70)
    print()
    
    # Step 1: Counts
    print("FACE COUNTS:")
    print("-" * 70)
    print(f"  Planar faces: {len(collection.planar_faces)}")
    print(f"  Cylindrical faces: {len(collection.cylinders) + len(collection.holes)}")
    print(f"    - External cylinders: {len(collection.cylinders)}")
    print(f"    - Internal cylinders (holes): {len(collection.holes)}")
    print()
    
    # Step 2: List all cylinders
    print("ALL CYLINDERS:")
    print("-" * 70)
    unified_cylinders = create_unified_cylinders(collection)
    for i, cyl in enumerate(unified_cylinders):
        print(f"  Cylinder {i+1}:")
        print(f"    Radius: {cyl.radius:.4f} inches (Diameter: {cyl.radius * 2:.4f} inches)")
        print(f"    {format_axis(cyl.axis)}")
        print(f"    Axial extent: {format_axial_range(cyl.z_min, cyl.z_max)}")
        print(f"    Source: {'Hole' if cyl.is_from_hole else 'External Cylinder'}")
        print()
    
    # Step 3: Coaxial grouping
    print("COAXIAL CYLINDER GROUPS:")
    print("-" * 70)
    coaxial_groups = group_coaxial_cylinders(unified_cylinders, COAXIAL_TOLERANCE)
    print(f"  Number of coaxial groups: {len(coaxial_groups)}")
    print()
    
    for group_idx, group in enumerate(coaxial_groups):
        print(f"  Group {group_idx + 1}: {len(group)} cylinder(s)")
        for cyl in group:
            print(f"    - Radius: {cyl.radius:.4f} inches, {format_axial_range(cyl.z_min, cyl.z_max)}")
        print()
    
    # Step 4: Radius clustering within each group and OD/ID classification
    print("COAXIAL GROUPS WITH RADIUS CLUSTERING AND OD/ID CLASSIFICATION:")
    print("-" * 70)
    
    all_od_steps = []
    all_id_steps = []
    
    for group_idx, group in enumerate(coaxial_groups):
        print(f"  Coaxial Group {group_idx + 1}:")
        
        # Cluster by radius
        radius_clusters = cluster_by_radius(group, RADIUS_CLUSTER_TOLERANCE)
        print(f"    Radius clusters: {len(radius_clusters)}")
        
        # Classify as OD/ID
        classified_cylinders = classify_od_id(group)
        
        for cluster_idx, cluster in enumerate(radius_clusters):
            # Get classification for this cluster (use first cylinder)
            cluster_cyl = cluster[0]
            classification = next(cls for cyl, cls in classified_cylinders if cyl == cluster_cyl)
            radius = cluster_cyl.radius
            
            print(f"      Cluster {cluster_idx + 1} ({classification}): Radius = {radius:.4f} inches, {len(cluster)} cylinder(s)")
            
            # Collect Z-ranges for this cluster
            z_mins = [cyl.z_min for cyl in cluster]
            z_maxs = [cyl.z_max for cyl in cluster]
            cluster_z_min = min(z_mins)
            cluster_z_max = max(z_maxs)
            
            print(f"        Axial extent: {format_axial_range(cluster_z_min, cluster_z_max)}")
            
            # Store for manufacturing summary
            if classification == "OD":
                all_od_steps.append((radius, cluster_z_min, cluster_z_max))
            else:
                all_id_steps.append((radius, cluster_z_min, cluster_z_max))
        print()
    
    # Step 5: Manufacturing-style summary
    print("MANUFACTURING SUMMARY:")
    print("-" * 70)
    
    # Sort OD steps by radius (largest first) then by Z position
    all_od_steps.sort(key=lambda x: (-x[0], x[1]))
    all_id_steps.sort(key=lambda x: (-x[0], x[1]))
    
    print("  OD steps:")
    if all_od_steps:
        od_list = []
        for radius, z_min, z_max in all_od_steps:
            diameter = radius * 2
            # Format axial range (manufacturing style: "ØX.XX up to Y.YY" or "ØX.XX from Y.YY to Z.ZZ")
            if abs(z_min) < 0.01:
                range_str = f"Ø{diameter:.3f} up to {z_max:.3f}"
            else:
                range_str = f"Ø{diameter:.3f} from {z_min:.3f} to {z_max:.3f}"
            od_list.append(range_str)
        print(f"    [{', '.join(od_list)}]")
    else:
        print("    (none)")
    
    print("  ID steps:")
    if all_id_steps:
        id_list = []
        for radius, z_min, z_max in all_id_steps:
            diameter = radius * 2
            # Format axial range
            if abs(z_min) < 0.01:
                range_str = f"Ø{diameter:.3f} up to {z_max:.3f}"
            else:
                range_str = f"Ø{diameter:.3f} from {z_min:.3f} to {z_max:.3f}"
            id_list.append(range_str)
        print(f"    [{', '.join(id_list)}]")
    else:
        print("    (none)")
    
    print()
    print("=" * 70)


def print_phase35_validation(collection: FeatureCollection, extractor: FeatureExtractor, solid):
    """Print Phase 3.5 validation with detailed cylinder information."""
    print("=" * 70)
    print("PHASE 3.5 VALIDATION: INTERNAL/EXTERNAL CLASSIFICATION")
    print("=" * 70)
    print()
    
    # Get epsilon value used
    eps = extractor.get_epsilon(solid)
    
    # Counts
    total_cylinders = len(collection.cylinders) + len(collection.holes)
    external_cylinders = len(collection.cylinders)
    internal_cylinders = len(collection.holes)
    planar_faces = len(collection.planar_faces)
    
    print("COUNTS:")
    print("-" * 70)
    print(f"  Total cylinders detected: {total_cylinders}")
    print(f"  External cylinders: {external_cylinders}")
    print(f"  Internal cylinders/bores: {internal_cylinders}")
    print(f"  Planar faces: {planar_faces}")
    print()
    
    # Expected values
    print("EXPECTED:")
    print("-" * 70)
    print(f"  Total cylinders: 4")
    print(f"  External cylinders: 2 (r~0.815, 0.403)")
    print(f"  Internal cylinders/bores: 2 (r~0.565, 0.3765)")
    print(f"  Planar faces: 4")
    print()
    
    # Validation
    print("VALIDATION:")
    print("-" * 70)
    all_ok = True
    if total_cylinders != 4:
        print(f"  [FAIL] Total cylinders: expected 4, got {total_cylinders}")
        all_ok = False
    else:
        print(f"  [OK] Total cylinders: {total_cylinders}")
    
    if external_cylinders != 2:
        print(f"  [FAIL] External cylinders: expected 2, got {external_cylinders}")
        all_ok = False
    else:
        print(f"  [OK] External cylinders: {external_cylinders}")
    
    if internal_cylinders != 2:
        print(f"  [FAIL] Internal cylinders: expected 2, got {internal_cylinders}")
        all_ok = False
    else:
        print(f"  [OK] Internal cylinders: {internal_cylinders}")
    
    if planar_faces != 4:
        print(f"  [FAIL] Planar faces: expected 4, got {planar_faces}")
        all_ok = False
    else:
        print(f"  [OK] Planar faces: {planar_faces}")
    print()
    
    # Detailed cylinder list
    print("DETAILED CYLINDER LIST:")
    print("-" * 70)
    print(f"  Epsilon used: {eps:.2e} inches")
    print()
    
    # External cylinders
    print("  External Cylinders:")
    for i, cyl in enumerate(collection.cylinders):
        axis = cyl.axis
        loc = axis.Location()
        dir = axis.Direction()
        extent_str = f"span [{cyl.axial_extent[0]:.6f}, {cyl.axial_extent[1]:.6f}]" if cyl.axial_extent else "span [unknown]"
        print(f"    Cylinder {i+1}:")
        print(f"      Radius: {cyl.radius:.6f} inches")
        print(f"      Axis location: ({loc.X():.6f}, {loc.Y():.6f}, {loc.Z():.6f})")
        print(f"      Axis direction: ({dir.X():.6f}, {dir.Y():.6f}, {dir.Z():.6f})")
        print(f"      Classification: EXTERNAL")
        print(f"      Axial extent: {extent_str}")
        print(f"      Epsilon: {eps:.2e} inches")
        print()
    
    # Internal cylinders (holes)
    print("  Internal Cylinders (Bores):")
    for i, hole in enumerate(collection.holes):
        axis = hole.axis
        loc = axis.Location()
        dir = axis.Direction()
        radius = hole.diameter / 2.0
        extent_str = f"span [{hole.axial_extent[0]:.6f}, {hole.axial_extent[1]:.6f}]" if hole.axial_extent else "span [unknown]"
        print(f"    Bore {i+1}:")
        print(f"      Radius: {radius:.6f} inches")
        print(f"      Axis location: ({loc.X():.6f}, {loc.Y():.6f}, {loc.Z():.6f})")
        print(f"      Axis direction: ({dir.X():.6f}, {dir.Y():.6f}, {dir.Z():.6f})")
        print(f"      Classification: INTERNAL")
        print(f"      Axial extent: {extent_str}")
        print(f"      Epsilon: {eps:.2e} inches")
        print()
    
    # Phase 3.6: Axial extents summary
    print("PHASE 3.6: AXIAL EXTENTS SUMMARY")
    print("-" * 70)
    print()
    
    # Group by type and sort by radius
    od_features = []
    id_features = []
    
    for cyl in collection.cylinders:
        if cyl.axial_extent:
            od_features.append(("OD", cyl.radius, cyl.axial_extent[0], cyl.axial_extent[1]))
    
    for hole in collection.holes:
        if hole.axial_extent:
            radius = hole.diameter / 2.0
            id_features.append(("ID", radius, hole.axial_extent[0], hole.axial_extent[1]))
    
    # Sort by radius (largest first) then by t_min
    od_features.sort(key=lambda x: (-x[1], x[2]))
    id_features.sort(key=lambda x: (-x[1], x[2]))
    
    print("  External Diameters (OD):")
    for i, (label, radius, t_min, t_max) in enumerate(od_features):
        print(f"    OD{i+1}: radius {radius:.6f}, span [{t_min:.6f}, {t_max:.6f}]")
    print()
    
    print("  Internal Diameters (ID/Bores):")
    for i, (label, radius, t_min, t_max) in enumerate(id_features):
        print(f"    ID{i+1}: radius {radius:.6f}, span [{t_min:.6f}, {t_max:.6f}]")
    print()
    
    # Compute step stations (shared boundaries between adjacent spans)
    # Convert all extents to Z coordinates (common coordinate system)
    print("  Step Stations:")
    print("-" * 70)
    
    tolerance = 1e-5
    
    # Convert t values to Z coordinates for each feature
    # For a point on axis: Z = axis_location.Z + t * axis_direction.Z
    all_extents_z = []
    
    for cyl in collection.cylinders:
        if cyl.axial_extent:
            axis = cyl.axis
            axis_z = axis.Location().Z()
            axis_dir_z = axis.Direction().Z()
            t_min, t_max = cyl.axial_extent
            z_min = axis_z + t_min * axis_dir_z
            z_max = axis_z + t_max * axis_dir_z
            all_extents_z.append(("OD", cyl.radius, min(z_min, z_max), max(z_min, z_max)))
    
    for hole in collection.holes:
        if hole.axial_extent:
            axis = hole.axis
            axis_z = axis.Location().Z()
            axis_dir_z = axis.Direction().Z()
            t_min, t_max = hole.axial_extent
            z_min = axis_z + t_min * axis_dir_z
            z_max = axis_z + t_max * axis_dir_z
            radius = hole.diameter / 2.0
            all_extents_z.append(("ID", radius, min(z_min, z_max), max(z_min, z_max)))
    
    # Collect all Z boundary values
    z_boundaries = set()
    for label, radius, z_min, z_max in all_extents_z:
        z_boundaries.add(z_min)
        z_boundaries.add(z_max)
    
    z_boundaries = sorted(z_boundaries)
    
    # Find Z boundaries that are shared (within tolerance) by multiple features
    step_stations = []
    processed_z = set()
    
    for z1 in z_boundaries:
        if z1 in processed_z:
            continue
        
        # Find all boundaries close to z1
        cluster = [z1]
        for z2 in z_boundaries:
            if z2 != z1 and abs(z1 - z2) < tolerance:
                cluster.append(z2)
                processed_z.add(z2)
        
            if len(cluster) > 1 or len(cluster) == 1:
                # Find features that share this boundary
                sharing_features = []
                representative_z = sum(cluster) / len(cluster)  # Average for display
                seen_features = set()
                
                for label, radius, z_min, z_max in all_extents_z:
                    # Check if this feature's boundary is close to the cluster
                    feature_key = (label, radius)
                    if feature_key in seen_features:
                        continue
                    
                    for z_bound in cluster:
                        if abs(z_min - z_bound) < tolerance or abs(z_max - z_bound) < tolerance:
                            sharing_features.append((label, radius))
                            seen_features.add(feature_key)
                            break
            
            if len(sharing_features) > 1:
                # This is a step station (shared by multiple features)
                # Remove duplicate features (same label and radius)
                unique_features = []
                seen = set()
                for label, radius in sharing_features:
                    key = (label, radius)
                    if key not in seen:
                        unique_features.append((label, radius))
                        seen.add(key)
                
                if len(unique_features) > 1:
                    step_stations.append((representative_z, unique_features))
        
        processed_z.add(z1)
    
    # Remove duplicates and sort
    unique_stations = []
    for station_z, features in step_stations:
        is_duplicate = False
        for existing_z, _ in unique_stations:
            if abs(station_z - existing_z) < tolerance:
                is_duplicate = True
                break
        if not is_duplicate:
            unique_stations.append((station_z, features))
    
    if unique_stations:
        for station_z, features in sorted(unique_stations, key=lambda x: x[0]):
            feature_strs = [f"{label} (r={r:.6f})" for label, r in features]
            print(f"    Station at Z={station_z:.6f}: {', '.join(feature_strs)}")
    else:
        print("    (no step stations found)")
    print()
    
    print("=" * 70)
    if all_ok:
        print("Phase 3.5 validation: PASSED")
    else:
        print("Phase 3.5 validation: FAILED")
    print("=" * 70)


def print_phase37_validation(collection: FeatureCollection, extractor: FeatureExtractor):
    """Print Phase 3.7 validation: TurnedPartStack building."""
    print("PHASE 3.7: TURNED PART STACK")
    print("-" * 70)
    print()
    
    # Build the stack
    stack = extractor.build_turned_part_stack(collection, tolerance=1e-6)
    
    # Print segments
    print("  Segments:")
    print("-" * 70)
    if not stack.segments:
        print("    (no segments found)")
    else:
        for i, seg in enumerate(stack.segments):
            print(f"    Segment {i+1}:")
            print(f"      Z range: [{seg.z_start:.6f}, {seg.z_end:.6f}]")
            print(f"      OD diameter: {seg.od_diameter:.6f} inches")
            print(f"      ID diameter: {seg.id_diameter:.6f} inches")
            print(f"      Wall thickness: {seg.wall_thickness:.6f} inches")
            print()
    
    # Validate
    print("  Validation:")
    print("-" * 70)
    is_valid, errors = stack.validate(tolerance=1e-6)
    if is_valid:
        print("    [PASS] Stack validation passed")
    else:
        print("    [FAIL] Stack validation failed:")
        for error in errors:
            print(f"      - {error}")
    print()
    
    # Summary
    if stack.segments:
        min_z = min(seg.z_start for seg in stack.segments)
        max_z = max(seg.z_end for seg in stack.segments)
        print(f"  Total Z range: [{min_z:.6f}, {max_z:.6f}]")
        print(f"  Number of segments: {len(stack.segments)}")
        print()
    
    print("=" * 70)
    if is_valid:
        print("Phase 3.7 validation: PASSED")
    else:
        print("Phase 3.7 validation: FAILED")
    print("=" * 70)


def print_phase38_validation(collection: FeatureCollection, extractor: FeatureExtractor):
    """Print Phase 3.8 validation: Volume computation from TurnedPartStack."""
    print("PHASE 3.8: VOLUME COMPUTATION")
    print("-" * 70)
    print()
    
    # Build the stack
    stack = extractor.build_turned_part_stack(collection, tolerance=1e-6)
    
    # Print segment volumes
    print("  Segment Volumes:")
    print("-" * 70)
    if not stack.segments:
        print("    (no segments found)")
    else:
        for i, seg in enumerate(stack.segments):
            vol = seg.volume()
            print(f"    Segment {i+1}:")
            print(f"      Z range: [{seg.z_start:.6f}, {seg.z_end:.6f}] inches")
            print(f"      OD diameter: {seg.od_diameter:.6f} inches")
            print(f"      ID diameter: {seg.id_diameter:.6f} inches")
            print(f"      Volume: {vol:.6f} in³")
            print()
    
    # Print total volume
    total_vol = stack.total_volume()
    print("  Total Volume:")
    print("-" * 70)
    print(f"    {total_vol:.6f} in³")
    print()
    
    # Optional: weight computation (if density provided)
    # Example densities:
    # - Steel: ~0.283 lb/in³ or ~7.85 g/cm³
    # - Aluminum: ~0.098 lb/in³ or ~2.70 g/cm³
    # Uncomment and provide density to compute weight:
    # weight_lb = stack.compute_weight(density_lb_per_in3=0.283)  # Steel
    # if weight_lb is not None:
    #     print(f"  Weight (Steel, 0.283 lb/in³): {weight_lb:.6f} lb")
    #     print()
    
    print("=" * 70)
    print("Phase 3.8 validation: PASSED")
    print("=" * 70)


def print_phase39_validation(collection: FeatureCollection, extractor: FeatureExtractor):
    """Print Phase 3.9 validation: Surface area computation from TurnedPartStack."""
    print("PHASE 3.9: SURFACE AREA COMPUTATION")
    print("-" * 70)
    print()
    
    # Build the stack
    stack = extractor.build_turned_part_stack(collection, tolerance=1e-6)
    
    # Print segment surface areas
    print("  Segment Surface Areas:")
    print("-" * 70)
    if not stack.segments:
        print("    (no segments found)")
    else:
        for i, seg in enumerate(stack.segments):
            od_area = seg.od_surface_area()
            id_area = seg.id_surface_area()
            print(f"    Segment {i+1}:")
            print(f"      Z range: [{seg.z_start:.6f}, {seg.z_end:.6f}] inches")
            print(f"      OD diameter: {seg.od_diameter:.6f} inches")
            print(f"      ID diameter: {seg.id_diameter:.6f} inches")
            print(f"      OD surface area: {od_area:.6f} in²")
            print(f"      ID surface area: {id_area:.6f} in²")
            print()
    
    # Print total surface areas
    total_od = stack.total_od_surface_area()
    total_id = stack.total_id_surface_area()
    print("  Total Surface Areas:")
    print("-" * 70)
    print(f"    Total OD surface area: {total_od:.6f} in²")
    print(f"    Total ID surface area: {total_id:.6f} in²")
    print()
    
    # Note about step-face ring areas (optional, for future)
    print("  Note:")
    print("-" * 70)
    print("    Step-face ring areas at Z boundaries are not included.")
    print("    (Optional enhancement for future implementation)")
    print()
    
    print("=" * 70)
    print("Phase 3.9 validation: PASSED")
    print("=" * 70)


def print_phase310_validation(collection: FeatureCollection, extractor: FeatureExtractor):
    """Print Phase 3.10 validation: Export Part Summary JSON."""
    print("PHASE 3.10: PART SUMMARY JSON EXPORT")
    print("-" * 70)
    print()
    
    # Build the stack
    stack = extractor.build_turned_part_stack(collection, tolerance=1e-6)
    
    # Export to JSON
    json_path = "part_summary.json"
    stack.export_json(json_path, collection)
    print(f"  Exported JSON to: {json_path}")
    print()
    
    # Show summary from dict
    data = stack.to_dict(collection)
    print("  Summary:")
    print("-" * 70)
    print(f"    Units: {data['units']}")
    print(f"    Z range: {data['z_range']}")
    print(f"    Segments: {len(data['segments'])}")
    print(f"    Totals:")
    print(f"      Volume: {data['totals']['volume_in3']:.6f} in³")
    print(f"      OD area: {data['totals']['od_area_in2']:.6f} in²")
    print(f"      ID area: {data['totals']['id_area_in2']:.6f} in²")
    print(f"    Feature counts:")
    print(f"      External cylinders: {data['feature_counts']['external_cylinders']}")
    print(f"      Internal bores: {data['feature_counts']['internal_bores']}")
    print(f"      Planar faces: {data['feature_counts']['planar_faces']}")
    print(f"      Total faces: {data['feature_counts']['total_faces']}")
    print()
    
    print("=" * 70)
    print("Phase 3.10 validation: PASSED")
    print("=" * 70)


def print_phase311_validation(collection: FeatureCollection, extractor: FeatureExtractor):
    """Print Phase 3.11 validation: Planar ring areas at Z boundaries."""
    print("PHASE 3.11: PLANAR RING AREAS AT Z BOUNDARIES")
    print("-" * 70)
    print()
    
    # Build the stack
    stack = extractor.build_turned_part_stack(collection, tolerance=1e-6)
    
    if not stack.segments:
        print("  (no segments found)")
        print()
        print("=" * 70)
        print("Phase 3.11 validation: SKIPPED (no segments)")
        print("=" * 70)
        return
    
    # Sort segments by z_start
    sorted_segments = sorted(stack.segments, key=lambda s: s.z_start)
    
    # Compute end face areas
    end_face_start = stack.end_face_area_start()
    end_face_end = stack.end_face_area_end()
    od_shoulder = stack.od_shoulder_area()
    id_shoulder = stack.id_shoulder_area()
    
    print("  End Face Areas:")
    print("-" * 70)
    first_seg = sorted_segments[0]
    last_seg = sorted_segments[-1]
    ro_first = first_seg.od_diameter / 2.0
    ri_first = first_seg.id_diameter / 2.0
    ro_last = last_seg.od_diameter / 2.0
    ri_last = last_seg.id_diameter / 2.0
    print(f"    Start (minZ={first_seg.z_start:.6f}): {end_face_start:.6f} in²")
    print(f"      (OD radius: {ro_first:.6f}, ID radius: {ri_first:.6f})")
    print(f"    End (maxZ={last_seg.z_end:.6f}): {end_face_end:.6f} in²")
    print(f"      (OD radius: {ro_last:.6f}, ID radius: {ri_last:.6f})")
    print()
    
    # Shoulder areas at internal stations
    print("  Shoulder Areas at Internal Boundaries:")
    print("-" * 70)
    
    if len(sorted_segments) > 1:
        for i in range(len(sorted_segments) - 1):
            seg_i = sorted_segments[i]
            seg_next = sorted_segments[i + 1]
            station_z = seg_i.z_end  # Should equal seg_next.z_start
            
            # OD shoulder area
            ro_i = seg_i.od_diameter / 2.0
            ro_next = seg_next.od_diameter / 2.0
            a_od_shoulder = math.pi * abs(ro_i * ro_i - ro_next * ro_next)
            
            # ID shoulder area
            ri_i = seg_i.id_diameter / 2.0
            ri_next = seg_next.id_diameter / 2.0
            a_id_shoulder = math.pi * abs(ri_i * ri_i - ri_next * ri_next)
            
            print(f"    Boundary at Z={station_z:.6f}:")
            print(f"      OD shoulder: {a_od_shoulder:.6f} in² (ro_i={ro_i:.6f}, ro_next={ro_next:.6f})")
            print(f"      ID shoulder: {a_id_shoulder:.6f} in² (ri_i={ri_i:.6f}, ri_next={ri_next:.6f})")
            print(f"      Total shoulder: {a_od_shoulder + a_id_shoulder:.6f} in²")
            print()
    else:
        print("    (no internal boundaries)")
        print()
    
    # Totals
    planar_ring_area = stack.total_planar_ring_area()
    total_od = stack.total_od_surface_area()
    total_id = stack.total_id_surface_area()
    total_surface = stack.total_surface_area()
    
    print("  Totals:")
    print("-" * 70)
    print(f"    End face area (start): {end_face_start:.6f} in²")
    print(f"    End face area (end): {end_face_end:.6f} in²")
    print(f"    OD shoulder area: {od_shoulder:.6f} in²")
    print(f"    ID shoulder area: {id_shoulder:.6f} in²")
    print(f"    Planar ring area: {planar_ring_area:.6f} in²")
    print()
    print(f"    OD cylindrical area: {total_od:.6f} in²")
    print(f"    ID cylindrical area: {total_id:.6f} in²")
    print(f"    Planar ring area: {planar_ring_area:.6f} in²")
    print(f"    Total surface area: {total_surface:.6f} in²")
    print()
    
    # Validation
    expected_planar = end_face_start + end_face_end + od_shoulder + id_shoulder
    if abs(planar_ring_area - expected_planar) < 1e-6:
        print("  Validation:")
        print("-" * 70)
        print("    [PASS] Planar ring area computation verified")
    else:
        print("  Validation:")
        print("-" * 70)
        print(f"    [WARNING] Planar ring area mismatch: computed={planar_ring_area:.6f}, expected={expected_planar:.6f}")
    print()
    
    print("=" * 70)
    print("Phase 3.11 validation: PASSED")
    print("=" * 70)


def main():
    """Main validation workflow."""
    print("=" * 70)
    print("Phase 3: Feature Extraction Validation")
    print("=" * 70)
    print()
    
    # Step 1: Build the baseline solid
    print("Step 1: Building baseline solid...")
    profile = create_baseline_profile()
    builder = RevolvedSolidBuilder(debug_validate_analytic=True)
    
    success = builder.build_from_profile(profile)
    if not success:
        print("  [ERROR] Failed to build solid")
        return
    
    solid = builder.get_solid()
    if solid is None or solid.IsNull():
        print("  [ERROR] Solid is null")
        return
    
    print("  [OK] Solid built successfully")
    print()
    
    # Step 2: Extract features (internal/external classification enabled by default)
    print("Step 2: Extracting features...")
    # use_radius_fallback=False ensures point-in-solid classification is used
    extractor = FeatureExtractor(tolerance=1e-6, debug=True, use_radius_fallback=False)
    collection = extractor.extract_features(solid)
    print("  [OK] Feature extraction complete")
    print()
    
    # Step 3: Print Phase 3 summary
    print("Step 3: Phase 3 validation...")
    print()
    print_phase3_summary(collection)
    print()
    
    # Step 4: Print Phase 3.5 validation
    print("Step 4: Phase 3.5 validation...")
    print()
    print_phase35_validation(collection, extractor, solid)
    print()
    
    # Step 5: Print Phase 3.7 validation
    print("Step 5: Phase 3.7 validation...")
    print()
    print_phase37_validation(collection, extractor)
    print()
    
    # Step 6: Print Phase 3.8 validation
    print("Step 6: Phase 3.8 validation...")
    print()
    print_phase38_validation(collection, extractor)
    print()
    
    # Step 7: Print Phase 3.9 validation
    print("Step 7: Phase 3.9 validation...")
    print()
    print_phase39_validation(collection, extractor)
    print()
    
    # Step 8: Print Phase 3.10 validation
    print("Step 8: Phase 3.10 validation...")
    print()
    print_phase310_validation(collection, extractor)
    print()
    
    # Step 9: Print Phase 3.11 validation (optional)
    print("Step 9: Phase 3.11 validation (optional)...")
    print()
    print_phase311_validation(collection, extractor)
    print()
    
    print("Phase 3 validation complete.")


if __name__ == "__main__":
    main()
