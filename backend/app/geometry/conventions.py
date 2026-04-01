"""
Axis Convention Module

Standardizes coordinate system conventions across the pipeline.

CONVENTION:
- Profile2D coordinates: x=radius, y=axial (Z in 3D)
- Construction plane: XZ plane (Y=0)
- Revolution axis: Z-axis
- Mapping: Profile2D(x, y) -> 3D(X=x, Y=0, Z=y)

This ensures consistency across:
- RevolvedSolidBuilder (profile to solid)
- FeatureExtractor (reference axis)
- TurnedPartStack (z_range extents)
"""

from typing import Tuple
from OCC.Core.gp import gp_Pnt, gp_Dir, gp_Ax1, gp_Ax2
from app.geometry.geometry_2d import Point2D


def profile2d_to_3d_point(profile_point: Point2D) -> gp_Pnt:
    """Convert Profile2D point to 3D point.
    
    Convention:
    - Profile2D: x=radius, y=axial
    - 3D: X=radius, Y=0, Z=axial (XZ plane)
    
    Args:
        profile_point: Point2D with (x=radius, y=axial)
        
    Returns:
        gp_Pnt in 3D space (X=radius, Y=0, Z=axial)
    """
    return gp_Pnt(profile_point.x, 0.0, profile_point.y)


def profile2d_to_3d_coords(radius: float, axial: float) -> Tuple[float, float, float]:
    """Convert Profile2D coordinates to 3D coordinates.
    
    Convention:
    - Profile2D: (radius, axial)
    - 3D: (X=radius, Y=0, Z=axial)
    
    Args:
        radius: Radius coordinate (X in 3D)
        axial: Axial coordinate (Z in 3D)
        
    Returns:
        Tuple of (X, Y, Z) coordinates
    """
    return (radius, 0.0, axial)


def get_reference_axis(axis_point_2d: Point2D = None) -> gp_Ax1:
    """Get the standard reference axis for revolution.
    
    Convention:
    - Revolution axis: Z-axis
    - Passes through point (axis_x, 0, axis_y) in 3D
    - Direction: (0, 0, 1) - positive Z
    
    Args:
        axis_point_2d: Optional Point2D for axis position (x=radius, y=axial).
                      If None, uses origin (0, 0).
        
    Returns:
        gp_Ax1 representing the Z-axis revolution axis
    """
    if axis_point_2d is None:
        axis_point_2d = Point2D(0.0, 0.0)
    
    # Convert 2D axis point to 3D
    axis_origin_3d = profile2d_to_3d_point(axis_point_2d)
    
    # Z-axis direction
    axis_dir = gp_Dir(0.0, 0.0, 1.0)
    
    return gp_Ax1(axis_origin_3d, axis_dir)


def get_construction_plane_axis(center_2d: Point2D) -> gp_Ax2:
    """Get axis system for construction plane (XZ plane).
    
    Convention:
    - Construction plane: XZ plane (Y=0)
    - Normal: Y-axis (0, 1, 0)
    - Center: (radius, 0, axial) in 3D
    
    Args:
        center_2d: Point2D for center position (x=radius, y=axial)
        
    Returns:
        gp_Ax2 for XZ plane construction
    """
    center_3d = profile2d_to_3d_point(center_2d)
    normal = gp_Dir(0.0, 1.0, 0.0)  # Y-axis normal to XZ plane
    return gp_Ax2(center_3d, normal)


def extract_axial_from_3d(z_coord: float) -> float:
    """Extract axial coordinate from 3D Z coordinate.
    
    Convention:
    - 3D Z coordinate = Profile2D y (axial)
    
    Args:
        z_coord: Z coordinate in 3D space
        
    Returns:
        Axial coordinate (same value)
    """
    return z_coord


def extract_radius_from_3d(x_coord: float) -> float:
    """Extract radius coordinate from 3D X coordinate.
    
    Convention:
    - 3D X coordinate = Profile2D x (radius)
    
    Args:
        x_coord: X coordinate in 3D space
        
    Returns:
        Radius coordinate (same value)
    """
    return x_coord








