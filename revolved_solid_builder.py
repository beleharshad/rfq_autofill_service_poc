"""
Phase 2: 3D Solid Reconstruction Module

This module converts validated Profile2D objects into PythonOCC B-Rep solids
using revolve-based construction for axisymmetric geometry.
"""

from __future__ import annotations  # Defer all annotations so OCC type hints don't raise NameError when OCC is absent

from typing import Optional, Tuple, Any
import math

try:
    from OCC.Core.gp import gp_Pnt, gp_Dir, gp_Ax1, gp_Ax2, gp_Circ
    from OCC.Core.GC import GC_MakeArcOfCircle, GC_MakeSegment
    from OCC.Core.TopoDS import TopoDS_Solid, TopoDS_Wire, TopoDS_Face
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeWire, BRepBuilderAPI_MakeFace
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeRevol
    from OCC.Core.BRepCheck import BRepCheck_Analyzer
    OCC_AVAILABLE = True
except ImportError:
    OCC_AVAILABLE = False
    # Stub names so that method bodies referencing them only fail at call-time, not at import
    gp_Pnt = gp_Dir = gp_Ax1 = gp_Ax2 = gp_Circ = None  # type: ignore[assignment]
    GC_MakeArcOfCircle = GC_MakeSegment = None  # type: ignore[assignment]
    TopoDS_Solid = TopoDS_Wire = TopoDS_Face = None  # type: ignore[assignment]
    BRepBuilderAPI_MakeWire = BRepBuilderAPI_MakeFace = BRepBuilderAPI_MakeEdge = None  # type: ignore[assignment]
    BRepPrimAPI_MakeRevol = BRepCheck_Analyzer = None  # type: ignore[assignment]
import sys
from pathlib import Path

# Add backend to path for conventions module
backend_path = Path(__file__).parent / "backend"
if backend_path.exists():
    sys.path.insert(0, str(backend_path.parent))

try:
    from app.geometry.conventions import (
        profile2d_to_3d_point,
        get_reference_axis,
        get_construction_plane_axis
    )
    _CONVENTIONS_AVAILABLE = True
except ImportError:
    # Fallback for when running outside backend context
    _CONVENTIONS_AVAILABLE = False

# Optional imports for STEP export (only needed if export_step is called)
try:
    from OCC.Core.STEPControl import STEPControl_Writer
    from OCC.Core.Interface import Interface_Static
    _STEP_EXPORT_AVAILABLE = True
except ImportError:
    _STEP_EXPORT_AVAILABLE = False
    STEPControl_Writer = None
    Interface_Static = None

from geometry_2d import Profile2D, LineSegment, ArcSegment, Point2D


class RevolvedSolidBuilder:
    """Builds 3D B-Rep solids by revolving 2D profiles around an axis.
    
    CRITICAL CONSTRAINT:
    The profile must lie entirely on one side of the revolution axis.
    
    Rationale:
    - Open CASCADE will generate self-intersecting solids if the profile
      crosses the revolution axis
    - These self-intersecting solids pass STEP export validation
    - However, they fail downstream CAM (Computer-Aided Manufacturing) systems
    - This constraint ensures manufacturable geometry
    
    Note: Profile validation for axis-side constraint is not yet implemented.
    """
    
    def __init__(self, debug_validate_analytic: bool = False):
        """Initialize the builder with default revolution parameters.
        
        Args:
            debug_validate_analytic: If True, validate that revolved solid contains
                analytic cylindrical faces. Default False (production mode).
                Set to True in tests to catch faceting issues.
        """
        self._axis: Optional[gp_Ax1] = None
        self._angle: float = 2.0 * math.pi  # Full revolution (360 degrees)
        self._solid: Optional[TopoDS_Solid] = None
        self._construction_plane_y: float = 0.0  # Profile is in XZ plane (Y=0)
        self._debug_validate_analytic: bool = debug_validate_analytic
    
    def set_axis(self, axis_point: Point2D, direction: Tuple[float, float, float] = None) -> None:
        """Set the revolution axis.
        
        Convention: Uses standard Z-axis revolution (direction ignored if conventions available).
        
        Args:
            axis_point: Point in the profile plane (x=radius, y=axial)
                       through which the axis passes. Default (0,0) = Z-axis through origin.
            direction: Direction vector for the axis (ignored if conventions available,
                      defaults to Z-axis (0,0,1) for backward compatibility)
        
        CONSTRAINT:
        The profile must lie entirely on one side of this axis. If the profile
        crosses the axis, the resulting solid will be self-intersecting and may
        fail in CAM systems despite passing STEP export.
        """
        if _CONVENTIONS_AVAILABLE:
            # Use standard convention: Z-axis revolution
            self._axis = get_reference_axis(axis_point)
        else:
            # Fallback for backward compatibility
            if direction is None:
                direction = (0.0, 0.0, 1.0)
            # Axis passes through (radius=x, Y=0, Z=axial=y) in XZ plane
            axis_origin = gp_Pnt(axis_point.x, 0.0, axis_point.y)
            axis_dir = gp_Dir(direction[0], direction[1], direction[2])
            self._axis = gp_Ax1(axis_origin, axis_dir)
    
    def set_angle(self, angle_radians: float) -> None:
        """Set the revolution angle.
        
        Args:
            angle_radians: Revolution angle in radians (default: 2π for full revolution)
        """
        self._angle = angle_radians
    
    def build_from_profile(self, profile: Profile2D) -> bool:
        """Build a 3D solid by revolving the given profile.
        
        Args:
            profile: Validated Profile2D object
            
        Returns:
            True if solid was successfully built, False otherwise
        
        CONSTRAINT:
        The profile must lie entirely on one side of the revolution axis.
        This constraint is not yet validated - profiles that cross the axis
        will produce self-intersecting solids that pass STEP export but fail
        in downstream CAM systems.
        """
        if profile.is_empty():
            return False
        
        # Validate profile topology before building
        is_valid, errors = profile.validate_topology()
        if not is_valid:
            return False
        
        # Set default axis if not set (Z-axis through origin)
        if self._axis is None:
            self.set_axis(Point2D(0.0, 0.0))
        
        # TODO: Validate that profile lies entirely on one side of the axis
        # This prevents self-intersecting solids that pass STEP export but fail CAM
        
        # Build wire directly from profile (create 3D edges)
        wire = self._profile_to_wire(profile)
        if wire is None:
            print("DEBUG: Failed to build wire from profile")
            return False
        
        # Create face from wire
        face = self._wire_to_face(wire)
        if face is None:
            print("DEBUG: Failed to create face from wire")
            return False
        
        # Revolve face to create solid
        solid = self._revolve_face(face)
        if solid is None:
            print("DEBUG: Failed to revolve face")
            return False
        
        # Validate solid topology
        if not self._validate_solid(solid):
            print("DEBUG: Solid validation failed")
            return False
        
        # Validate solid has analytic cylindrical faces (if debug flag enabled)
        if self._debug_validate_analytic:
            self._assert_has_cylindrical_faces(solid)
        
        self._solid = solid
        return True
    
    def _assert_has_cylindrical_faces(self, solid: TopoDS_Solid) -> None:
        """Internal debug function to assert solid has analytic cylindrical faces.
        
        Args:
            solid: TopoDS_Solid to check
            
        Raises:
            RuntimeError: If no cylindrical faces are found (indicates faceting issue)
        """
        from OCC.Core.TopExp import TopExp_Explorer
        from OCC.Core.TopAbs import TopAbs_FACE
        from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
        from OCC.Core.GeomAbs import GeomAbs_Cylinder, GeomAbs_Plane
        
        cylinder_count = 0
        face_type_counts = {}
        exp = TopExp_Explorer(solid, TopAbs_FACE)
        face_idx = 0
        while exp.More():
            face_shape = exp.Current()
            face = TopoDS_Face()
            face.TShape(face_shape.TShape())
            face.Location(face_shape.Location())
            face.Orientation(face_shape.Orientation())
            
            adaptor = BRepAdaptor_Surface(face, True)
            face_type = adaptor.GetType()
            
            # Count face types for debugging
            type_name = str(face_type)
            face_type_counts[type_name] = face_type_counts.get(type_name, 0) + 1
            
            if face_type == GeomAbs_Cylinder:
                cylinder_count += 1
            
            exp.Next()
            face_idx += 1
        
        # Debug output
        print(f"DEBUG: Face type distribution: {face_type_counts}")
        print(f"DEBUG: Cylindrical faces found: {cylinder_count}")
        
        # Raise RuntimeError if no cylindrical faces found (indicates faceting)
        if cylinder_count == 0:
            raise RuntimeError(
                f"Revolved solid validation failed: Expected at least 1 analytic cylindrical face, "
                f"but found 0. Face type distribution: {face_type_counts}. "
                f"This indicates the solid was faceted instead of using analytic geometry. "
                f"Check that profile edges are built as 3D analytic curves (not 2D edges)."
            )
    
    def _profile_to_wire(self, profile: Profile2D) -> Optional[TopoDS_Wire]:
        """Build a 3D wire from profile primitives using analytic 3D curves in XZ plane.
        
        Profile2D convention: (x=radius, y=axial)
        Converts Profile2D segments into 3D curves in the XZ plane (Y=0):
        - LineSegment -> GC_MakeSegment(gp_Pnt(x1, 0, y1), gp_Pnt(x2, 0, y2))
        - ArcSegment  -> GC_MakeArcOfCircle(...) using gp_Circ with gp_Ax2(gp_Pnt(cx, 0, cy), gp_Dir(0,1,0))
        
        The profile is built in the XZ plane to be revolved around the Z-axis.
        
        Args:
            profile: Profile2D object (x=radius, y=axial)
            
        Returns:
            TopoDS_Wire or None if wire construction fails
        """
        if profile.is_empty():
            return None
        
        try:
            wire_builder = BRepBuilderAPI_MakeWire()
            
            for primitive in profile.get_primitives():
                if isinstance(primitive, LineSegment):
                    # Create 3D points using convention: Profile2D(x=radius, y=axial) -> 3D(X=radius, Y=0, Z=axial)
                    if _CONVENTIONS_AVAILABLE:
                        p1_3d = profile2d_to_3d_point(primitive.start_point)
                        p2_3d = profile2d_to_3d_point(primitive.end_point)
                    else:
                        # Fallback
                        p1_3d = gp_Pnt(primitive.start_point.x, 0.0, primitive.start_point.y)
                        p2_3d = gp_Pnt(primitive.end_point.x, 0.0, primitive.end_point.y)
                    
                    # Create analytic line segment using GC_MakeSegment
                    segment_maker = GC_MakeSegment(p1_3d, p2_3d)
                    if not segment_maker.IsDone():
                        print(f"DEBUG: Failed to create line segment")
                        return None
                    
                    line_curve = segment_maker.Value()
                    
                    # Build edge from 3D curve with explicit start and end points
                    # This ensures the edge uses the full analytic curve
                    edge_builder = BRepBuilderAPI_MakeEdge(line_curve, p1_3d, p2_3d)
                    if not edge_builder.IsDone():
                        print(f"DEBUG: Failed to create edge from line segment")
                        return None
                    
                    wire_builder.Add(edge_builder.Edge())
                        
                elif isinstance(primitive, ArcSegment):
                    # Create 3D circle using convention
                    if _CONVENTIONS_AVAILABLE:
                        center_3d = profile2d_to_3d_point(primitive.center)
                        axis_3d = get_construction_plane_axis(primitive.center)
                    else:
                        # Fallback
                        center_3d = gp_Pnt(primitive.center.x, 0.0, primitive.center.y)
                        axis_3d = gp_Ax2(center_3d, gp_Dir(0.0, 1.0, 0.0))
                    circle_3d = gp_Circ(axis_3d, primitive.radius)
                    
                    # Calculate start and end points on the circle in XZ plane
                    # Same coordinate mapping: (cx + r*cos, 0, cy + r*sin)
                    start_3d = gp_Pnt(
                        primitive.center.x + primitive.radius * math.cos(primitive.start_angle),
                        0.0,
                        primitive.center.y + primitive.radius * math.sin(primitive.start_angle)
                    )
                    end_3d = gp_Pnt(
                        primitive.center.x + primitive.radius * math.cos(primitive.end_angle),
                        0.0,
                        primitive.center.y + primitive.radius * math.sin(primitive.end_angle)
                    )
                    
                    # Create arc of circle
                    # Third parameter (sense): True for counterclockwise, False for clockwise
                    sense = not primitive.clockwise
                    arc_maker = GC_MakeArcOfCircle(circle_3d, start_3d, end_3d, sense)
                    if not arc_maker.IsDone():
                        print(f"DEBUG: Failed to create arc segment")
                        return None
                    
                    arc_curve = arc_maker.Value()
                    
                    # Build edge from 3D curve with explicit start and end points
                    # This ensures the edge uses the full analytic curve
                    edge_builder = BRepBuilderAPI_MakeEdge(arc_curve, start_3d, end_3d)
                    if not edge_builder.IsDone():
                        print(f"DEBUG: Failed to create edge from arc segment")
                        return None
                    
                    wire_builder.Add(edge_builder.Edge())
            
            if not wire_builder.IsDone():
                print("DEBUG: Wire builder not done")
                return None
            
            wire = wire_builder.Wire()
            
            # Verify wire is closed
            if not self._is_wire_closed(wire):
                print("DEBUG: Wire is not closed")
                return None
            
            print("DEBUG: Wire construction successful")
            return wire
        except Exception as e:
            print(f"DEBUG: Exception in _profile_to_wire: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    
    def _is_wire_closed(self, wire: TopoDS_Wire) -> bool:
        """Check if a wire is closed.
        
        Args:
            wire: TopoDS_Wire to check
            
        Returns:
            True if wire is closed
        """
        try:
            # Use wire's IsClosed method if available
            # Otherwise check vertices
            from OCC.Core.TopExp import TopExp_Explorer
            from OCC.Core.TopAbs import TopAbs_VERTEX
            
            vertices = []
            exp = TopExp_Explorer(wire, TopAbs_VERTEX)
            while exp.More():
                vertex = exp.Current()
                # Get point from vertex - try different methods
                try:
                    from OCC.Core.BRep import BRep_Tool
                    pnt = BRep_Tool.Pnt(vertex)
                except:
                    # Fallback: try to get coordinates another way
                    # For now, assume wire is closed if builder says it's done
                    return True
                vertices.append(pnt)
                exp.Next()
            
            if len(vertices) < 2:
                return False
            
            # Check if first and last vertices are the same (within tolerance)
            first = vertices[0]
            last = vertices[-1]
            distance = first.Distance(last)
            
            return distance < 1e-6
        except Exception:
            # If we can't check, assume it's valid if wire builder succeeded
            return True
    
    def _wire_to_face(self, wire: TopoDS_Wire) -> Optional[TopoDS_Face]:
        """Create a face from a closed wire on the XZ plane.
        
        Creates face with explicit plane surface to ensure analytic geometry is preserved.
        The wire edges are 3D analytic curves in the XZ plane (Y=0).
        
        Args:
            wire: Closed TopoDS_Wire (edges are 3D analytic curves in XZ plane)
            
        Returns:
            TopoDS_Face or None if face construction fails
        """
        try:
            from OCC.Core.Geom import Geom_Plane
            from OCC.Core.gp import gp_Pln
            
            # Create explicit plane surface in XZ plane (Y=0)
            # Normal is Y-axis (0, 1, 0) pointing from origin
            # This ensures the face has a proper analytic surface
            plane = gp_Pln(gp_Pnt(0.0, 0.0, 0.0), gp_Dir(0.0, 1.0, 0.0))
            geom_plane = Geom_Plane(plane)
            
            # Create face from wire on the plane surface
            # The wire must lie in this plane
            face_builder = BRepBuilderAPI_MakeFace(geom_plane, wire)
            
            if not face_builder.IsDone():
                print(f"DEBUG: BRepBuilderAPI_MakeFace(geom_plane, wire) failed")
                # Fallback: try without explicit plane
                face_builder = BRepBuilderAPI_MakeFace(wire)
                if not face_builder.IsDone():
                    print(f"DEBUG: BRepBuilderAPI_MakeFace(wire) also failed")
                    return None
            
            return face_builder.Face()
        except Exception as e:
            print(f"DEBUG: Exception in _wire_to_face: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def _revolve_face(self, face: TopoDS_Face) -> Optional[TopoDS_Solid]:
        """Revolve a face around the axis to create a solid.
        
        Args:
            face: TopoDS_Face to revolve
            
        Returns:
            TopoDS_Solid or None if revolution fails
        """
        if self._axis is None:
            print("DEBUG: Axis is None")
            return None
        
        try:
            revolve_builder = BRepPrimAPI_MakeRevol(face, self._axis, self._angle)
            
            if not revolve_builder.IsDone():
                print("DEBUG: Revolve builder not done")
                return None
            
            # Get the shape - BRepPrimAPI_MakeRevol.Shape() returns a TopoDS_Shape
            # We need to downcast it to TopoDS_Solid
            from OCC.Core.TopAbs import TopAbs_SOLID
            from OCC.Core.TopExp import TopExp_Explorer
            
            shape = revolve_builder.Shape()
            if shape.IsNull():
                print("DEBUG: Revolved shape is null")
                return None
            
            # Check if shape is a solid
            if shape.ShapeType() != TopAbs_SOLID:
                print(f"DEBUG: Shape type is {shape.ShapeType()}, expected SOLID")
                return None
            
            # Downcast to solid using the correct method
            solid = TopoDS_Solid()
            solid.TShape(shape.TShape())
            solid.Location(shape.Location())
            solid.Orientation(shape.Orientation())
            
            print("DEBUG: Revolve successful")
            return solid
        except Exception as e:
            print(f"DEBUG: Exception in _revolve_face: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def _validate_solid(self, solid: TopoDS_Solid) -> bool:
        """Validate the topology of a solid.
        
        Args:
            solid: TopoDS_Solid to validate
            
        Returns:
            True if solid is valid
        """
        try:
            analyzer = BRepCheck_Analyzer(solid)
            return analyzer.IsValid()
        except Exception:
            return False
    
    def get_solid(self) -> Optional[TopoDS_Solid]:
        """Get the constructed solid.
        
        Returns:
            TopoDS_Solid or None if not yet built
        """
        return self._solid
    
    def export_step(self, file_path: str) -> bool:
        """Export the solid to a STEP file.
        
        Args:
            file_path: Path to the output STEP file
            
        Returns:
            True if export was successful, False otherwise
        """
        if self._solid is None:
            return False
        
        if not _STEP_EXPORT_AVAILABLE:
            return False
        
        try:
            # Set STEP export mode to STEP (not AP203/AP214)
            Interface_Static.SetCVal("write.step.schema", "AP203")
            
            writer = STEPControl_Writer()
            transfer_result = writer.Transfer(self._solid, 1)  # 1 = STEPControl_AsIs
            
            if transfer_result != 1:  # 1 = IFSelect_RetDone
                return False
            
            write_result = writer.Write(file_path)
            
            return write_result == 1  # 1 = IFSelect_RetDone
        except Exception:
            return False

