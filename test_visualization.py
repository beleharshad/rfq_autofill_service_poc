"""
Minimal example: Visualizing a Phase 2 solid using debug viewer.
DEBUG ONLY - For validation purposes.
"""

from geometry_2d import Profile2D, LineSegment, Point2D
from revolved_solid_builder import RevolvedSolidBuilder
from debug_viewer import view_solid

# Step 1: Create a simple rectangular profile
# Profile must be entirely on one side of the revolution axis (default: Z-axis at origin)
# So we'll create a profile that's entirely in the positive X region
profile = Profile2D()
profile.add_primitive(LineSegment(Point2D(5, 0), Point2D(15, 0)))  # Bottom edge
profile.add_primitive(LineSegment(Point2D(15, 0), Point2D(15, 5)))  # Right edge
profile.add_primitive(LineSegment(Point2D(15, 5), Point2D(5, 5)))  # Top edge
profile.add_primitive(LineSegment(Point2D(5, 5), Point2D(5, 0)))  # Left edge

# Step 2: Validate profile topology
is_valid, errors = profile.validate_topology()
if not is_valid:
    print("Profile validation errors:")
    for error in errors:
        print(f"  - {error}")
    exit(1)

print("Profile validation: PASSED")

# Step 3: Build 3D solid using Phase 2
builder = RevolvedSolidBuilder()
print("Building solid...")
success = builder.build_from_profile(profile)

if not success:
    print("Failed to build solid")
    print("Checking profile state...")
    print(f"  Profile is empty: {profile.is_empty()}")
    print(f"  Profile is closed: {profile.is_closed()}")
    is_valid, errors = profile.validate_topology()
    print(f"  Profile is valid: {is_valid}")
    if errors:
        print("  Errors:")
        for error in errors:
            print(f"    - {error}")
    exit(1)

print("Solid construction: SUCCESS")

# Step 4: Get the solid
solid = builder.get_solid()

if solid is None or solid.IsNull():
    print("Solid is null")
    exit(1)

print("Solid retrieved: OK")

# Step 5: Visualize using debug viewer
print("Opening debug viewer...")
view_solid(solid)
print("Viewer closed.")

