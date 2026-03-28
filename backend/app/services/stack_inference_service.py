"""Service for inferring TurnedPartStack from detected turned view."""

import json
import re
import cv2
import numpy as np
from pathlib import Path
from typing import List, Dict, Tuple, Optional
from datetime import datetime, timezone
import sys

# Add project root to path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from feature_extractor import TurnedPartStack, TurnedPartSegment
from app.storage.file_storage import FileStorage
from app.services.job_service import JobService
from app.models.job import JobStatus
from app.models.part_summary import PartSummary
from app.services.dimension_detector import DimensionDetector
from app.services.rfq_dimension_classifier import RFQDimensionClassifier
import logging

logger = logging.getLogger(__name__)


class StackInferenceService:
    """Service for inferring TurnedPartStack from detected turned view."""
    
    def __init__(self):
        """Initialize stack inference service."""
        self.file_storage = FileStorage()
        self.job_service = JobService()
        self.axial_bin_size = 3  # Pixels per bin for axial sampling (reduced from 5 for finer resolution)
        self.min_segment_length = 3  # Minimum pixels for a segment (reduced from 10 to capture short features)
        self.dimension_detector = DimensionDetector()
        self.dimension_classifier = RFQDimensionClassifier()
        
        # Sanity gate thresholds (wide enough for DPI-based scaling with
        # various drawing scales; post-calibration refines further).
        self.min_total_length_inches = 0.1
        self.max_total_length_inches = 20.0
        self.min_max_od_inches = 0.1
        self.max_max_od_inches = 20.0
    
    def normalize_turned_part_stack_with_confidence(
        self, 
        stack: TurnedPartStack, 
        original_segments: List[Dict]
    ) -> Tuple[TurnedPartStack, Dict[int, Dict]]:
        """Normalize and clean a TurnedPartStack, tracking confidence metadata.
        
        Args:
            stack: Input TurnedPartStack to normalize
            original_segments: List of original segment dictionaries with metadata
            
        Returns:
            Tuple of (normalized_stack, normalization_metadata)
            normalization_metadata: Dict mapping normalized segment index to metadata
        """
        if not stack.segments:
            return stack, {}
        
        # Sort segments by z_start
        sorted_segments = sorted(stack.segments, key=lambda s: s.z_start)
        
        # Track original indices
        original_indices = list(range(len(sorted_segments)))
        
        # Calculate total Z range for relative tolerances
        min_z = min(s.z_start for s in sorted_segments)
        max_z = max(s.z_end for s in sorted_segments)
        total_z_range = max_z - min_z
        
        if total_z_range <= 0:
            return stack, {i: {"original_index": i} for i in range(len(sorted_segments))}
        
        normalization_metadata = {}
        boundary_snapped_flags = [False] * len(sorted_segments)
        
        # Step 1: Snap adjacent segment boundaries
        eps_z = max(0.02, 0.005 * total_z_range)  # 0.02 in or 0.5% of range
        for i in range(len(sorted_segments) - 1):
            gap = sorted_segments[i + 1].z_start - sorted_segments[i].z_end
            if abs(gap) < eps_z:
                # Snap boundaries together
                boundary_snapped_flags[i] = True
                boundary_snapped_flags[i + 1] = True
                mid_point = (sorted_segments[i].z_end + sorted_segments[i + 1].z_start) / 2.0
                sorted_segments[i] = TurnedPartSegment(
                    z_start=sorted_segments[i].z_start,
                    z_end=mid_point,
                    od_diameter=sorted_segments[i].od_diameter,
                    id_diameter=sorted_segments[i].id_diameter,
                    wall_thickness=(sorted_segments[i].od_diameter - sorted_segments[i].id_diameter) / 2.0
                )
                sorted_segments[i + 1] = TurnedPartSegment(
                    z_start=mid_point,
                    z_end=sorted_segments[i + 1].z_end,
                    od_diameter=sorted_segments[i + 1].od_diameter,
                    id_diameter=sorted_segments[i + 1].id_diameter,
                    wall_thickness=(sorted_segments[i + 1].od_diameter - sorted_segments[i + 1].id_diameter) / 2.0
                )
        
        # Step 2: Normalize Z (shift so min z_start == 0.0)
        if min_z != 0.0:
            for i in range(len(sorted_segments)):
                sorted_segments[i] = TurnedPartSegment(
                    z_start=sorted_segments[i].z_start - min_z,
                    z_end=sorted_segments[i].z_end - min_z,
                    od_diameter=sorted_segments[i].od_diameter,
                    id_diameter=sorted_segments[i].id_diameter,
                    wall_thickness=sorted_segments[i].wall_thickness
                )
        
        # Step 3 & 4: Merge short and similar segments
        # Reduced minimum length to preserve short features like threads
        min_len = max(0.005, 0.002 * total_z_range)  # Reduced from 0.05/1% to 0.005/0.2% of total length
        merged_segments = []
        merged_metadata = []
        i = 0
        while i < len(sorted_segments):
            current = sorted_segments[i]
            segment_len = current.z_end - current.z_start
            current_indices = [original_indices[i]]
            current_id_clamped = False
            if original_indices[i] < len(original_segments):
                seg_meta = original_segments[original_indices[i]].get("_metadata", {})
                current_id_clamped = seg_meta.get("id_auto_clamped", False) if isinstance(seg_meta, dict) else False
            
            # Check for short segment merge
            if segment_len < min_len and i < len(sorted_segments) - 1:
                # Merge with next segment
                next_seg = sorted_segments[i + 1]
                current_indices.append(original_indices[i + 1])
                if original_indices[i + 1] < len(original_segments):
                    seg_meta = original_segments[original_indices[i + 1]].get("_metadata", {})
                    next_id_clamped = seg_meta.get("id_auto_clamped", False) if isinstance(seg_meta, dict) else False
                    current_id_clamped = current_id_clamped or next_id_clamped
                
                # Use weighted average for diameters (by length)
                total_len = (current.z_end - current.z_start) + (next_seg.z_end - next_seg.z_start)
                if total_len > 0:
                    w1 = (current.z_end - current.z_start) / total_len
                    w2 = (next_seg.z_end - next_seg.z_start) / total_len
                    merged_od = current.od_diameter * w1 + next_seg.od_diameter * w2
                    merged_id = current.id_diameter * w1 + next_seg.id_diameter * w2
                else:
                    merged_od = current.od_diameter
                    merged_id = current.id_diameter
                
                merged = TurnedPartSegment(
                    z_start=current.z_start,
                    z_end=next_seg.z_end,
                    od_diameter=merged_od,
                    id_diameter=merged_id,
                    wall_thickness=(merged_od - merged_id) / 2.0
                )
                merged_segments.append(merged)
                merged_metadata.append({
                    "merged_from_indices": current_indices,
                    "boundary_snapped": boundary_snapped_flags[i] or boundary_snapped_flags[i + 1],
                    "id_auto_clamped": current_id_clamped,
                    "original_index": current_indices[0]  # Primary index
                })
                i += 2  # Skip next segment since we merged it
                continue  # ← skip "No merge" block below (already appended)
            else:
                # Check for similar segment merge
                if merged_segments and i < len(sorted_segments):
                    prev = merged_segments[-1]
                    curr = sorted_segments[i]
                    
                    # Calculate tolerance based on diameter
                    avg_od = (prev.od_diameter + curr.od_diameter) / 2.0
                    eps_d = max(0.02, 0.01 * avg_od) if avg_od > 0 else 0.02  # 0.02 in or 1% of diameter
                    
                    od_diff = abs(prev.od_diameter - curr.od_diameter)
                    id_diff = abs(prev.id_diameter - curr.id_diameter)
                    
                    if od_diff < eps_d and id_diff < eps_d:
                        # Merge segments
                        prev_indices = merged_metadata[-1].get("merged_from_indices", [merged_metadata[-1].get("original_index", len(merged_segments) - 1)])
                        current_indices = prev_indices + [original_indices[i]]
                        if original_indices[i] < len(original_segments):
                            seg_meta = original_segments[original_indices[i]].get("_metadata", {})
                            next_id_clamped = seg_meta.get("id_auto_clamped", False) if isinstance(seg_meta, dict) else False
                            current_id_clamped = current_id_clamped or next_id_clamped
                        
                        # Use weighted average for diameters
                        total_len = (prev.z_end - prev.z_start) + (curr.z_end - curr.z_start)
                        if total_len > 0:
                            w1 = (prev.z_end - prev.z_start) / total_len
                            w2 = (curr.z_end - curr.z_start) / total_len
                            merged_od = prev.od_diameter * w1 + curr.od_diameter * w2
                            merged_id = prev.id_diameter * w1 + curr.id_diameter * w2
                        else:
                            merged_od = prev.od_diameter
                            merged_id = prev.id_diameter
                        
                        # Build flags for merged segment
                        merged_flags = []
                        if hasattr(prev, 'flags'):
                            merged_flags.extend(prev.flags)
                        if hasattr(curr, 'flags'):
                            merged_flags.extend(curr.flags)
                        if "auto_merged" not in merged_flags:
                            merged_flags.append("auto_merged")
                        
                        merged = TurnedPartSegment(
                            z_start=prev.z_start,
                            z_end=curr.z_end,
                            od_diameter=merged_od,
                            id_diameter=merged_id,
                            wall_thickness=(merged_od - merged_id) / 2.0,
                            flags=merged_flags
                        )
                        merged_segments[-1] = merged
                        merged_metadata[-1] = {
                            "merged_from_indices": current_indices,
                            "boundary_snapped": merged_metadata[-1].get("boundary_snapped", False) or boundary_snapped_flags[i],
                            "id_auto_clamped": current_id_clamped,
                            "original_index": prev_indices[0]
                        }
                        i += 1
                        continue
            
            # No merge, add as-is
            merged_segments.append(current)
            merged_metadata.append({
                "original_index": original_indices[i],
                "boundary_snapped": boundary_snapped_flags[i],
                "id_auto_clamped": current_id_clamped
            })
            i += 1
        
        # Create normalized stack
        normalized_stack = TurnedPartStack(segments=merged_segments)
        
        # Build metadata dict
        metadata_dict = {i: merged_metadata[i] for i in range(len(merged_metadata))}
        
        return normalized_stack, metadata_dict
    
    def normalize_turned_part_stack(self, stack: TurnedPartStack) -> TurnedPartStack:
        """Normalize and clean a TurnedPartStack.
        
        Performs:
        1. Snap adjacent segment boundaries (remove small gaps)
        2. Normalize Z (shift so min z_start == 0.0)
        3. Merge short segments
        4. Merge similar adjacent segments
        5. Recompute wall thickness after merging
        
        Args:
            stack: Input TurnedPartStack to normalize
            
        Returns:
            Normalized TurnedPartStack
        """
        if not stack.segments:
            return stack
        
        # Sort segments by z_start
        sorted_segments = sorted(stack.segments, key=lambda s: s.z_start)
        
        # Calculate total Z range for relative tolerances
        min_z = min(s.z_start for s in sorted_segments)
        max_z = max(s.z_end for s in sorted_segments)
        total_z_range = max_z - min_z
        
        if total_z_range <= 0:
            return stack
        
        # Step 1: Snap adjacent segment boundaries
        eps_z = max(0.02, 0.005 * total_z_range)  # 0.02 in or 0.5% of range
        for i in range(len(sorted_segments) - 1):
            gap = sorted_segments[i + 1].z_start - sorted_segments[i].z_end
            if abs(gap) < eps_z:
                # Snap boundaries together
                mid_point = (sorted_segments[i].z_end + sorted_segments[i + 1].z_start) / 2.0
                sorted_segments[i] = TurnedPartSegment(
                    z_start=sorted_segments[i].z_start,
                    z_end=mid_point,
                    od_diameter=sorted_segments[i].od_diameter,
                    id_diameter=sorted_segments[i].id_diameter,
                    wall_thickness=(sorted_segments[i].od_diameter - sorted_segments[i].id_diameter) / 2.0
                )
                sorted_segments[i + 1] = TurnedPartSegment(
                    z_start=mid_point,
                    z_end=sorted_segments[i + 1].z_end,
                    od_diameter=sorted_segments[i + 1].od_diameter,
                    id_diameter=sorted_segments[i + 1].id_diameter,
                    wall_thickness=(sorted_segments[i + 1].od_diameter - sorted_segments[i + 1].id_diameter) / 2.0
                )
        
        # Step 2: Normalize Z (shift so min z_start == 0.0)
        if min_z != 0.0:
            for i in range(len(sorted_segments)):
                sorted_segments[i] = TurnedPartSegment(
                    z_start=sorted_segments[i].z_start - min_z,
                    z_end=sorted_segments[i].z_end - min_z,
                    od_diameter=sorted_segments[i].od_diameter,
                    id_diameter=sorted_segments[i].id_diameter,
                    wall_thickness=sorted_segments[i].wall_thickness
                )
        
        # Step 3: Merge short segments
        # Reduced minimum length to preserve short features like threads
        min_len = max(0.005, 0.002 * total_z_range)  # Reduced from 0.05/1% to 0.005/0.2% of total length
        merged_segments = []
        i = 0
        while i < len(sorted_segments):
            current = sorted_segments[i]
            segment_len = current.z_end - current.z_start
            
            if segment_len < min_len and i < len(sorted_segments) - 1:
                # Merge with next segment
                next_seg = sorted_segments[i + 1]
                # Use weighted average for diameters (by length)
                total_len = (current.z_end - current.z_start) + (next_seg.z_end - next_seg.z_start)
                if total_len > 0:
                    w1 = (current.z_end - current.z_start) / total_len
                    w2 = (next_seg.z_end - next_seg.z_start) / total_len
                    merged_od = current.od_diameter * w1 + next_seg.od_diameter * w2
                    merged_id = current.id_diameter * w1 + next_seg.id_diameter * w2
                else:
                    merged_od = current.od_diameter
                    merged_id = current.id_diameter
                
                merged = TurnedPartSegment(
                    z_start=current.z_start,
                    z_end=next_seg.z_end,
                    od_diameter=merged_od,
                    id_diameter=merged_id,
                    wall_thickness=(merged_od - merged_id) / 2.0
                )
                merged_segments.append(merged)
                i += 2  # Skip next segment since we merged it
            else:
                merged_segments.append(current)
                i += 1
        
        # Step 4: Merge similar adjacent segments
        if len(merged_segments) > 1:
            final_segments = [merged_segments[0]]
            for i in range(1, len(merged_segments)):
                prev = final_segments[-1]
                curr = merged_segments[i]
                
                # Calculate tolerance based on diameter
                avg_od = (prev.od_diameter + curr.od_diameter) / 2.0
                eps_d = max(0.02, 0.01 * avg_od) if avg_od > 0 else 0.02  # 0.02 in or 1% of diameter
                
                od_diff = abs(prev.od_diameter - curr.od_diameter)
                id_diff = abs(prev.id_diameter - curr.id_diameter)
                
                if od_diff < eps_d and id_diff < eps_d:
                    # Merge segments
                    # Use weighted average for diameters
                    total_len = (prev.z_end - prev.z_start) + (curr.z_end - curr.z_start)
                    if total_len > 0:
                        w1 = (prev.z_end - prev.z_start) / total_len
                        w2 = (curr.z_end - curr.z_start) / total_len
                        merged_od = prev.od_diameter * w1 + curr.od_diameter * w2
                        merged_id = prev.id_diameter * w1 + curr.id_diameter * w2
                    else:
                        merged_od = prev.od_diameter
                        merged_id = prev.id_diameter
                    
                    # Build flags for merged segment
                    merged_flags = []
                    if hasattr(prev, 'flags'):
                        merged_flags.extend(prev.flags)
                    if hasattr(curr, 'flags'):
                        merged_flags.extend(curr.flags)
                    if "auto_merged" not in merged_flags:
                        merged_flags.append("auto_merged")
                    
                    merged = TurnedPartSegment(
                        z_start=prev.z_start,
                        z_end=curr.z_end,
                        od_diameter=merged_od,
                        id_diameter=merged_id,
                        wall_thickness=(merged_od - merged_id) / 2.0,
                        flags=merged_flags
                    )
                    final_segments[-1] = merged  # Replace last segment
                else:
                    final_segments.append(curr)
        else:
            final_segments = merged_segments
        
        # Create normalized stack
        normalized_stack = TurnedPartStack(segments=final_segments)
        
        return normalized_stack
    
    def normalize_axis(self, crop: np.ndarray, axis_info: Dict) -> Tuple[np.ndarray, Dict]:
        """Normalize image so axis is vertical and centered.
        
        Args:
            crop: Cropped view image
            axis_info: Axis information with 'line' [x1, y1, x2, y2] and 'angle'
            
        Returns:
            Tuple of (normalized_image, transform_info)
        """
        x1, y1, x2, y2 = axis_info["line"]
        angle = axis_info["angle"]
        
        h, w = crop.shape[:2]
        
        # Calculate rotation needed to make axis vertical
        # If angle is close to 90 or -90, axis is already vertical
        # Otherwise, rotate to make it vertical
        rotation_angle = 90.0 - angle if abs(angle) < 90 else -(90.0 + angle)
        
        # Get rotation matrix
        center = (w // 2, h // 2)
        M = cv2.getRotationMatrix2D(center, rotation_angle, 1.0)
        
        # Rotate image
        normalized = cv2.warpAffine(crop, M, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))
        
        # Calculate axis position after rotation
        # Transform axis endpoints
        axis_points = np.array([[x1, y1, 1], [x2, y2, 1]], dtype=np.float32)
        axis_points_transformed = (M @ axis_points.T).T
        
        # Find average x position (axis should be vertical, so x should be constant)
        axis_x = np.mean(axis_points_transformed[:, 0])
        
        # Translate to center axis at x = w/2
        translation_x = w / 2 - axis_x
        M_translation = np.float32([[1, 0, translation_x], [0, 1, 0]])
        normalized = cv2.warpAffine(normalized, M_translation, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))
        
        transform_info = {
            "rotation_angle": float(rotation_angle),
            "translation_x": float(translation_x),
            "axis_x": float(w / 2)
        }
        
        return normalized, transform_info
    
    def extract_silhouette_edges(self, normalized: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Extract silhouette edges (OD and ID) from normalized image, including horizontal step lines.
        
        Args:
            normalized: Normalized image with vertical axis
            
        Returns:
            Tuple of (od_edges, id_edges) as binary images
        """
        # Convert to grayscale
        if len(normalized.shape) == 3:
            gray = cv2.cvtColor(normalized, cv2.COLOR_BGR2GRAY)
        else:
            gray = normalized
        
        # Apply adaptive threshold to get binary image (more sensitive)
        # Use multiple thresholds to catch faint lines
        _, binary1 = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY_INV)
        _, binary2 = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY_INV)  # Lower threshold for faint lines
        _, binary3 = cv2.threshold(gray, 220, 255, cv2.THRESH_BINARY_INV)  # Higher threshold for strong lines
        binary = cv2.bitwise_or(cv2.bitwise_or(binary1, binary2), binary3)
        
        # Enhanced Canny edge detection with multiple thresholds to catch all edges
        edges1 = cv2.Canny(gray, 20, 60)   # Very sensitive for faint lines and threads
        edges2 = cv2.Canny(gray, 30, 100)  # Lower threshold for faint lines
        edges3 = cv2.Canny(gray, 50, 150)  # Standard threshold
        edges4 = cv2.Canny(gray, 80, 200)  # Higher threshold for strong edges
        canny_edges = cv2.bitwise_or(cv2.bitwise_or(edges1, edges2), cv2.bitwise_or(edges3, edges4))
        
        # Combine threshold and Canny results
        binary = cv2.bitwise_or(binary, canny_edges)
        
        # Detect horizontal lines (step boundaries) using HoughLinesP with lower thresholds
        h, w = gray.shape
        # Use lower thresholds to catch short lines (threads, small steps)
        horizontal_lines = cv2.HoughLinesP(
            binary,
            rho=1,
            theta=np.pi/180,
            threshold=max(5, w // 40),  # Lower threshold (was w // 20)
            minLineLength=w // 8,  # Reduced from w // 4 to catch shorter lines
            maxLineGap=5  # Reduced from 10 to connect closer line segments
        )
        
        # Create horizontal line mask
        horizontal_mask = np.zeros_like(binary)
        if horizontal_lines is not None:
            for line in horizontal_lines:
                x1, y1, x2, y2 = line[0]
                # Check if line is roughly horizontal (within 5 degrees)
                angle = np.abs(np.arctan2(y2 - y1, x2 - x1) * 180 / np.pi)
                if angle < 5 or angle > 175:
                    cv2.line(horizontal_mask, (x1, y1), (x2, y2), 255, 2)
        
        # Apply morphological operations to clean up (use smaller kernel to preserve small features)
        kernel_small = np.ones((2, 2), np.uint8)  # Smaller kernel to preserve threads and small features
        kernel_medium = np.ones((3, 3), np.uint8)
        # Use smaller kernel first to preserve detail
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel_small)
        binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel_small)
        
        # Find contours with RETR_TREE to capture nested contours (holes, threads)
        contours, hierarchy = cv2.findContours(binary, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        
        # Also detect curves/arcs using Douglas-Peucker approximation with lower epsilon
        # This helps capture threads and fillets
        refined_contours = []
        for contour in contours:
            # Use lower epsilon to preserve curve details (threads, arcs)
            epsilon = 0.5  # Lower value preserves more detail (was typically 1-2)
            approx = cv2.approxPolyDP(contour, epsilon, False)
            refined_contours.append(approx)
        contours = refined_contours
        
        if not contours:
            return np.zeros_like(binary), np.zeros_like(binary)
        
        # Find largest contour (likely OD)
        largest_contour = max(contours, key=cv2.contourArea)
        
        # Create OD edge image - include all significant contours
        od_edges = np.zeros_like(binary)
        cv2.drawContours(od_edges, [largest_contour], -1, 255, 1)
        
        # Add other contours that might be OD segments (lower threshold to catch small features)
        for contour in contours:
            area_ratio = cv2.contourArea(contour) / cv2.contourArea(largest_contour)
            if area_ratio > 0.05:  # Reduced from 0.3 to 0.05 to include smaller features (threads, steps)
                cv2.drawContours(od_edges, [contour], -1, 255, 1)
        
        # Add horizontal lines to OD edges (these are step boundaries)
        od_edges = cv2.bitwise_or(od_edges, horizontal_mask)
        
        # For ID, look for inner contours or holes
        id_edges = np.zeros_like(binary)
        axis_x = w // 2
        
        # Find contours that are likely inner (bore) - lower threshold to catch small holes
        # These should be on the left side of the axis (assuming axisymmetric view)
        for contour in contours:
            # Reduced threshold from 0.1 to 0.01 to capture very small holes
            if cv2.contourArea(contour) < cv2.contourArea(largest_contour) * 0.01:
                # Small contour, might be inner (hole, thread, or small bore)
                M = cv2.moments(contour)
                if M["m00"] != 0:
                    cx = int(M["m10"] / M["m00"])
                    # If contour is on the left side of axis, it might be ID
                    # Also check if it's very close to axis (likely a small hole)
                    if cx < axis_x or abs(cx - axis_x) < w * 0.1:  # Within 10% of width from axis
                        cv2.drawContours(id_edges, [contour], -1, 255, 1)
        
        return od_edges, id_edges
    
    def estimate_od_envelope(self, od_edges: np.ndarray, axis_x: float) -> Dict[str, np.ndarray]:
        """Estimate OD envelope as max distance from axis per axial position (binned).
        
        Args:
            od_edges: Binary image with OD edges
            axis_x: X coordinate of axis
            
        Returns:
            Dictionary with 'axial_positions' and 'od_radii' arrays
        """
        h, w = od_edges.shape
        
        # Bin by axial position (y coordinate)
        num_bins = h // self.axial_bin_size
        if num_bins == 0:
            num_bins = 1
        
        axial_positions = []
        od_radii = []
        
        for bin_idx in range(num_bins):
            y_start = bin_idx * self.axial_bin_size
            y_end = min((bin_idx + 1) * self.axial_bin_size, h)
            y_center = (y_start + y_end) / 2.0
            
            # Find max distance from axis in this bin
            bin_region = od_edges[y_start:y_end, :]
            edge_pixels = np.where(bin_region > 0)
            
            if len(edge_pixels[0]) > 0:
                # Get x coordinates of edge pixels
                x_coords = edge_pixels[1]
                # Distance from axis
                distances = np.abs(x_coords - axis_x)
                max_distance = np.max(distances) if len(distances) > 0 else 0.0
            else:
                max_distance = 0.0
            
            axial_positions.append(y_center)
            od_radii.append(max_distance)
        
        return {
            "axial_positions": np.array(axial_positions),
            "od_radii": np.array(od_radii)
        }
    
    def _save_od_radius_plot(self, od_data: Dict[str, np.ndarray], debug_dir: Path):
        """Save a plot of OD radius vs axial position for debugging."""
        try:
            import matplotlib
            matplotlib.use('Agg')  # Non-interactive backend
            import matplotlib.pyplot as plt
            
            axial = od_data["axial_positions"]
            radii = od_data["od_radii"]
            
            plt.figure(figsize=(10, 6))
            plt.plot(axial, radii, 'b-', linewidth=2, label='OD Radius')
            plt.xlabel('Axial Position (pixels)')
            plt.ylabel('OD Radius (pixels)')
            plt.title('OD Radius vs Axial Position')
            plt.grid(True, alpha=0.3)
            plt.legend()
            plt.tight_layout()
            plt.savefig(str(debug_dir / "od_radius_curve.png"), dpi=150)
            plt.close()
        except ImportError:
            # matplotlib not available, skip plotting
            pass
    
    def detect_change_points(self, values: np.ndarray, min_change: float = 0.005) -> List[int]:
        """Detect change points in a 1D signal using simple threshold-based method.
        
        Args:
            values: 1D array of values
            min_change: Minimum relative change to consider a change point (default 0.5% to catch minute changes)
            
        Returns:
            List of change point indices
        """
        if len(values) < 2:
            return []
        
        change_points = [0]  # Start is always a change point
        
        # Use a sliding window to smooth out noise and detect trends
        window_size = min(5, len(values) // 10)  # Adaptive window size
        if window_size < 1:
            window_size = 1
        
        # Compute smoothed values
        smoothed = []
        for i in range(len(values)):
            start = max(0, i - window_size // 2)
            end = min(len(values), i + window_size // 2 + 1)
            window_vals = values[start:end]
            if len(window_vals) > 0:
                smoothed.append(np.median(window_vals))
            else:
                smoothed.append(values[i])
        smoothed = np.array(smoothed)
        
        # Compute relative changes on smoothed data
        for i in range(1, len(smoothed)):
            if smoothed[i] > 0 and smoothed[i-1] > 0:
                relative_change = abs(smoothed[i] - smoothed[i-1]) / max(smoothed[i], smoothed[i-1])
                if relative_change > min_change:
                    change_points.append(i)
        
        change_points.append(len(values) - 1)  # End is always a change point
        
        # Remove duplicates and sort
        change_points = sorted(list(set(change_points)))
        
        # Ensure we have at least 2 segments if the part has any length
        if len(change_points) == 2 and len(values) > 10:
            # If only start and end, try to find at least one intermediate point
            # by looking for the point with maximum deviation from the mean
            mean_val = np.mean(smoothed)
            deviations = np.abs(smoothed - mean_val)
            max_dev_idx = np.argmax(deviations)
            # Only add if it's not too close to start or end
            if max_dev_idx > len(values) * 0.1 and max_dev_idx < len(values) * 0.9:
                change_points.insert(1, max_dev_idx)
                change_points = sorted(list(set(change_points)))
        
        return change_points
    
    def estimate_id_envelope(self, id_edges: np.ndarray, axis_x: float) -> Tuple[Dict[str, np.ndarray], bool]:
        """Estimate ID envelope from inner silhouette or infer if bore visible.
        
        Args:
            id_edges: Binary image with ID edges
            axis_x: X coordinate of axis
            
        Returns:
            Tuple of (id_data, has_bore)
            id_data: Dictionary with 'axial_positions' and 'id_radii' arrays
            has_bore: True if bore is visible, False otherwise
        """
        h, w = id_edges.shape
        
        # Check if there are any ID edges
        if np.sum(id_edges) == 0:
            # No ID edges detected, assume solid (ID = 0)
            num_bins = h // self.axial_bin_size
            if num_bins == 0:
                num_bins = 1
            
            axial_positions = []
            id_radii = []
            
            for bin_idx in range(num_bins):
                y_start = bin_idx * self.axial_bin_size
                y_end = min((bin_idx + 1) * self.axial_bin_size, h)
                y_center = (y_start + y_end) / 2.0
                axial_positions.append(y_center)
                id_radii.append(0.0)
            
            return {
                "axial_positions": np.array(axial_positions),
                "id_radii": np.array(id_radii)
            }, False
        
        # ID edges detected, estimate ID envelope
        num_bins = h // self.axial_bin_size
        if num_bins == 0:
            num_bins = 1
        
        axial_positions = []
        id_radii = []
        
        for bin_idx in range(num_bins):
            y_start = bin_idx * self.axial_bin_size
            y_end = min((bin_idx + 1) * self.axial_bin_size, h)
            y_center = (y_start + y_end) / 2.0
            
            # Find min distance from axis in this bin (ID is the inner edge, closest to axis)
            bin_region = id_edges[y_start:y_end, :]
            edge_pixels = np.where(bin_region > 0)
            
            if len(edge_pixels[0]) > 0:
                # Get x coordinates of edge pixels
                x_coords = edge_pixels[1]
                # Distance from axis (consider both sides, but ID should be closer to axis)
                distances = np.abs(x_coords - axis_x)
                # ID is the MINIMUM distance from axis (inner edge)
                min_distance = np.min(distances) if len(distances) > 0 else 0.0
            else:
                min_distance = 0.0
            
            axial_positions.append(y_center)
            id_radii.append(min_distance)
        
        return {
            "axial_positions": np.array(axial_positions),
            "id_radii": np.array(id_radii)
        }, True
    
    def infer_stack_from_view(
        self,
        job_id: str,
        best_view: Dict,
        mode: str = "auto_detect"
    ) -> Dict:
        """Infer TurnedPartStack from chosen turned view.
        
        Args:
            job_id: Job identifier
            best_view: Best view from auto-detection (with axis_info, bbox_pixels, etc.)
            
        Returns:
            Dictionary with segments, totals, confidence scores, warnings
        """
        outputs_path = self.file_storage.get_outputs_path(job_id)
        pages_dir = outputs_path / "pdf_pages"
        debug_dir = outputs_path / "pdf_inference_debug"
        debug_dir.mkdir(parents=True, exist_ok=True)

        # Initialize variables to ensure they are always defined
        total_length = 0.0
        overall_confidence = 0.5
        normalized_segments = []
        normalized_stack = None
        validation_errors = []
        validation_warnings = []
        warnings = []
        
        # Load page image
        page_num = best_view["page"]
        page_file = pages_dir / f"page_{page_num}.png"
        
        if not page_file.exists():
            raise FileNotFoundError(f"Page image not found: {page_file}")
        
        page_img = cv2.imread(str(page_file))
        if page_img is None:
            raise ValueError(f"Failed to load page image: {page_file}")
        
        # Crop view
        bbox_pixels = best_view["bbox_pixels"]
        crop = page_img[bbox_pixels[1]:bbox_pixels[1]+bbox_pixels[3], bbox_pixels[0]:bbox_pixels[0]+bbox_pixels[2]]
        
        if crop.size == 0:
            raise ValueError("Cropped view is empty")
        
        # Normalize axis
        axis_info = best_view.get("axis_info")
        if axis_info is None:
            raise ValueError("Axis info not found in best_view")
        
        normalized, transform_info = self.normalize_axis(crop, axis_info)
        axis_x = transform_info["axis_x"]
        
        # Save normalized image
        cv2.imwrite(str(debug_dir / "normalized.png"), normalized)
        
        # Extract silhouette edges
        od_edges, id_edges = self.extract_silhouette_edges(normalized)
        
        # Save edge images
        cv2.imwrite(str(debug_dir / "od_edges.png"), od_edges)
        cv2.imwrite(str(debug_dir / "id_edges.png"), id_edges)
        
        # Estimate OD envelope
        od_data = self.estimate_od_envelope(od_edges, axis_x)
        
        # Estimate ID envelope
        id_data, has_bore = self.estimate_id_envelope(id_edges, axis_x)
        
        # Save OD radius curve visualization for debugging
        self._save_od_radius_plot(od_data, debug_dir)
        
        # Detect change points in OD radius curve
        od_change_points = self.detect_change_points(od_data["od_radii"])
        
        # Debug: Log change points detected
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"Detected {len(od_change_points)} change points: {od_change_points}")
        logger.info(f"OD radii range: {np.min(od_data['od_radii']):.2f} - {np.max(od_data['od_radii']):.2f} pixels")
        logger.info(f"OD radii std dev: {np.std(od_data['od_radii']):.2f} pixels")
        
        # Create segments from change points
        segments = []
        warnings = []
        segment_confidences = []
        
        # Convert pixel coordinates to physical units
        # Use scale calibration from detected dimensions
        h, w = normalized.shape[:2]
        
        # Step 1: Detect dimensions in the view using OCR
        # Note: page_img is already loaded earlier (line 759), reuse it
        bbox_pixels = best_view["bbox_pixels"]
        raw_dimensions = self.dimension_detector.detect_dimensions_in_view(
            page_img, 
            (bbox_pixels[0], bbox_pixels[1], bbox_pixels[2], bbox_pixels[3])
        )
        
        # Step 1.5: Filter dimensions using RFQ classifier to remove tolerance ranges,
        # metric brackets, and small features
        filtered_dimensions = self._filter_dimensions_with_classifier(raw_dimensions)
        
        logger.info(f"Detected {len(raw_dimensions)} raw dimensions, {len(filtered_dimensions)} after filtering")
        
        # Step 1.8: If EasyOCR on the view crop found few dimensions,
        # supplement with full-page Tesseract OCR from PDFSpecExtractor.
        if len(filtered_dimensions) < 3:
            try:
                from app.services.pdf_spec_extractor import PDFSpecExtractor
                pdf_path = self.file_storage.get_inputs_path(job_id) / "source.pdf"
                if pdf_path.exists():
                    extractor = PDFSpecExtractor()
                    full_page_dims = extractor.extract_all_dimension_candidates(str(pdf_path))
                    supplement_count = 0
                    for cand in full_page_dims:
                        val = cand.get("value_in") or cand.get("value")
                        if val and float(val) > 0.05 and cand.get("unit", "in") == "in":
                            if not cand.get("is_tolerance"):
                                filtered_dimensions.append({
                                    "value": float(val),
                                    "unit": "in",
                                    "text": cand.get("text", ""),
                                    "confidence": min(float(cand.get("confidence", 0.5)), 0.7),
                                    "source": "tesseract_full_page",
                                })
                                supplement_count += 1
                    if supplement_count:
                        logger.info(
                            f"[SCALE] Supplemented with {supplement_count} "
                            f"Tesseract full-page dimensions "
                            f"(total now {len(filtered_dimensions)})"
                        )
            except Exception as e:
                logger.warning(f"[SCALE] Full-page OCR supplement failed: {e}")

        # Step 2: Find anchor dimension for scale calibration (using filtered dimensions)
        anchor_dim = self.dimension_detector.find_anchor_dimension(
            filtered_dimensions,
            normalized,
            od_data,
        )
        
        # Step 3: Calculate pixel-to-inch conversion
        RENDER_DPI = 300
        base_inch_per_pixel = 1.0 / RENDER_DPI
        scale_report = {}

        if anchor_dim:
            pixel_to_inch = anchor_dim['inch_per_pixel']
            scale_report = {
                'method': 'anchor_dimension',
                'confidence': min(anchor_dim.get('confidence', 0.9), 0.92),
                'anchor_name': anchor_dim['name'],
                'anchor_value_inches': anchor_dim['value'],
                'anchor_pixel_length': anchor_dim['pixel_length'],
                'inch_per_pixel': pixel_to_inch,
                'implied_drawing_scale': anchor_dim.get('implied_drawing_scale', 1.0),
                'render_dpi': RENDER_DPI,
            }
            logger.info(
                f"Scale from anchor: {anchor_dim['name']}={anchor_dim['value']}\" "
                f"({anchor_dim['pixel_length']:.0f} px) → {pixel_to_inch:.6f} in/px  "
                f"drawing_scale≈{anchor_dim.get('implied_drawing_scale', '?')}:1"
            )
        else:
            # DPI-based fallback: we know 1 pixel = 1/300 page-inch.
            # Estimate drawing scale from OCR dimensions + geometry.
            drawing_scale = self._estimate_drawing_scale(
                filtered_dimensions, od_data, base_inch_per_pixel,
                page_img=page_img,
            )
            pixel_to_inch = base_inch_per_pixel / drawing_scale
            scale_report = {
                'method': 'dpi_based',
                'confidence': 0.70 if drawing_scale != 1.0 else 0.55,
                'render_dpi': RENDER_DPI,
                'detected_drawing_scale': drawing_scale,
                'inch_per_pixel': pixel_to_inch,
            }
            logger.info(
                f"DPI-based scale: drawing_scale={drawing_scale}:1 → "
                f"pixel_to_inch={pixel_to_inch:.6f}"
            )
        
        # Save scale report
        scale_report_file = debug_dir / "scale_report.json"
        with open(scale_report_file, 'w') as f:
            json.dump(scale_report, f, indent=2)
        
        for i in range(len(od_change_points) - 1):
            start_idx = od_change_points[i]
            end_idx = od_change_points[i + 1]
            
            if end_idx - start_idx < 1:
                continue  # Skip only truly empty segments (reduced from 2 to 1)
            
            # Get axial range
            z_start_pixels = od_data["axial_positions"][start_idx]
            z_end_pixels = od_data["axial_positions"][end_idx]
            
            # Get OD and ID for this segment (use median or mean)
            segment_od_radii = od_data["od_radii"][start_idx:end_idx+1]
            segment_id_radii = id_data["id_radii"][start_idx:end_idx+1]
            
            # Filter out zero radii
            segment_od_radii = segment_od_radii[segment_od_radii > 0]
            segment_id_radii = segment_id_radii[segment_id_radii > 0]
            
            if len(segment_od_radii) == 0:
                continue  # Skip segments with no OD data
            
            # Use median for robustness
            od_radius_pixels = np.median(segment_od_radii)
            id_radius_pixels = np.median(segment_id_radii) if len(segment_id_radii) > 0 else 0.0
            
            # CRITICAL: Ensure ID <= OD (swap if reversed)
            if id_radius_pixels > od_radius_pixels:
                # They're swapped - fix it
                od_radius_pixels, id_radius_pixels = id_radius_pixels, od_radius_pixels
                warnings.append(f"Segment {len(segments)}: OD/ID were swapped (ID > OD), corrected")
            
            # Convert to physical units
            z_start = z_start_pixels * pixel_to_inch
            z_end = z_end_pixels * pixel_to_inch
            od_diameter = od_radius_pixels * 2.0 * pixel_to_inch
            id_diameter_raw = id_radius_pixels * 2.0 * pixel_to_inch
            
            # Final validation: ensure ID <= OD after conversion
            if id_diameter_raw > od_diameter:
                id_diameter_raw = 0.0  # If still wrong, assume solid (no bore)
                warnings.append(f"Segment {len(segments)}: ID > OD after conversion, set ID=0 (solid part)")
            
            # Apply ID inference improvements
            id_diameter = id_diameter_raw
            id_assumed_solid = False
            id_was_inferred = not has_bore or len(segment_id_radii) == 0
            id_auto_clamped = False
            
            # Rule 1: If inferred ID < id_min_threshold → treat as solid
            # Lowered threshold to capture smaller holes (threads, small bores)
            if id_diameter > 0:
                id_min_threshold = max(0.01, 0.01 * od_diameter)  # Reduced from 0.125/5% to 0.01/1% of OD
                if id_diameter < id_min_threshold:
                    id_diameter = 0.0
                    id_assumed_solid = True
                    id_auto_clamped = True
                    warnings.append(f"Segment {len(segments)}: ID ({id_diameter_raw:.4f} in) < threshold ({id_min_threshold:.4f} in), treated as solid (id_assumed_solid)")
            
            # Rule 2: Prevent near-zero IDs (clamp to 0 if very small)
            # Lowered threshold to capture very small holes (e.g., thread reliefs)
            if id_diameter > 0 and id_diameter < 0.001:  # Reduced from 0.01 to 0.001 inches
                id_diameter = 0.0
                id_assumed_solid = True
                id_auto_clamped = True
                warnings.append(f"Segment {len(segments)}: ID ({id_diameter:.4f} in) too small, treated as solid")
            
            # Calculate wall thickness
            wall_thickness = (od_diameter - id_diameter) / 2.0 if od_diameter > 0 else 0.0
            segment_length = z_end - z_start
            
            # Compute base detector confidence
            od_std = np.std(segment_od_radii) if len(segment_od_radii) > 1 else 0.0
            od_mean = np.mean(segment_od_radii)
            od_consistency = 1.0 - min(1.0, od_std / (od_mean + 1e-6))  # Lower std = higher confidence
            
            # Base confidence from detector
            if id_was_inferred:
                id_detector_conf = 0.5 if id_diameter == 0 else 0.6
            elif id_assumed_solid:
                id_detector_conf = 0.7
            else:
                id_detector_conf = 0.85
            
            base_confidence = (od_consistency * 0.7 + id_detector_conf * 0.3)
            
            # Apply penalties
            penalties = []
            
            # Penalty 1: ID was auto-clamped to 0
            if id_auto_clamped:
                penalties.append(("id_auto_clamped", 0.15))  # 15% penalty
            
            # Penalty 2: Wall thickness < 0.05 in
            if wall_thickness > 0 and wall_thickness < 0.05:
                thin_wall_penalty = 0.10 * (1.0 - wall_thickness / 0.05)  # Up to 10% penalty
                penalties.append(("thin_wall", thin_wall_penalty))
            
            # Penalty 3: Segment length < 2% of total Z (will be calculated after all segments)
            # This will be applied during normalization
            
            # Calculate final confidence with penalties
            total_penalty = sum(penalty for _, penalty in penalties)
            segment_conf = max(0.0, min(1.0, base_confidence - total_penalty))
            
            segment_confidences.append(segment_conf)
            
            if id_assumed_solid:
                # Already added warning above
                pass
            elif not has_bore and id_diameter == 0:
                warnings.append(f"Segment {len(segments)}: ID inferred as 0 (no bore detected)")
            
            segments.append({
                "z_start": float(z_start),
                "z_end": float(z_end),
                "od_diameter": float(od_diameter),
                "id_diameter": float(id_diameter),
                "confidence": float(segment_conf),
                "_metadata": {
                    "id_auto_clamped": id_auto_clamped,
                    "wall_thickness": float(wall_thickness),
                    "segment_length": float(segment_length),
                    "base_confidence": float(base_confidence),
                    "penalties": [(name, float(penalty)) for name, penalty in penalties]
                }
            })
        
        if not segments:
            raise ValueError("No segments inferred from view")
        
        # Calculate total Z range for relative penalties
        total_z_range = max(seg["z_end"] for seg in segments) - min(seg["z_start"] for seg in segments)
        
        # Apply segment length penalty to initial segments
        for seg_data in segments:
            segment_length = seg_data["z_end"] - seg_data["z_start"]
            rel_length = segment_length / total_z_range if total_z_range > 0 else 1.0
            
            # Penalty 3: Segment length < 2% of total Z
            if rel_length < 0.02:
                length_penalty = 0.10 * (1.0 - rel_length / 0.02)  # Up to 10% penalty
                seg_data["_metadata"]["penalties"].append(("short_segment", length_penalty))
                # Recalculate confidence with new penalty
                total_penalty = sum(penalty for _, penalty in seg_data["_metadata"]["penalties"])
                seg_data["confidence"] = max(0.0, min(1.0, seg_data["_metadata"]["base_confidence"] - total_penalty))
        
        # Build TurnedPartStack
        stack_segments = []
        for seg_data in segments:
            # Calculate wall thickness
            od = seg_data["od_diameter"]
            id_val = seg_data.get("id_diameter", 0.0)
            wall_thickness = (od - id_val) / 2.0 if od > 0 else 0.0
            
            # Build initial flags
            initial_flags = []
            if seg_data.get("_metadata", {}).get("id_auto_clamped", False):
                initial_flags.append("id_assumed_solid")
            if wall_thickness > 0 and wall_thickness < 0.05:
                initial_flags.append("thin_wall")
            segment_length = seg_data["z_end"] - seg_data["z_start"]
            rel_length = segment_length / total_z_range if total_z_range > 0 else 1.0
            if rel_length < 0.02:
                initial_flags.append("short_segment")
            if seg_data.get("confidence", 1.0) < 0.6:
                initial_flags.append("low_confidence")
            
            segment = TurnedPartSegment(
                z_start=seg_data["z_start"],
                z_end=seg_data["z_end"],
                od_diameter=od,
                id_diameter=id_val,
                wall_thickness=wall_thickness,  # Required field for dataclass
                flags=initial_flags
            )
            stack_segments.append(segment)
        
        stack = TurnedPartStack(segments=stack_segments)
        
        # Normalize and clean the stack (snap boundaries, merge segments, etc.)
        # Pass segments metadata for confidence tracking
        normalized_stack, normalization_metadata = self.normalize_turned_part_stack_with_confidence(
            stack, segments
        )
        
        # Update segments list to match normalized stack with recalculated confidence
        normalized_segments = []
        for i, seg in enumerate(normalized_stack.segments):
            norm_meta = normalization_metadata.get(i, {})
            
            # Get base confidence from original segments (weighted average if merged)
            if norm_meta.get("merged_from_indices"):
                # Merged segment: average confidence of merged segments
                merged_indices = norm_meta["merged_from_indices"]
                base_conf = np.mean([segments[idx]["_metadata"]["base_confidence"] for idx in merged_indices if idx < len(segments)])
            else:
                # Single segment (may have been snapped)
                orig_idx = norm_meta.get("original_index", i)
                if orig_idx < len(segments):
                    base_conf = segments[orig_idx]["_metadata"]["base_confidence"]
                else:
                    base_conf = np.mean([s["_metadata"]["base_confidence"] for s in segments]) if segments else 0.9
            
            # Apply penalties
            penalties = []
            
            # Penalty: Merged from multiple segments
            if norm_meta.get("merged_from_indices") and len(norm_meta["merged_from_indices"]) > 1:
                merge_count = len(norm_meta["merged_from_indices"])
                merge_penalty = min(0.20, 0.05 * (merge_count - 1))  # 5% per additional segment, max 20%
                penalties.append(("merged", merge_penalty))
            
            # Penalty: Z boundaries were snapped
            if norm_meta.get("boundary_snapped"):
                penalties.append(("boundary_snapped", 0.05))  # 5% penalty
            
            # Penalty: ID auto-clamped (from original segment metadata)
            if norm_meta.get("id_auto_clamped"):
                penalties.append(("id_auto_clamped", 0.15))  # 15% penalty
            
            # Penalty: Wall thickness < 0.05 in
            if seg.wall_thickness > 0 and seg.wall_thickness < 0.05:
                thin_wall_penalty = 0.10 * (1.0 - seg.wall_thickness / 0.05)  # Up to 10% penalty
                penalties.append(("thin_wall", thin_wall_penalty))
            
            # Penalty: Segment length < 2% of total Z
            segment_length = seg.z_end - seg.z_start
            rel_length = segment_length / total_z_range if total_z_range > 0 else 1.0
            if rel_length < 0.02:
                length_penalty = 0.10 * (1.0 - rel_length / 0.02)  # Up to 10% penalty
                penalties.append(("short_segment", length_penalty))
            
            # Calculate final confidence
            total_penalty = sum(penalty for _, penalty in penalties)
            final_confidence = max(0.0, min(1.0, base_conf - total_penalty))
            
            # Build flags list
            flags = []
            if norm_meta.get("merged_from_indices") and len(norm_meta["merged_from_indices"]) > 1:
                flags.append("auto_merged")
            if norm_meta.get("id_auto_clamped"):
                flags.append("id_assumed_solid")
            if seg.wall_thickness > 0 and seg.wall_thickness < 0.05:
                flags.append("thin_wall")
            if rel_length < 0.02:
                flags.append("short_segment")
            if final_confidence < 0.6:  # Low confidence threshold
                flags.append("low_confidence")
            
            normalized_segments.append({
                "z_start": seg.z_start,
                "z_end": seg.z_end,
                "od_diameter": seg.od_diameter,
                "id_diameter": seg.id_diameter,
                "confidence": float(final_confidence),
                "flags": flags,
                "_penalties": [(name, float(penalty)) for name, penalty in penalties]
            })

        # Compute total length before totals
        if normalized_segments:
            total_length = sum(seg["z_end"] - seg["z_start"] for seg in normalized_segments)

        # Compute totals using normalized stack
        if normalized_stack is None:
            raise ValueError("Failed to create normalized stack - no segments to process")

        totals = {
            "volume_in3": normalized_stack.total_volume(),
            "od_area_in2": normalized_stack.total_od_surface_area(),
            "id_area_in2": normalized_stack.total_id_surface_area(),
            "total_length_in": total_length,
            "end_face_area_start_in2": normalized_stack.end_face_area_start(),
            "end_face_area_end_in2": normalized_stack.end_face_area_end(),
            "od_shoulder_area_in2": normalized_stack.od_shoulder_area(),
            "id_shoulder_area_in2": normalized_stack.id_shoulder_area(),
            "planar_ring_area_in2": normalized_stack.total_planar_ring_area(),
            "total_surface_area_in2": normalized_stack.total_surface_area()
        }
        
        # Compute length-weighted overall confidence
        if normalized_segments and total_length > 0:
            weighted_sum = sum(
                (seg["z_end"] - seg["z_start"]) * seg["confidence"]
                for seg in normalized_segments
            )
            overall_confidence = weighted_sum / total_length
        else:
            overall_confidence = np.mean([s["confidence"] for s in normalized_segments]) if normalized_segments else 0.0
        
        # SANITY GATES: Validate inferred dimensions
        validation_errors = []
        validation_warnings = []
        
        # Calculate derived values for validation
        total_length_inches = total_length if normalized_segments else 0.0
        max_od_inches = max((seg["od_diameter"] for seg in normalized_segments), default=0.0)
        
        # Gate 1: Total length must be between 0.8 and 2.0 inches
        if total_length_inches < self.min_total_length_inches:
            validation_errors.append(
                f"Total length ({total_length_inches:.3f} in) is below minimum threshold "
                f"({self.min_total_length_inches} in). Auto-detect FAILED."
            )
        elif total_length_inches > self.max_total_length_inches:
            validation_errors.append(
                f"Total length ({total_length_inches:.3f} in) exceeds maximum threshold "
                f"({self.max_total_length_inches} in). Auto-detect FAILED."
            )
        
        # Gate 2: Max OD must be between 1.0 and 3.0 inches
        if max_od_inches < self.min_max_od_inches:
            validation_errors.append(
                f"Max OD ({max_od_inches:.3f} in) is below minimum threshold "
                f"({self.min_max_od_inches} in). Auto-detect FAILED."
            )
        elif max_od_inches > self.max_max_od_inches:
            validation_errors.append(
                f"Max OD ({max_od_inches:.3f} in) exceeds maximum threshold "
                f"({self.max_max_od_inches} in). Auto-detect FAILED."
            )
        
        # Add scale report to validation output
        scale_report['derived_total_length_inches'] = total_length_inches
        scale_report['derived_max_od_inches'] = max_od_inches
        scale_report['validation_passed'] = len(validation_errors) == 0
        
        # Update scale report file with validation results
        with open(scale_report_file, 'w') as f:
            json.dump(scale_report, f, indent=2)
        
        # If validation failed, return error status
        if validation_errors:
            logger.error(f"Auto-detect validation FAILED for job {job_id}:")
            for error in validation_errors:
                logger.error(f"  - {error}")
            
            # Update job status to indicate failure
            self.job_service.job_storage.update_job_status(job_id, JobStatus.FAILED)
            
            return {
                "job_id": job_id,
                "status": "VALIDATION_FAILED",
                "error": "Auto-detect validation failed. Dimensions are outside expected ranges.",
                "validation_errors": validation_errors,
                "scale_report": scale_report,
                "derived_values": {
                    "total_length_inches": total_length_inches,
                    "max_od_inches": max_od_inches
                },
                "message": "Auto-detect failed validation. Please use Assisted Manual mode to enter dimensions manually.",
                "outputs": ["scale_report.json"]  # Only scale report, no stack JSON
            }
        
        # Generate inferred_stack.json (using normalized segments)
        # Include scale report in inferred stack data
        inferred_stack_data = {
            "job_id": job_id,
            "source_view": {
                "page": best_view["page"],
                "view_index": best_view.get("view_index", 0),
                "view_conf": (best_view.get("scores") or {}).get("view_conf", 0.0)
            },
            "segments": normalized_segments,
            "overall_confidence": float(overall_confidence),
            "warnings": warnings,
            "scale_report": scale_report,  # Include scale calibration info
            "inferred_at_utc": datetime.now(timezone.utc).isoformat()
        }
        
        inferred_stack_file = outputs_path / "inferred_stack.json"
        with open(inferred_stack_file, 'w') as f:
            json.dump(inferred_stack_data, f, indent=2)
        
        # Generate part_summary.json (using normalized stack)
        z_range = [normalized_stack.segments[0].z_start, normalized_stack.segments[-1].z_end] if normalized_stack.segments else [0.0, 0.0]
        
        segments_list = []
        for i, seg in enumerate(normalized_stack.segments):
            seg_dict = {
                "z_start": seg.z_start,
                "z_end": seg.z_end,
                "od_diameter": seg.od_diameter,
                "id_diameter": seg.id_diameter,
                "wall_thickness": seg.wall_thickness,
                "volume_in3": seg.volume(),
                "od_area_in2": seg.od_surface_area(),
                "id_area_in2": seg.id_surface_area(),
                "confidence": normalized_segments[i]["confidence"] if i < len(normalized_segments) else 0.9,
                "flags": normalized_segments[i].get("flags", []) if i < len(normalized_segments) else []
            }
            segments_list.append(seg_dict)
        
        part_summary = PartSummary(
            schema_version="0.1",
            generated_at_utc=datetime.now(timezone.utc).isoformat(),
            units={
                "length": "in",
                "area": "in^2",
                "volume": "in^3"
            },
            scale_report=scale_report,
            z_range=z_range,
            segments=segments_list,
            totals=totals,
            inference_metadata={
                "mode": mode,  # "reference_only" or "auto_detect"
                "overall_confidence": float(overall_confidence),
                "source": "math_stack_only"  # Math-only path, no OCC solid generated yet
            },
            features=None  # Features will be added later by feature detection
        )

        # Convert to dict for JSON serialization
        part_summary_dict = part_summary.to_dict()
        
        summary_file = outputs_path / "part_summary.json"
        with open(summary_file, 'w') as f:
            json.dump(part_summary_dict, f, indent=2)
        
        # Generate human-readable explanation
        from app.utils.stack_explanation import generate_stack_explanation
        explanation = generate_stack_explanation(
            normalized_segments,
            units="in",
            overall_confidence=overall_confidence
        )
        
        # Update job status
        self.job_service.job_storage.update_job_status(job_id, JobStatus.COMPLETED)
        
        return {
            "job_id": job_id,
            "status": "DONE",
            "segments": normalized_segments,
            "totals": totals,
            "overall_confidence": float(overall_confidence),
            "segment_confidences": [s["confidence"] for s in normalized_segments],
            "warnings": warnings,
            "explanation": explanation,
            "scale_report": scale_report,  # Include scale calibration info
            "outputs": ["inferred_stack.json", "part_summary.json", "scale_report.json"]
        }
    
    # Standard drawing scales used by _estimate_drawing_scale
    STANDARD_SCALES = [0.1, 0.2, 0.25, 0.5, 1.0, 2.0, 2.5, 4.0, 5.0, 10.0]

    # Regex patterns for SCALE annotations in title blocks
    _SCALE_TEXT_RE = re.compile(
        r'(?:SCALE|SC\.?|DRAWN\s+SCALE)\s*[:=]?\s*'
        r'(\d+)\s*[:/-]\s*(\d+)',
        re.IGNORECASE,
    )

    def _detect_scale_from_title_block(self, page_img: np.ndarray) -> Optional[float]:
        """Try to OCR the SCALE text from the bottom-right title block area."""
        if self.dimension_detector.easyocr_reader is None:
            return None
        h, w = page_img.shape[:2]
        # Title block is typically in the bottom-right ~25% of the page
        y0 = int(h * 0.75)
        x0 = int(w * 0.50)
        title_crop = page_img[y0:h, x0:w]
        if title_crop.size == 0:
            return None
        try:
            results = self.dimension_detector.easyocr_reader.readtext(title_crop)
            for (_bbox, text, _conf) in results:
                m = self._SCALE_TEXT_RE.search(text)
                if m:
                    drawing_part = float(m.group(1))
                    actual_part = float(m.group(2))
                    if actual_part > 0 and drawing_part > 0:
                        ratio = drawing_part / actual_part
                        logger.info(
                            f"[DRAW_SCALE] title block OCR: '{text}' "
                            f"→ SCALE {drawing_part}:{actual_part} = {ratio}"
                        )
                        return ratio
        except Exception as e:
            logger.warning(f"[DRAW_SCALE] title block OCR failed: {e}")
        return None

    def _estimate_drawing_scale(
        self,
        dimensions: List[Dict],
        od_data: Dict,
        base_inch_per_pixel: float,
        page_img: Optional[np.ndarray] = None,
    ) -> float:
        """Estimate the drawing scale ratio from OCR dimensions and geometry.

        Strategy (in priority order):
        1. OCR the SCALE text from the title block (most reliable).
        2. Use OCR dimension values + DPI-based page measurements to vote
           on the most likely standard scale.
        3. Fall back to 1.0 (full-size).
        """
        # --- Strategy 1: title block SCALE text ---
        if page_img is not None:
            title_scale = self._detect_scale_from_title_block(page_img)
            if title_scale is not None:
                return title_scale

        # --- Strategy 2: vote from OCR dims vs DPI page dims ---
        axial_positions = od_data.get("axial_positions", [])
        od_radii = od_data.get("od_radii", [])
        if len(axial_positions) == 0 or len(od_radii) == 0:
            return 1.0

        total_length_px = (
            axial_positions[-1] - axial_positions[0]
            if len(axial_positions) > 1
            else 0
        )
        max_od_diameter_px = float(np.max(od_radii)) * 2.0

        page_len_in = total_length_px * base_inch_per_pixel
        page_od_in = max_od_diameter_px * base_inch_per_pixel

        logger.info(
            f"[DRAW_SCALE] page dims: OD={page_od_in:.3f}\" len={page_len_in:.3f}\""
        )

        votes: List[float] = []

        for dim in dimensions:
            value = dim.get("value", 0)
            if dim.get("unit", "in") != "in" or value <= 0.05:
                continue

            for geom_name, page_in in [("OD", page_od_in), ("LEN", page_len_in)]:
                if page_in <= 0:
                    continue
                implied = page_in / value
                nearest = min(self.STANDARD_SCALES, key=lambda s: abs(s - implied))
                err = abs(implied - nearest) / nearest
                if err < 0.25:
                    votes.append(nearest)
                    logger.debug(
                        f"[DRAW_SCALE] OCR {value:.3f} vs {geom_name} "
                        f"→ implied={implied:.2f} ≈ {nearest}:1 (err={err:.2%})"
                    )

        if not votes:
            logger.info("[DRAW_SCALE] No scale votes, defaulting to 1:1")
            return 1.0

        from collections import Counter
        scale_counts = Counter(votes)
        best_scale, best_count = scale_counts.most_common(1)[0]
        logger.info(
            f"[DRAW_SCALE] votes={dict(scale_counts)} → "
            f"best={best_scale}:1 ({best_count} votes)"
        )
        return best_scale

    def _filter_dimensions_with_classifier(self, dimensions: List[Dict]) -> List[Dict]:
        """
        Filter dimensions using RFQ classifier rules to remove tolerance ranges,
        metric brackets, and small features.
        
        Args:
            dimensions: List of detected dimensions from OCR
            
        Returns:
            Filtered list of dimensions
        """
        if not dimensions:
            return []
        
        import re
        
        # Convert dimensions to classifier format and filter
        filtered_dimensions = []
        
        for dim in dimensions:
            value = dim.get('value')
            if value is None:
                continue
            
            text = dim.get('text', '')
            position = dim.get('position')
            confidence = dim.get('confidence', 0.5)
            
            # Rule 1: Ignore metric bracket values [mm]
            if '[' in text and ']' in text:
                bracket_pattern = re.compile(r'\[[\d.]+\]')
                if bracket_pattern.search(text):
                    logger.debug(f"Filtering out metric bracket dimension: {text} (value: {value})")
                    continue
            
            # Rule 2: Ignore tolerance ranges (e.g., 0.723-0.727, 1.006-1.008, .185-.190)
            tolerance_pattern = re.compile(r'\d+\.\d+\s*[-–]\s*\d+\.\d+|\.\d+\s*[-–]\s*\.\d+')
            if tolerance_pattern.search(text):
                logger.debug(f"Filtering out tolerance range dimension: {text} (value: {value})")
                continue
            
            # Determine if this is a diameter (OD/ID) or length dimension
            is_diameter = any(sym in text for sym in ['Ø', '∅', 'DIA', 'DIAMETER'])
            
            # Rule 3: For diameters (OD/ID), check valid range: 0.25-5.0 inches
            if is_diameter:
                if not (0.25 <= value <= 5.0):
                    logger.debug(f"Filtering out diameter dimension outside range: {value} in ({text})")
                    continue
            
            # Rule 4: For lengths, check valid range: >= 0.3 inches (not small shoulder)
            if not is_diameter:
                if value < 0.3:
                    logger.debug(f"Filtering out small shoulder dimension: {value} in ({text})")
                    continue
                if value > 20.0:
                    logger.debug(f"Filtering out oversized length dimension: {value} in ({text})")
                    continue
            
            # Passed all filters - keep this dimension
            filtered_dimensions.append({
                'value': value,
                'unit': dim.get('unit', 'in'),
                'text': text,
                'position': position,
                'confidence': confidence
            })
        
        logger.info(f"Dimension filtering: {len(dimensions)} raw → {len(filtered_dimensions)} filtered")
        
        return filtered_dimensions
    
    def _filter_dimensions_with_classifier(self, dimensions: List[Dict]) -> List[Dict]:
        """
        Filter dimensions using RFQ classifier to remove tolerance ranges,
        metric brackets, and small features.
        
        Args:
            dimensions: List of detected dimensions from OCR
            
        Returns:
            Filtered list of dimensions
        """
        if not dimensions:
            return []
        
        # Convert dimensions to classifier format
        dimension_candidates = []
        for dim in dimensions:
            value = dim.get('value')
            if value is None:
                continue
            
            text = dim.get('text', '')
            position = dim.get('position')
            confidence = dim.get('confidence', 0.5)
            
            # Determine orientation (diameters are typically vertical, lengths horizontal)
            # This is a heuristic - in practice, we'd need more context
            orientation = 'vertical' if any(sym in text for sym in ['Ø', '∅', 'DIA', 'DIAMETER']) else 'horizontal'
            
            dimension_candidates.append({
                'value': float(value),
                'text': text,
                'orientation': orientation,
                'position': position,
                'confidence': confidence,
                'segment_length': 0.0  # Will be updated if segments available
            })
        
        # Use classifier to filter
        filtered_candidates = []
        
        for cand in dimension_candidates:
            # Rule 1: Ignore metric bracket values
            text = cand.get('text', '')
            if '[' in text and ']' in text:
                # Check if value is inside brackets
                import re
                bracket_pattern = re.compile(r'\[[\d.]+\]')
                if bracket_pattern.search(text):
                    logger.debug(f"Filtering out metric bracket dimension: {text}")
                    continue
            
            # Rule 2: Ignore tolerance ranges
            tolerance_pattern = re.compile(r'\d+\.\d+\s*[-–]\s*\d+\.\d+|\.\d+\s*[-–]\s*\.\d+')
            if tolerance_pattern.search(text):
                logger.debug(f"Filtering out tolerance range dimension: {text}")
                continue
            
            # Rule 3: Check value ranges
            value = cand.get('value', 0.0)
            orientation = cand.get('orientation', 'unknown')
            
            # For diameters (OD/ID): must be 0.25-5.0 inches
            if orientation == 'vertical' or any(sym in text for sym in ['Ø', '∅', 'DIA']):
                if not (0.25 <= value <= 5.0):
                    logger.debug(f"Filtering out diameter dimension outside range: {value} in ({text})")
                    continue
            
            # For lengths: must be >= 0.3 inches (not small shoulder)
            if orientation == 'horizontal':
                if value < 0.3:
                    logger.debug(f"Filtering out small shoulder dimension: {value} in ({text})")
                    continue
                if value > 20.0:
                    logger.debug(f"Filtering out oversized length dimension: {value} in ({text})")
                    continue
            
            # Passed all filters
            filtered_candidates.append(cand)
        
        # Convert back to original format
        filtered_dimensions = []
        for cand in filtered_candidates:
            filtered_dimensions.append({
                'value': cand['value'],
                'unit': 'in',
                'text': cand['text'],
                'position': cand.get('position'),
                'confidence': cand.get('confidence', 0.5)
            })
        
        return filtered_dimensions

