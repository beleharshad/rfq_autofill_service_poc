"""
Phase 2 Verification Test

This script verifies the Phase 2 refactor to ensure revolved solids contain
analytic cylindrical faces (not faceted B-Rep).

Tests:
1. Build baseline profile and revolved solid
2. Print solid topology (ShapeType, face count, face type histogram)
3. Run curvature test (non-zero curvature detection)
4. Run Phase 3 feature extraction (cylinders, planes, holes)
"""

from geometry_2d import Profile2D, LineSegment, Point2D
from revolved_solid_builder import RevolvedSolidBuilder
from feature_extractor import FeatureExtractor
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_FACE, TopAbs_SOLID
from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
from OCC.Core.GeomAbs import GeomAbs_Plane, GeomAbs_Cylinder, GeomAbs_Cone, GeomAbs_Sphere
from OCC.Core.BRepTools import breptools_UVBounds
from OCC.Core.BRepGProp import brepgprop_SurfaceProperties
from OCC.Core.GProp import GProp_GProps
from OCC.Core.GeomLProp import GeomLProp_SLProps
from OCC.Core.TopoDS import TopoDS_Face
import math

# Dimensions from PDF Section A-A (baseline profile)
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


def print_solid_topology(solid):
    """Print solid topology information."""
    print("=" * 70)
    print("SOLID TOPOLOGY")
    print("=" * 70)
    
    # Print ShapeType
    print(f"solid.ShapeType() = {solid.ShapeType()}")
    
    # Count faces
    face_count = 0
    type_counts = {}
    type_names = {
        GeomAbs_Plane: "GeomAbs_Plane",
        GeomAbs_Cylinder: "GeomAbs_Cylinder",
        GeomAbs_Cone: "GeomAbs_Cone",
        GeomAbs_Sphere: "GeomAbs_Sphere",
    }
    
    exp = TopExp_Explorer(solid, TopAbs_FACE)
    faces = []
    while exp.More():
        face_shape = exp.Current()
        face = TopoDS_Face()
        face.TShape(face_shape.TShape())
        face.Location(face_shape.Location())
        face.Orientation(face_shape.Orientation())
        faces.append(face)
        
        adaptor = BRepAdaptor_Surface(face, True)
        surface_type = adaptor.GetType()
        type_name = type_names.get(surface_type, f"GeomAbs_Unknown({surface_type})")
        type_counts[type_name] = type_counts.get(type_name, 0) + 1
        face_count += 1
        exp.Next()
    
    print(f"Face count: {face_count}")
    print()
    
    # Print histogram
    print("Face type histogram:")
    for type_name, count in sorted(type_counts.items()):
        print(f"  {type_name}: {count}")
    print()
    
    return faces


def run_curvature_test(solid):
    """Run curvature test on all faces."""
    print("=" * 70)
    print("CURVATURE TEST")
    print("=" * 70)
    
    tol = 1e-6
    curved_faces = []
    sample_curvature_values = None
    
    exp = TopExp_Explorer(solid, TopAbs_FACE)
    face_idx = 0
    while exp.More():
        face_shape = exp.Current()
        face = TopoDS_Face()
        face.TShape(face_shape.TShape())
        face.Location(face_shape.Location())
        face.Orientation(face_shape.Orientation())
        
        try:
            adaptor = BRepAdaptor_Surface(face, True)
            
            # Get UV bounds and midpoint
            umin, umax, vmin, vmax = breptools_UVBounds(face)
            u_mid = (umin + umax) / 2.0
            v_mid = (vmin + vmax) / 2.0
            
            # Try to get curvature
            try:
                surface_handle = adaptor.Surface().Surface()
                props_curv = GeomLProp_SLProps(surface_handle, u_mid, v_mid, 2, tol)
                is_curv_defined = props_curv.IsCurvatureDefined()
                max_curv = props_curv.MaxCurvature() if is_curv_defined else 0.0
                min_curv = props_curv.MinCurvature() if is_curv_defined else 0.0
                
                # Check if face has non-zero curvature
                has_curvature = is_curv_defined and (abs(max_curv) > tol or abs(min_curv) > tol)
                if has_curvature:
                    curved_faces.append((face_idx, face, max_curv, min_curv))
                    # Store sample curvature values from first curved face
                    if sample_curvature_values is None:
                        sample_curvature_values = (max_curv, min_curv)
            except Exception:
                # Fallback: try adaptor.Cylinder() to detect misclassified cylinders
                if adaptor.GetType() == GeomAbs_Plane:
                    try:
                        gp_cyl = adaptor.Cylinder()
                        radius = gp_cyl.Radius()
                        # For cylinder, curvature = 1/radius
                        curv = 1.0 / radius if radius > tol else 0.0
                        curved_faces.append((face_idx, face, curv, curv))
                        if sample_curvature_values is None:
                            sample_curvature_values = (curv, curv)
                    except:
                        pass
        except Exception as e:
            print(f"  Face {face_idx}: Error in curvature test - {e}")
        
        face_idx += 1
        exp.Next()
    
    print(f"Number of faces with non-zero curvature: {len(curved_faces)}")
    print()
    
    if sample_curvature_values is not None:
        max_curv, min_curv = sample_curvature_values
        print(f"Sample curvature values (from first curved face):")
        print(f"  MaxCurvature: {max_curv:.6f}")
        print(f"  MinCurvature: {min_curv:.6f}")
    else:
        print("No curved faces found - no sample curvature values available")
    print()


def run_feature_extraction(solid):
    """Run Phase 3 feature extraction."""
    print("=" * 70)
    print("PHASE 3 FEATURE EXTRACTION")
    print("=" * 70)
    
    extractor = FeatureExtractor(tolerance=1e-6)
    collection = extractor.extract_features(solid)
    
    print(f"Cylinders (external): {len(collection.cylinders)}")
    print(f"Planar faces: {len(collection.planar_faces)}")
    print(f"Holes (internal): {len(collection.holes)}")
    print()


def main():
    """Main verification workflow."""
    print("=" * 70)
    print("Phase 2 Refactor Verification")
    print("=" * 70)
    print()
    
    # Step 1: Build baseline profile
    print("Step 1: Building baseline profile...")
    profile = create_baseline_profile()
    
    # Validate profile
    is_valid, errors = profile.validate_topology()
    if not is_valid:
        print("  [ERROR] Profile validation failed:")
        for error in errors:
            print(f"    - {error}")
        return
    print("  [OK] Profile validated")
    print()
    
    # Step 2: Build revolved solid with debug validation enabled
    print("Step 2: Building revolved solid (with analytic validation)...")
    builder = RevolvedSolidBuilder(debug_validate_analytic=True)
    
    try:
        success = builder.build_from_profile(profile)
        if not success:
            print("  [ERROR] Failed to build solid")
            return
        print("  [OK] Solid built successfully")
    except RuntimeError as e:
        print(f"  [ERROR] RuntimeError during solid construction:")
        print(f"    {e}")
        return
    print()
    
    solid = builder.get_solid()
    if solid is None or solid.IsNull():
        print("  [ERROR] Solid is null")
        return
    
    # Step 3: Print solid topology
    print("Step 3: Analyzing solid topology...")
    faces = print_solid_topology(solid)
    print()
    
    # Step 4: Run curvature test
    print("Step 4: Running curvature test...")
    run_curvature_test(solid)
    print()
    
    # Step 5: Run Phase 3 feature extraction
    print("Step 5: Running Phase 3 feature extraction...")
    run_feature_extraction(solid)
    print()
    
    print("=" * 70)
    print("Verification complete.")
    print("=" * 70)


if __name__ == "__main__":
    main()





