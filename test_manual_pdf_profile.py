"""
Manual PDF -> Profile2D -> 3D Solid Validation

This script manually constructs a baseline Profile2D from dimensions read from
the PDF's Section A-A view, then builds a 3D solid using the revolve pipeline.

Reference PDF: C:\\Users\\beleh\\Downloads\\drgs data\\1\\050ee0077_C.pdf
Section View: A-A (bottom-right section view)

INSTRUCTIONS:
1. Open the PDF and locate Section A-A
2. Read the dimensions listed below
3. Update the DIMENSIONS section with the actual values from the PDF
4. Run this script to generate the 3D solid
"""

from geometry_2d import Profile2D, LineSegment, Point2D
from revolved_solid_builder import RevolvedSolidBuilder
from debug_viewer import view_solid

# ============================================================================
# DIMENSIONS FROM PDF SECTION A-A
# ============================================================================
# Values read from PDF Section A-A (units: inches)
# ============================================================================

# Overall length (axial dimension)
L = 4.25

# Outer diameters (convert to radius)
OD1_diameter = 1.63  # Main OD diameter
OD2_diameter = 0.806  # Right-end OD diameter (nominal/mid; drawing shows 0.814/0.798)
OD1_radius = OD1_diameter / 2.0
OD2_radius = OD2_diameter / 2.0

# Inner diameters (convert to radius)
ID1_diameter = 1.13  # Main bore diameter
ID2_diameter = 0.753  # Right-end bore diameter
ID1_radius = ID1_diameter / 2.0
ID2_radius = ID2_diameter / 2.0

# Shoulder station (transition point between OD1/ID1 and OD2/ID2)
thread_length = 0.98  # Thread region length
yS = 3.27  # Shoulder station = L - thread_length (4.25 - 0.98)

# ============================================================================
# PROFILE CONSTRUCTION
# ============================================================================

def create_baseline_profile() -> Profile2D:
    """
    Create baseline Profile2D from PDF dimensions.
    
    Profile structure (closed loop, counterclockwise):
    1. Start at inner-left bottom: (ID1_radius, 0)
    2. ID region: ID1 from y=0 to yS, then taper to ID2 by y=L
    3. Right face: vertical line from (ID2_radius, L) to (OD2_radius, L)
    4. OD region: OD2 from y=L to yS, then OD1 from yS to y=0
    5. Left face: vertical line from (OD1_radius, 0) back to (ID1_radius, 0)
    
    Coordinate system:
    - X = radius (distance from revolution axis)
    - Y = axial position (0 = left end, L = right end)
    """
    profile = Profile2D()
    
    # Validate dimensions
    if L <= 0 or OD1_radius <= 0 or OD2_radius <= 0:
        raise ValueError("Invalid dimensions: L, OD1, OD2 must be positive")
    if ID1_radius < 0 or ID2_radius < 0:
        raise ValueError("Invalid dimensions: ID radii must be non-negative")
    if ID1_radius >= OD1_radius or ID2_radius >= OD2_radius:
        raise ValueError("Invalid dimensions: ID must be less than OD")
    if yS < 0 or yS > L:
        raise ValueError(f"Invalid shoulder station: yS={yS} must be in [0, L={L}]")
    
    # Build profile as a simple closed loop with ID step structure
    # Profile structure:
    # - OD1 from y=0 to yS, OD2 from yS to L (unchanged)
    # - ID1 from y=0 to yS, then ID step at yS, then ID2 from yS to L
    # - Closed loop suitable for revolve
    
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
        Point2D(ID1_radius, 0.0)  # Back to start
    ))
    
    return profile


def main():
    """Main validation workflow."""
    print("=" * 70)
    print("Manual PDF -> Profile2D -> 3D Solid Validation")
    print("=" * 70)
    print()
    
    # Display dimensions (as read from PDF)
    print("DIMENSIONS FROM PDF SECTION A-A:")
    print(f"  Overall length (L): {L:.4f} inches")
    print(f"  Main OD diameter (OD1): {OD1_diameter:.4f} inches (radius: {OD1_radius:.4f})")
    print(f"  Right-end OD diameter (OD2): {OD2_diameter:.4f} inches (radius: {OD2_radius:.4f})")
    print(f"  Main bore diameter (ID1): {ID1_diameter:.4f} inches (radius: {ID1_radius:.4f})")
    print(f"  Right-end bore diameter (ID2): {ID2_diameter:.4f} inches (radius: {ID2_radius:.4f})")
    print(f"  Shoulder station (yS): {yS:.4f} inches")
    print()
    
    # Step 1: Create profile
    print("Step 1: Creating baseline Profile2D...")
    try:
        profile = create_baseline_profile()
        print(f"  [OK] Profile created with {len(profile.get_primitives())} segments")
    except ValueError as e:
        print(f"  [ERROR] {e}")
        print()
        print("Please update the DIMENSIONS section with values from the PDF.")
        return
    print()
    
    # Step 2: Validate profile topology
    print("Step 2: Validating profile topology...")
    is_valid, errors = profile.validate_topology()
    
    if not is_valid:
        print("  [ERROR] Profile validation FAILED:")
        for error in errors:
            print(f"     - {error}")
        print()
        print("Validation failed. Stopping.")
        return
    else:
        print("  [OK] Profile validation PASSED")
    
    print(f"  - Is closed: {profile.is_closed()}")
    is_clockwise, signed_area = profile.get_winding_direction()
    print(f"  - Winding: {'Clockwise' if is_clockwise else 'Counterclockwise'}")
    print(f"  - Signed area: {signed_area:.6f}")
    print()
    
    # Step 3: Build 3D solid
    print("Step 3: Building 3D solid using revolve pipeline...")
    builder = RevolvedSolidBuilder()
    
    # Set revolution axis (default: Z-axis through origin)
    # Profile must lie entirely on one side of the axis (all X > 0)
    builder.set_axis(Point2D(0.0, 0.0))
    
    success = builder.build_from_profile(profile)
    
    if not success:
        print("  [X] Solid construction FAILED")
        print()
        print("Debugging information:")
        print(f"  - Profile is empty: {profile.is_empty()}")
        print(f"  - Profile is closed: {profile.is_closed()}")
        is_valid, errors = profile.validate_topology()
        print(f"  - Profile is valid: {is_valid}")
        if errors:
            print("  - Errors:")
            for error in errors:
                print(f"      {error}")
        return
    
    print("  [OK] Solid construction SUCCESS")
    print()
    
    # Step 4: Get solid
    print("Step 4: Retrieving solid...")
    solid = builder.get_solid()
    
    if solid is None or solid.IsNull():
        print("  [X] Solid is null")
        return
    
    print("  [OK] Solid retrieved successfully")
    print()
    
    # Step 5: Export STEP
    print("Step 5: Exporting STEP file...")
    step_file = "manual_pdf_profile_validation.step"
    export_success = builder.export_step(step_file)
    
    if export_success:
        print(f"  [OK] STEP file exported: {step_file}")
    else:
        print("  [WARNING] STEP export failed (may not be available)")
    print()
    
    # Step 6: Visualize
    print("Step 6: Opening debug viewer...")
    print("  (Close the viewer window when done)")
    print()
    view_solid(solid)
    print()
    print("Viewer closed.")
    print()
    print("=" * 70)
    print("Validation complete!")
    print("=" * 70)


if __name__ == "__main__":
    main()

