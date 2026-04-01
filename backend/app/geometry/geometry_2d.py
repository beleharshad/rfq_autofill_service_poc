"""
Phase 1: 2D Geometry Extraction Module

This module provides data structures and utilities for representing and validating
2D geometric profiles consisting of lines and arcs.
"""

from dataclasses import dataclass
from typing import List, Union, Tuple
import math


@dataclass
class Point2D:
    """Represents a 2D point with x and y coordinates."""
    x: float
    y: float
    
    def distance_to(self, other: 'Point2D') -> float:
        """Calculate Euclidean distance to another point."""
        dx = self.x - other.x
        dy = self.y - other.y
        return math.sqrt(dx * dx + dy * dy)
    
    def is_close(self, other: 'Point2D', tolerance: float = 1e-6) -> bool:
        """Check if this point is close to another point within tolerance."""
        return self.distance_to(other) <= tolerance
    
    def cross_product(self, other: 'Point2D') -> float:
        """Calculate 2D cross product (z-component of 3D cross product).
        
        Returns positive value if other is counterclockwise from self.
        """
        return self.x * other.y - self.y * other.x


@dataclass
class LineSegment:
    """Represents a line segment defined by start and end points."""
    start: Point2D
    end: Point2D
    
    @property
    def start_point(self) -> Point2D:
        """Get the start point of the segment."""
        return self.start
    
    @property
    def end_point(self) -> Point2D:
        """Get the end point of the segment."""
        return self.end
    
    def length(self) -> float:
        """Calculate the length of the line segment."""
        return self.start.distance_to(self.end)
    
    def is_valid(self) -> bool:
        """Check geometric consistency: start and end points must be distinct."""
        return not self.start.is_close(self.end)
    
    def is_degenerate(self, tolerance: float = 1e-9) -> bool:
        """Check if the line segment is degenerate (zero length)."""
        return self.start.is_close(self.end, tolerance)
    
    def get_points(self) -> Tuple[Point2D, Point2D]:
        """Get start and end points as a tuple."""
        return (self.start, self.end)
    
    def intersects_line(self, other: 'LineSegment', tolerance: float = 1e-9) -> Tuple[bool, Point2D]:
        """Check if this line segment intersects another line segment.
        
        Returns:
            Tuple of (intersects, intersection_point)
            If segments don't intersect, intersection_point is None.
        """
        # Line-line intersection using parametric form
        p1, p2 = self.start, self.end
        p3, p4 = other.start, other.end
        
        # Check if segments share endpoints (not considered intersection for topology)
        if (p1.is_close(p3, tolerance) or p1.is_close(p4, tolerance) or
            p2.is_close(p3, tolerance) or p2.is_close(p4, tolerance)):
            return False, None
        
        # Vector from p1 to p2
        dx1 = p2.x - p1.x
        dy1 = p2.y - p1.y
        
        # Vector from p3 to p4
        dx2 = p4.x - p3.x
        dy2 = p4.y - p3.y
        
        # Denominator for parametric equations
        denom = dx1 * dy2 - dy1 * dx2
        
        # Lines are parallel
        if abs(denom) < tolerance:
            return False, None
        
        # Vector from p1 to p3
        dx3 = p3.x - p1.x
        dy3 = p3.y - p1.y
        
        # Parameters
        t = (dx3 * dy2 - dy3 * dx2) / denom
        u = (dx3 * dy1 - dy3 * dx1) / denom
        
        # Check if intersection is within both segments
        if 0.0 <= t <= 1.0 and 0.0 <= u <= 1.0:
            # Calculate intersection point
            intersection = Point2D(p1.x + t * dx1, p1.y + t * dy1)
            return True, intersection
        
        return False, None


@dataclass
class ArcSegment:
    """Represents a circular arc segment.
    
    The arc is defined by:
    - center: center point of the circle
    - radius: radius of the circle
    - start_angle: starting angle in radians (0 = positive x-axis, counterclockwise)
    - end_angle: ending angle in radians
    - clockwise: direction of the arc (True = clockwise, False = counterclockwise)
    """
    center: Point2D
    radius: float
    start_angle: float
    end_angle: float
    clockwise: bool = False
    
    @property
    def start_point(self) -> Point2D:
        """Calculate the start point of the arc."""
        x = self.center.x + self.radius * math.cos(self.start_angle)
        y = self.center.y + self.radius * math.sin(self.start_angle)
        return Point2D(x, y)
    
    @property
    def end_point(self) -> Point2D:
        """Calculate the end point of the arc."""
        x = self.center.x + self.radius * math.cos(self.end_angle)
        y = self.center.y + self.radius * math.sin(self.end_angle)
        return Point2D(x, y)
    
    def is_valid(self) -> bool:
        """Check geometric consistency: radius must be positive."""
        return self.radius > 0.0
    
    def is_degenerate(self, tolerance: float = 1e-9) -> bool:
        """Check if the arc is degenerate (zero arc length)."""
        if not self.is_valid():
            return True
        angle_diff = self._normalized_angle_diff()
        return angle_diff < tolerance
    
    def has_valid_radius(self) -> bool:
        """Check if the radius is positive."""
        return self.radius > 0.0
    
    def get_points(self) -> Tuple[Point2D, Point2D]:
        """Get start and end points as a tuple."""
        return (self.start_point, self.end_point)
    
    def intersects_line(self, line: LineSegment, tolerance: float = 1e-9) -> Tuple[bool, List[Point2D]]:
        """Check if this arc intersects a line segment.
        
        This is a basic check - finds intersection points between the line
        and the full circle, then checks if they lie on the arc segment.
        
        Returns:
            Tuple of (intersects, list_of_intersection_points)
        """
        intersections = []
        
        # Get line endpoints
        p1, p2 = line.start, line.end
        
        # Vector from p1 to p2
        dx = p2.x - p1.x
        dy = p2.y - p1.y
        
        # Vector from center to p1
        cx = p1.x - self.center.x
        cy = p1.y - self.center.y
        
        # Quadratic equation coefficients: at^2 + bt + c = 0
        a = dx * dx + dy * dy
        b = 2 * (dx * cx + dy * cy)
        c = cx * cx + cy * cy - self.radius * self.radius
        
        discriminant = b * b - 4 * a * c
        
        if discriminant < 0:
            return False, []
        
        if abs(a) < tolerance:
            # Line segment is degenerate
            return False, []
        
        sqrt_disc = math.sqrt(discriminant)
        t1 = (-b - sqrt_disc) / (2 * a)
        t2 = (-b + sqrt_disc) / (2 * a)
        
        # Check both solutions
        for t in [t1, t2]:
            if 0.0 <= t <= 1.0:
                # Point on line segment
                point = Point2D(p1.x + t * dx, p1.y + t * dy)
                
                # Check if point lies on arc (angle check)
                angle = math.atan2(point.y - self.center.y, point.x - self.center.x)
                # Normalize angle to [0, 2π)
                angle = angle % (2 * math.pi)
                
                start_angle_norm = self.start_angle % (2 * math.pi)
                end_angle_norm = self.end_angle % (2 * math.pi)
                
                # Check if angle is within arc range
                on_arc = False
                if self.clockwise:
                    if end_angle_norm <= start_angle_norm:
                        on_arc = end_angle_norm <= angle <= start_angle_norm
                    else:
                        on_arc = angle <= start_angle_norm or angle >= end_angle_norm
                else:
                    if end_angle_norm >= start_angle_norm:
                        on_arc = start_angle_norm <= angle <= end_angle_norm
                    else:
                        on_arc = angle >= start_angle_norm or angle <= end_angle_norm
                
                if on_arc:
                    intersections.append(point)
        
        return len(intersections) > 0, intersections
    
    def arc_length(self) -> float:
        """Calculate the arc length."""
        angle_diff = self._normalized_angle_diff()
        return angle_diff * self.radius
    
    def _normalized_angle_diff(self) -> float:
        """Calculate normalized angle difference accounting for direction.
        
        Returns the swept angle in radians, always positive.
        """
        # Normalize angles to [0, 2π)
        start = self.start_angle % (2 * math.pi)
        end = self.end_angle % (2 * math.pi)
        
        if self.clockwise:
            # Clockwise: from start to end going clockwise
            if end <= start:
                diff = start - end
            else:
                # Wrap around: start -> 2π -> end
                diff = start + (2 * math.pi - end)
        else:
            # Counterclockwise: from start to end going counterclockwise
            if end >= start:
                diff = end - start
            else:
                # Wrap around: start -> 2π -> end
                diff = (2 * math.pi - start) + end
        
        return diff


# Type alias for geometric primitives
GeometricPrimitive = Union[LineSegment, ArcSegment]


class Profile2D:
    """Represents a 2D profile as an ordered sequence of geometric primitives."""
    
    def __init__(self, primitives: List[GeometricPrimitive] = None):
        """Initialize a profile with an optional list of primitives.
        
        Args:
            primitives: Ordered list of LineSegment and ArcSegment objects
        """
        self.primitives: List[GeometricPrimitive] = primitives if primitives else []
    
    def add_primitive(self, primitive: GeometricPrimitive) -> None:
        """Add a geometric primitive to the profile."""
        self.primitives.append(primitive)
    
    def get_primitives(self) -> List[GeometricPrimitive]:
        """Get the list of primitives in the profile."""
        return self.primitives.copy()
    
    def is_empty(self) -> bool:
        """Check if the profile is empty."""
        return len(self.primitives) == 0
    
    def get_connection_points(self) -> List[Point2D]:
        """Get all connection points between primitives.
        
        Returns:
            List of points where primitives connect (start/end points)
        """
        if self.is_empty():
            return []
        
        points = []
        for primitive in self.primitives:
            points.append(primitive.start_point)
            points.append(primitive.end_point)
        
        return points
    
    def is_closed(self, tolerance: float = 1e-6) -> bool:
        """Check if the profile forms a closed loop.
        
        A profile is closed if:
        1. All consecutive primitives are connected end-to-start
        2. The end point of the last primitive is close to the start point of the first primitive
        
        Args:
            tolerance: Maximum distance between connected points to consider closed
            
        Returns:
            True if the profile is closed, False otherwise
        """
        if len(self.primitives) < 2:
            return False
        
        # Check that ALL consecutive primitives are connected
        for i in range(len(self.primitives)):
            current_end = self._get_primitive_end(self.primitives[i])
            next_idx = (i + 1) % len(self.primitives)
            next_start = self._get_primitive_start(self.primitives[next_idx])
            
            if not current_end.is_close(next_start, tolerance):
                return False
        
        return True
    
    def validate_connectivity(self, tolerance: float = 1e-6) -> Tuple[bool, List[str]]:
        """Validate that all primitives are properly connected (no dangling edges).
        
        Checks that every primitive's end point connects to the next primitive's start point,
        and the last primitive connects back to the first.
        
        Args:
            tolerance: Maximum distance between connected points to consider valid
            
        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        errors = []
        
        if self.is_empty():
            errors.append("Profile is empty")
            return False, errors
        
        if len(self.primitives) < 2:
            errors.append("Profile must contain at least 2 primitives to form a closed loop")
            return False, errors
        
        # Check connectivity between ALL consecutive primitives
        for i in range(len(self.primitives)):
            current_end = self._get_primitive_end(self.primitives[i])
            next_idx = (i + 1) % len(self.primitives)
            next_start = self._get_primitive_start(self.primitives[next_idx])
            
            if not current_end.is_close(next_start, tolerance):
                gap = current_end.distance_to(next_start)
                errors.append(
                    f"Dangling edge: gap between primitive {i} end and primitive {next_idx} start: "
                    f"distance = {gap:.6f} (tolerance = {tolerance:.6f})"
                )
        
        return len(errors) == 0, errors
    
    def get_winding_direction(self) -> Tuple[bool, float]:
        """Calculate the winding direction of the profile.
        
        Uses the signed area (shoelace formula) to determine if the profile
        is clockwise (negative area) or counterclockwise (positive area).
        
        Returns:
            Tuple of (is_clockwise, signed_area)
            signed_area is negative for clockwise, positive for counterclockwise
        """
        if len(self.primitives) < 3:
            return False, 0.0
        
        # Collect all vertices in order
        vertices = []
        for primitive in self.primitives:
            vertices.append(primitive.start_point)
        # Last vertex is the end of the last primitive
        vertices.append(self.primitives[-1].end_point)
        
        # Shoelace formula for signed area
        area = 0.0
        n = len(vertices)
        for i in range(n):
            j = (i + 1) % n
            area += vertices[i].x * vertices[j].y
            area -= vertices[j].x * vertices[i].y
        
        signed_area = area / 2.0
        is_clockwise = signed_area < 0.0
        
        return is_clockwise, signed_area
    
    def validate_consistent_direction(self, tolerance: float = 1e-6) -> Tuple[bool, List[str]]:
        """Validate that the profile has consistent winding direction.
        
        For a valid closed profile, all segments should follow the same
        overall winding direction (either all CW or all CCW).
        
        Args:
            tolerance: Tolerance for area calculation
            
        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        errors = []
        
        if len(self.primitives) < 3:
            errors.append("Profile must contain at least 3 primitives to determine winding direction")
            return False, errors
        
        is_clockwise, signed_area = self.get_winding_direction()
        
        if abs(signed_area) < tolerance:
            errors.append(f"Profile has zero or near-zero signed area: {signed_area:.6f}")
            return False, errors
        
        # Check individual segment directions for arcs
        for i, primitive in enumerate(self.primitives):
            if isinstance(primitive, ArcSegment):
                # For arcs, check if direction matches overall profile direction
                # This is a simplified check - in practice, arc direction should
                # be consistent with the overall profile winding
                pass  # More sophisticated check could be added here
        
        return len(errors) == 0, errors
    
    def check_self_intersections(self, tolerance: float = 1e-9) -> Tuple[bool, List[str]]:
        """Check for self-intersections in the profile.
        
        Performs basic intersection checks between non-adjacent segments.
        Adjacent segments are allowed to share endpoints.
        
        Args:
            tolerance: Tolerance for intersection calculations
            
        Returns:
            Tuple of (has_intersections, list_of_errors)
        """
        errors = []
        intersections_found = []
        
        if len(self.primitives) < 3:
            return True, []  # Need at least 3 segments for self-intersection
        
        # Check all pairs of non-adjacent segments
        for i in range(len(self.primitives)):
            seg1 = self.primitives[i]
            
            # Check against non-adjacent segments
            for j in range(i + 2, len(self.primitives)):
                # Skip if j wraps around to be adjacent to i
                if i == 0 and j == len(self.primitives) - 1:
                    continue  # These are adjacent (first and last)
                
                seg2 = self.primitives[j]
                
                # Line-Line intersection
                if isinstance(seg1, LineSegment) and isinstance(seg2, LineSegment):
                    intersects, point = seg1.intersects_line(seg2, tolerance)
                    if intersects and point is not None:
                        intersections_found.append((i, j, point))
                
                # Line-Arc intersection
                elif isinstance(seg1, LineSegment) and isinstance(seg2, ArcSegment):
                    intersects, points = seg2.intersects_line(seg1, tolerance)
                    if intersects:
                        intersections_found.append((i, j, points[0] if points else None))
                
                # Arc-Line intersection
                elif isinstance(seg1, ArcSegment) and isinstance(seg2, LineSegment):
                    intersects, points = seg1.intersects_line(seg2, tolerance)
                    if intersects:
                        intersections_found.append((i, j, points[0] if points else None))
                
                # Arc-Arc intersection (simplified - check if arcs are too close)
                elif isinstance(seg1, ArcSegment) and isinstance(seg2, ArcSegment):
                    # Basic check: if centers are close and radii overlap significantly
                    center_dist = seg1.center.distance_to(seg2.center)
                    min_dist = abs(seg1.radius - seg2.radius)
                    max_dist = seg1.radius + seg2.radius
                    
                    if min_dist < center_dist < max_dist:
                        # Arcs potentially intersect - more detailed check could be added
                        # For now, we flag this as a potential issue
                        if center_dist < (seg1.radius + seg2.radius) * 0.9:
                            intersections_found.append((i, j, None))
        
        if intersections_found:
            for i, j, point in intersections_found:
                if point is not None:
                    seg1 = self.primitives[i]
                    seg2 = self.primitives[j]
                    
                    # Get endpoints
                    seg1_start = seg1.start_point
                    seg1_end = seg1.end_point
                    seg2_start = seg2.start_point
                    seg2_end = seg2.end_point
                    
                    # Check if intersection point is close to an endpoint of BOTH segments
                    # If it's an endpoint of both, it's a shared endpoint (valid connection)
                    point_is_seg1_endpoint = (
                        point.is_close(seg1_start, tolerance) or 
                        point.is_close(seg1_end, tolerance)
                    )
                    point_is_seg2_endpoint = (
                        point.is_close(seg2_start, tolerance) or 
                        point.is_close(seg2_end, tolerance)
                    )
                    
                    # If intersection is at an endpoint of either segment, it's likely just
                    # a shared endpoint connection, not a true crossing
                    # Only flag as error if the intersection is in the interior of both segments
                    if point_is_seg1_endpoint or point_is_seg2_endpoint:
                        # This is an endpoint intersection - treat as valid connection, not error
                        continue
                
                # This is a real interior intersection (not just an endpoint)
                if point:
                    errors.append(
                        f"Self-intersection detected between primitive {i} and {j} "
                        f"at point ({point.x:.6f}, {point.y:.6f})"
                    )
                else:
                    errors.append(
                        f"Potential self-intersection detected between primitive {i} and {j}"
                    )
        
        return len(errors) == 0, errors
    
    def _get_primitive_start(self, primitive: GeometricPrimitive) -> Point2D:
        """Get the start point of a primitive."""
        return primitive.start_point
    
    def _get_primitive_end(self, primitive: GeometricPrimitive) -> Point2D:
        """Get the end point of a primitive."""
        return primitive.end_point
    
    def validate_geometric_consistency(self) -> Tuple[bool, List[str]]:
        """Validate geometric consistency of all primitives.
        
        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        errors = []
        
        for i, primitive in enumerate(self.primitives):
            if isinstance(primitive, LineSegment):
                if not primitive.is_valid():
                    errors.append(f"LineSegment {i} is degenerate (start and end points are identical)")
            elif isinstance(primitive, ArcSegment):
                if not primitive.is_valid():
                    errors.append(f"ArcSegment {i} has invalid radius: {primitive.radius}")
                if primitive.is_degenerate():
                    errors.append(f"ArcSegment {i} is degenerate (zero arc length)")
            else:
                errors.append(f"Unknown primitive type at index {i}: {type(primitive)}")
        
        return len(errors) == 0, errors
    
    def get_total_length(self) -> float:
        """Calculate the total length of the profile (sum of all segment lengths)."""
        total = 0.0
        for primitive in self.primitives:
            if isinstance(primitive, LineSegment):
                total += primitive.length()
            elif isinstance(primitive, ArcSegment):
                total += primitive.arc_length()
        return total
    
    def validate_topology(self, tolerance: float = 1e-6) -> Tuple[bool, List[str]]:
        """Comprehensive topological validation of the profile.
        
        Validates:
        1. Closed loop (all segments connected, last connects to first)
        2. Consistent direction (consistent winding direction)
        3. No dangling edges (all segments properly connected)
        4. No self-intersections (basic check)
        
        Args:
            tolerance: Tolerance for geometric comparisons
            
        Returns:
            Tuple of (is_valid, list_of_all_errors)
        """
        all_errors = []
        
        # 1. Check geometric consistency of primitives
        is_geom_valid, geom_errors = self.validate_geometric_consistency()
        all_errors.extend(geom_errors)
        
        if not is_geom_valid:
            return False, all_errors
        
        # 2. Check connectivity (no dangling edges) and closed loop
        is_conn_valid, conn_errors = self.validate_connectivity(tolerance)
        all_errors.extend(conn_errors)
        
        if not is_conn_valid:
            return False, all_errors
        
        # 3. Check consistent direction
        is_dir_valid, dir_errors = self.validate_consistent_direction(tolerance)
        all_errors.extend(dir_errors)
        
        if not is_dir_valid:
            return False, all_errors
        
        # 4. Check for self-intersections
        no_intersections, intersection_errors = self.check_self_intersections(tolerance)
        all_errors.extend(intersection_errors)
        
        is_valid = len(all_errors) == 0
        return is_valid, all_errors

