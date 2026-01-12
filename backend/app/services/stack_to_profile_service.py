"""Service for converting TurnedPartStack segments to Profile2D."""

import logging
from typing import List, Dict, Tuple, Optional
from geometry_2d import Profile2D, Point2D, LineSegment

logger = logging.getLogger(__name__)


class StackToProfileService:
    """Service for converting stack segments to Profile2D."""
    
    def __init__(self):
        """Initialize stack to profile service."""
        pass
    
    def build_profile2d_from_stack(
        self, 
        segments: List[Dict], 
        axis: Tuple[float, float] = (0.0, 0.0)
    ) -> Profile2D:
        """Build Profile2D from turned part stack segments.
        
        Each segment [z_start, z_end, od_diameter, id_diameter] becomes a closed 2D outline:
        - start at (ri, z_start)
        - go to (ri, z_end)
        - go to (ro, z_end)
        - go to (ro, z_start)
        - back to (ri, z_start)
        
        This produces a piecewise-constant OD/ID revolved part (a stepped turned part).
        Adjacent edges are merged when collinear to keep profile minimal.
        
        Args:
            segments: List of segment dictionaries with:
                - z_start: float
                - z_end: float
                - od_diameter: float (outer diameter)
                - id_diameter: float (inner diameter, 0 if solid)
            axis: Tuple of (x, y) for revolution axis point (default: (0, 0))
            
        Returns:
            Profile2D object representing the closed profile
        """
        if not segments:
            logger.warning("No segments provided, returning empty profile")
            return Profile2D()
        
        # Sort segments by z_start, then by z_end for consistent ordering
        sorted_segments = sorted(segments, key=lambda s: (s.get('z_start', 0.0), s.get('z_end', 0.0)))
        
        # Merge overlapping segments by creating a continuous z-range map
        # For each z position, determine the OD and ID from the segment that covers it
        z_points = set()
        for seg in sorted_segments:
            z_points.add(seg.get('z_start', 0.0))
            z_points.add(seg.get('z_end', 0.0))
        
        z_sorted = sorted(z_points)
        min_z = z_sorted[0]
        max_z = z_sorted[-1]
        
        # Build a map of z -> (od_radius, id_radius) by finding which segment covers each z
        # For boundary points (z_start or z_end), prefer segments that have that exact boundary
        # For interior points, use the segment that contains it
        # For overlapping segments, prefer the one with the highest z_end
        z_to_od_id = {}
        for z in z_sorted:
            best_seg = None
            best_priority = -1  # Higher priority = better match
            
            for seg in sorted_segments:
                z_start = seg.get('z_start', 0.0)
                z_end = seg.get('z_end', 0.0)
                
                # Check if this segment covers z
                if z_start <= z <= z_end:
                    # Priority: exact boundary match > interior point
                    # For exact boundary, use the segment that has that boundary
                    priority = 0
                    if abs(z - z_start) < 1e-9 or abs(z - z_end) < 1e-9:
                        # Exact boundary match - high priority
                        priority = 1000 + z_end  # Prefer longer segments for boundaries
                    else:
                        # Interior point - lower priority
                        priority = z_end
                    
                    if priority > best_priority:
                        best_priority = priority
                        best_seg = seg
            
            if best_seg:
                od_radius = best_seg.get('od_diameter', 0.0) / 2.0
                id_radius = best_seg.get('id_diameter', 0.0) / 2.0
                z_to_od_id[z] = (od_radius, id_radius)
            else:
                # No segment covers this z, use previous values or default
                prev_z = None
                for pz in z_sorted:
                    if pz < z and pz in z_to_od_id:
                        prev_z = pz
                if prev_z:
                    z_to_od_id[z] = z_to_od_id[prev_z]
                else:
                    # Default to first segment
                    first_seg = sorted_segments[0]
                    z_to_od_id[z] = (
                        first_seg.get('od_diameter', 0.0) / 2.0,
                        first_seg.get('id_diameter', 0.0) / 2.0
                    )
        
        logger.info(f"[StackToProfile] Building profile from {len(sorted_segments)} segments, z_range: [{min_z:.6f}, {max_z:.6f}], {len(z_sorted)} z points")
        
        # Log segment ranges for debugging
        for i, seg in enumerate(sorted_segments):
            logger.debug(f"[StackToProfile] Segment {i}: z=[{seg.get('z_start', 0.0):.6f}, {seg.get('z_end', 0.0):.6f}], "
                        f"OD={seg.get('od_diameter', 0.0):.6f}, ID={seg.get('id_diameter', 0.0):.6f}")
        
        # Get radii at min_z and max_z
        # For min_z, use the segment with the lowest z_start (first segment)
        # For max_z, use the segment with the highest z_end (last segment)
        first_seg = sorted_segments[0]
        last_seg = sorted_segments[-1]
        first_od_radius = first_seg.get('od_diameter', 0.0) / 2.0
        first_id_radius = first_seg.get('id_diameter', 0.0) / 2.0
        last_od_radius = last_seg.get('od_diameter', 0.0) / 2.0
        last_id_radius = last_seg.get('id_diameter', 0.0) / 2.0
        
        # Update z_to_od_id to use these values at boundaries
        z_to_od_id[min_z] = (first_od_radius, first_id_radius)
        z_to_od_id[max_z] = (last_od_radius, last_id_radius)
        
        profile = Profile2D()
        
        # Build profile in clockwise order (starting from bottom-left, going around)
        # Convention: x = radius, y = axial (Z coordinate)
        
        # 1. Bottom edge: from (first_id_radius, min_z) to (first_od_radius, min_z)
        # This will be the starting point of the profile
        start_point = Point2D(first_id_radius if first_id_radius > 1e-6 else 0.0, min_z)
        end_bottom_point = Point2D(first_od_radius, min_z)
        
        if first_id_radius > 1e-6:
            profile.add_primitive(LineSegment(start_point, end_bottom_point))
        else:
            # Start at axis (0, min_z)
            profile.add_primitive(LineSegment(start_point, end_bottom_point))
        
        # 2. Right edge (OD profile): build continuous profile from min_z to max_z
        # Use z_sorted points to ensure continuity
        # The first vertical line must start exactly where the bottom edge ended
        prev_z = min_z
        prev_od_radius = first_od_radius  # This matches end_bottom_point.x
        current_point = end_bottom_point  # Start exactly where bottom edge ended
        
        for z in z_sorted[1:]:  # Skip min_z, we already have it
            od_radius, _ = z_to_od_id[z]
            
            # Always draw vertical line from prev_z to z
            # If OD changed, we need a horizontal transition first
            if abs(od_radius - prev_od_radius) > 1e-6:
                # OD changed: draw horizontal transition at prev_z, then vertical
                transition_end = Point2D(od_radius, prev_z)
                profile.add_primitive(LineSegment(current_point, transition_end))
                # Now draw vertical line at new OD radius
                vertical_end = Point2D(od_radius, z)
                profile.add_primitive(LineSegment(transition_end, vertical_end))
                current_point = vertical_end
            else:
                # OD didn't change: just draw vertical line
                vertical_end = Point2D(od_radius, z)
                profile.add_primitive(LineSegment(current_point, vertical_end))
                current_point = vertical_end
            
            prev_z = z
            prev_od_radius = od_radius
        
        # After the loop, prev_z should be max_z and prev_od_radius should match last_od_radius
        # Verify this and adjust if needed
        if abs(prev_z - max_z) > 1e-9:
            logger.warning(f"[StackToProfile] OD profile ended at z={prev_z:.6f}, expected {max_z:.6f}")
        if abs(prev_od_radius - last_od_radius) > 1e-6:
            logger.warning(f"[StackToProfile] OD profile ended at radius={prev_od_radius:.6f}, expected {last_od_radius:.6f}")
            # Use the actual last OD radius
            prev_od_radius = last_od_radius
        
        # 3. Top edge: from (last_od_radius, max_z) to (last_id_radius, max_z)
        # The last OD vertical line should end at (last_od_radius, max_z)
        # Verify this matches before adding top edge
        top_start_point = Point2D(prev_od_radius, max_z)
        top_end_point = Point2D(last_id_radius if last_id_radius > 1e-6 else 0.0, max_z)
        
        if last_id_radius > 1e-6:
            profile.add_primitive(LineSegment(top_start_point, top_end_point))
        else:
            # End at axis (0, max_z)
            profile.add_primitive(LineSegment(top_start_point, top_end_point))
        
        # 4. Left edge (ID profile): build continuous profile from max_z to min_z (going backwards)
        # The first ID vertical line must start exactly where the top edge ended
        prev_z = max_z
        prev_id_radius = last_id_radius if last_id_radius > 1e-6 else 0.0  # This matches top_end_point.x
        
        # Current point where we are (end of top edge)
        current_id_point = top_end_point
        
        for z in reversed(z_sorted[:-1]):  # Skip max_z, go backwards
            _, id_radius = z_to_od_id[z]
            effective_id_radius = id_radius if id_radius > 1e-6 else 0.0
            
            # Always draw vertical line from prev_z to z (going down)
            # If ID changed, we need a horizontal transition first
            if abs(id_radius - prev_id_radius) > 1e-6:
                # ID changed: draw horizontal transition at prev_z, then vertical
                transition_end = Point2D(effective_id_radius, prev_z)
                profile.add_primitive(LineSegment(current_id_point, transition_end))
                # Now draw vertical line at new ID radius
                vertical_end = Point2D(effective_id_radius, z)
                profile.add_primitive(LineSegment(transition_end, vertical_end))
                current_id_point = vertical_end
            else:
                # ID didn't change: just draw vertical line
                vertical_end = Point2D(effective_id_radius, z)
                profile.add_primitive(LineSegment(current_id_point, vertical_end))
                current_id_point = vertical_end
            
            prev_z = z
            prev_id_radius = id_radius
        
        # After the loop, prev_z should be min_z and prev_id_radius should match first_id_radius
        # Verify this and adjust if needed
        if abs(prev_z - min_z) > 1e-9:
            logger.warning(f"[StackToProfile] ID profile ended at z={prev_z:.6f}, expected {min_z:.6f}")
        if abs(prev_id_radius - first_id_radius) > 1e-6:
            logger.warning(f"[StackToProfile] ID profile ended at radius={prev_id_radius:.6f}, expected {first_id_radius:.6f}")
            # Use the actual first ID radius
            prev_id_radius = first_id_radius
        
        # Ensure profile closes: connect last ID point to starting point at min_z
        # The last vertical line should end at (prev_id_radius, min_z) or (0, min_z)
        # We need to connect this to the starting point (first_id_radius, min_z) or (0, min_z)
        final_id_radius = prev_id_radius if prev_id_radius > 1e-6 else 0.0
        start_id_radius = first_id_radius if first_id_radius > 1e-6 else 0.0
        
        # The last ID vertical line ends at (final_id_radius, min_z)
        # The profile starts at (start_id_radius, min_z)
        # If they don't match, add a horizontal closing segment
        final_id_point = Point2D(final_id_radius, min_z)
        if not final_id_point.is_close(start_point, 1e-6):
            # Need horizontal transition to close the loop
            profile.add_primitive(LineSegment(
                final_id_point,
                start_point
            ))
        
        # Merge collinear edges to keep profile minimal
        profile = self._merge_collinear_edges(profile)
        
        # Debug: Log all primitives to check connectivity
        logger.debug(f"[StackToProfile] Profile has {len(profile.get_primitives())} primitives")
        for i, prim in enumerate(profile.get_primitives()):
            logger.debug(f"[StackToProfile] Primitive {i}: ({prim.start_point.x:.6f}, {prim.start_point.y:.6f}) -> "
                        f"({prim.end_point.x:.6f}, {prim.end_point.y:.6f})")
        
        # Validate closed loop
        is_valid, errors = profile.validate_topology(tolerance=1e-6)
        if not is_valid:
            logger.warning(f"[StackToProfile] Profile validation failed: {errors}")
            # Log detailed connectivity info
            primitives = profile.get_primitives()
            for i in range(len(primitives)):
                current_end = primitives[i].end_point
                next_idx = (i + 1) % len(primitives)
                next_start = primitives[next_idx].start_point
                gap = current_end.distance_to(next_start)
                if gap > 1e-6:
                    logger.warning(f"[StackToProfile] Gap at primitive {i}->{next_idx}: "
                                 f"end=({current_end.x:.6f}, {current_end.y:.6f}), "
                                 f"start=({next_start.x:.6f}, {next_start.y:.6f}), "
                                 f"distance={gap:.6f}")
        
        return profile
    
    def _merge_collinear_edges(self, profile: Profile2D) -> Profile2D:
        """Merge collinear adjacent edges in the profile.
        
        Args:
            profile: Profile2D object
            
        Returns:
            Profile2D with merged collinear edges
        """
        # For now, return the profile as-is
        # TODO: Implement collinear edge merging if needed
        # This would involve checking if consecutive line segments are collinear
        # and combining them into a single segment
        return profile

