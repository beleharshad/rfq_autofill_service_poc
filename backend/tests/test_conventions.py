"""Unit tests for geometry conventions."""

import pytest
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from geometry_2d import Point2D
from app.geometry.conventions import (
    profile2d_to_3d_point,
    profile2d_to_3d_coords,
    get_reference_axis,
    get_construction_plane_axis,
    extract_axial_from_3d,
    extract_radius_from_3d
)
from OCC.Core.gp import gp_Pnt, gp_Dir, gp_Ax1


class TestConventions:
    """Test geometry conventions."""
    
    def test_profile2d_to_3d_point(self):
        """Test Profile2D to 3D point conversion."""
        # Profile2D: x=radius, y=axial
        profile_point = Point2D(2.5, 3.0)  # radius=2.5, axial=3.0
        
        # Should map to: X=2.5, Y=0, Z=3.0
        point_3d = profile2d_to_3d_point(profile_point)
        
        assert point_3d.X() == pytest.approx(2.5)
        assert point_3d.Y() == pytest.approx(0.0)
        assert point_3d.Z() == pytest.approx(3.0)
    
    def test_profile2d_to_3d_coords(self):
        """Test Profile2D to 3D coordinates conversion."""
        x, y, z = profile2d_to_3d_coords(1.5, 2.0)
        
        assert x == pytest.approx(1.5)  # radius -> X
        assert y == pytest.approx(0.0)  # Y always 0 (XZ plane)
        assert z == pytest.approx(2.0)  # axial -> Z
    
    def test_get_reference_axis_default(self):
        """Test getting default reference axis."""
        axis = get_reference_axis()
        
        # Should be Z-axis through origin
        origin = axis.Location()
        direction = axis.Direction()
        
        assert origin.X() == pytest.approx(0.0)
        assert origin.Y() == pytest.approx(0.0)
        assert origin.Z() == pytest.approx(0.0)
        
        assert direction.X() == pytest.approx(0.0)
        assert direction.Y() == pytest.approx(0.0)
        assert direction.Z() == pytest.approx(1.0)  # Z-axis
    
    def test_get_reference_axis_custom(self):
        """Test getting reference axis with custom point."""
        axis_point = Point2D(0.5, 1.0)  # radius=0.5, axial=1.0
        axis = get_reference_axis(axis_point)
        
        # Should be Z-axis through (0.5, 0, 1.0)
        origin = axis.Location()
        direction = axis.Direction()
        
        assert origin.X() == pytest.approx(0.5)
        assert origin.Y() == pytest.approx(0.0)
        assert origin.Z() == pytest.approx(1.0)
        
        assert direction.X() == pytest.approx(0.0)
        assert direction.Y() == pytest.approx(0.0)
        assert direction.Z() == pytest.approx(1.0)  # Z-axis
    
    def test_get_construction_plane_axis(self):
        """Test getting construction plane axis."""
        center = Point2D(2.0, 3.0)  # radius=2.0, axial=3.0
        axis2 = get_construction_plane_axis(center)
        
        # Should be XZ plane (Y=0) with Y-axis normal
        origin = axis2.Location()
        normal = axis2.Direction()
        
        assert origin.X() == pytest.approx(2.0)
        assert origin.Y() == pytest.approx(0.0)
        assert origin.Z() == pytest.approx(3.0)
        
        assert normal.X() == pytest.approx(0.0)
        assert normal.Y() == pytest.approx(1.0)  # Y-axis normal
        assert normal.Z() == pytest.approx(0.0)
    
    def test_extract_axial_from_3d(self):
        """Test extracting axial coordinate from 3D Z."""
        z_coord = 5.5
        axial = extract_axial_from_3d(z_coord)
        
        assert axial == pytest.approx(5.5)  # Z = axial
    
    def test_extract_radius_from_3d(self):
        """Test extracting radius coordinate from 3D X."""
        x_coord = 3.5
        radius = extract_radius_from_3d(x_coord)
        
        assert radius == pytest.approx(3.5)  # X = radius
    
    def test_convention_consistency(self):
        """Test that conventions are consistent across conversions."""
        # Start with Profile2D point
        profile_point = Point2D(2.0, 4.0)
        
        # Convert to 3D
        point_3d = profile2d_to_3d_point(profile_point)
        
        # Extract back
        radius = extract_radius_from_3d(point_3d.X())
        axial = extract_axial_from_3d(point_3d.Z())
        
        # Should match original
        assert radius == pytest.approx(profile_point.x)
        assert axial == pytest.approx(profile_point.y)
    
    def test_multiple_points_consistency(self):
        """Test consistency across multiple points."""
        test_points = [
            Point2D(0.0, 0.0),
            Point2D(1.0, 2.0),
            Point2D(5.5, 10.25),
            Point2D(0.1, 0.001),
        ]
        
        for profile_point in test_points:
            point_3d = profile2d_to_3d_point(profile_point)
            
            # Verify mapping
            assert point_3d.X() == pytest.approx(profile_point.x)  # radius -> X
            assert point_3d.Y() == pytest.approx(0.0)  # Y always 0
            assert point_3d.Z() == pytest.approx(profile_point.y)  # axial -> Z
            
            # Verify round-trip
            radius = extract_radius_from_3d(point_3d.X())
            axial = extract_axial_from_3d(point_3d.Z())
            
            assert radius == pytest.approx(profile_point.x)
            assert axial == pytest.approx(profile_point.y)





