"""CV-based feature detection using computer vision."""

import os
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime

# Optional OpenCV import
try:
    import cv2
    import numpy as np
    _OPENCV_AVAILABLE = True
except ImportError:
    _OPENCV_AVAILABLE = False
    cv2 = None
    np = None

from .schema import (
    DetectedFeatures, HoleFeature, SlotFeature, create_feature_meta, DetectionResult
)


class CVFeatureDetector:
    """Detector for holes and slots using computer vision."""

    # Version tracking - bump when CV algorithms change
    DETECTOR_VERSION = "1.0.0"

    def __init__(self):
        """Initialize CV feature detector."""
        self.feature_flag = os.getenv('FEATURE_CV_DETECT', '0') == '1'

    def detect_features(self, job_id: str, file_storage) -> DetectionResult:
        """
        Detect geometric features using computer vision.

        Args:
            job_id: Job identifier
            file_storage: File storage instance

        Returns:
            DetectionResult with detected features
        """
        if not self.feature_flag:
            return DetectionResult(
                success=False,
                error="CV feature detection is disabled (FEATURE_CV_DETECT=0)"
            )

        if not _OPENCV_AVAILABLE:
            return DetectionResult(
                success=False,
                error="OpenCV not available for CV detection"
            )

        try:
            # Get best view from auto-detect results
            best_view = self._get_best_view(job_id, file_storage)
            if not best_view:
                return DetectionResult(
                    success=False,
                    error="No best view found for CV detection"
                )

            # Load page image
            page_image_path = self._get_page_image_path(job_id, best_view['page'], file_storage)
            if not page_image_path or not page_image_path.exists():
                return DetectionResult(
                    success=False,
                    error=f"Page image not found: {page_image_path}"
                )

            # Crop to view bbox
            view_image = self._crop_to_view(page_image_path, best_view['bbox'])
            if view_image is None:
                return DetectionResult(
                    success=False,
                    error="Failed to crop image to view"
                )

            # Detect features
            holes = self._detect_holes_cv(view_image, best_view)
            slots = self._detect_slots_cv(view_image, best_view)

            # Get scale information
            scale_info = self._get_scale_info(job_id, file_storage)
            inch_per_pixel = scale_info.get('inch_per_pixel', 0.01)  # Default fallback

            # Convert pixel dimensions to inches
            for hole in holes:
                if hole.geometry_px:
                    hole.geometry_in = self._convert_geometry_to_inches(
                        hole.geometry_px, inch_per_pixel
                    )

            for slot in slots:
                if slot.geometry_px:
                    slot.geometry_in = self._convert_geometry_to_inches(
                        slot.geometry_px, inch_per_pixel
                    )

            # Create metadata
            meta = create_feature_meta(self.DETECTOR_VERSION)

            features = DetectedFeatures(
                holes=holes,
                slots=slots,
                meta=meta
            )

            return DetectionResult(
                success=True,
                features=features
            )

        except Exception as e:
            return DetectionResult(
                success=False,
                error=f"CV detection failed: {str(e)}"
            )

    def _get_best_view(self, job_id: str, file_storage) -> Optional[Dict[str, Any]]:
        """Get the best view from auto-detect results."""
        try:
            outputs_path = file_storage.get_outputs_path(job_id)
            best_view_file = outputs_path / "best_view.json"

            if not best_view_file.exists():
                return None

            with open(best_view_file, 'r') as f:
                best_view_data = json.load(f)

            return {
                'page': best_view_data.get('page', 0),
                'bbox': best_view_data.get('bbox', [0, 0, 1, 1]),
                'view_index': best_view_data.get('view_index', 0)
            }
        except Exception:
            return None

    def _get_page_image_path(self, job_id: str, page_num: int, file_storage) -> Optional[Path]:
        """Get the path to a page image."""
        outputs_path = file_storage.get_outputs_path(job_id)
        image_path = outputs_path / f"pdf_pages" / "page_{page_num}.png"
        return image_path if image_path.exists() else None

    def _crop_to_view(self, image_path: Path, bbox: List[float]) -> Optional[np.ndarray]:
        """Crop image to view bounding box."""
        try:
            image = cv2.imread(str(image_path))
            if image is None:
                return None

            height, width = image.shape[:2]

            # Convert normalized bbox to pixel coordinates
            x1 = int(bbox[0] * width)
            y1 = int(bbox[1] * height)
            x2 = int(bbox[2] * width)
            y2 = int(bbox[3] * height)

            # Ensure valid coordinates
            x1, x2 = max(0, x1), min(width, x2)
            y1, y2 = max(0, y1), min(height, y2)

            if x2 <= x1 or y2 <= y1:
                return None

            return image[y1:y2, x1:x2]
        except Exception:
            return None

    def _detect_holes_cv(self, image: np.ndarray, view_info: Dict[str, Any]) -> List[HoleFeature]:
        """Detect holes using computer vision with strict filtering."""
        holes = []
        
        # Filtering thresholds to reduce false positives
        MIN_CONFIDENCE = 0.75  # Minimum confidence to accept a hole
        MIN_RADIUS_PX = 10     # Minimum radius in pixels (very small = noise)
        MAX_RADIUS_PX = 100    # Maximum radius in pixels (too large = not a hole)
        MIN_DIST_BETWEEN = 30  # Minimum distance between detected holes

        try:
            # Preprocess image
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            blurred = cv2.GaussianBlur(gray, (5, 5), 0)

            # Detect circles using Hough transform with stricter parameters
            circles = cv2.HoughCircles(
                blurred,
                cv2.HOUGH_GRADIENT,
                dp=1,
                minDist=MIN_DIST_BETWEEN,  # Increased from 20
                param1=60,                 # Increased from 50 (edge strength)
                param2=40,                 # Increased from 30 (circle detection threshold)
                minRadius=MIN_RADIUS_PX,   # Increased from 5
                maxRadius=MAX_RADIUS_PX    # Increased from 50
            )

            if circles is not None:
                circles = np.round(circles[0, :]).astype(int)
                
                # Track accepted hole centers to avoid duplicates
                accepted_centers = []

                for circle in circles:
                    center_x, center_y, radius = circle
                    
                    # Skip if radius is out of bounds
                    if radius < MIN_RADIUS_PX or radius > MAX_RADIUS_PX:
                        continue

                    # Calculate confidence based on circle properties
                    confidence = self._calculate_hole_confidence(image, center_x, center_y, radius)
                    
                    # Skip low confidence holes
                    if confidence < MIN_CONFIDENCE:
                        continue
                    
                    # Skip if too close to an already accepted hole (duplicate detection)
                    is_duplicate = False
                    for ac_x, ac_y, ac_r in accepted_centers:
                        dist = np.sqrt((center_x - ac_x)**2 + (center_y - ac_y)**2)
                        if dist < max(radius, ac_r) * 1.5:  # Overlapping circles
                            is_duplicate = True
                            break
                    
                    if is_duplicate:
                        continue

                    # Store pixel geometry
                    geometry_px = {
                        'center': [float(center_x), float(center_y)],
                        'radius': float(radius),
                        'diameter': float(radius * 2)
                    }

                    hole = HoleFeature(
                        confidence=confidence,
                        source_page=view_info['page'],
                        source_view_index=view_info['view_index'],
                        diameter=float(radius * 2),  # Will be converted to inches later
                        kind="cross",  # CV detection assumes cross holes
                        geometry_px=geometry_px,
                        notes=f"CV-detected hole at ({center_x}, {center_y}) with radius {radius}px"
                    )
                    holes.append(hole)
                    accepted_centers.append((center_x, center_y, radius))

        except Exception as e:
            print(f"Warning: CV hole detection failed: {e}")

        return holes

    def _detect_slots_cv(self, image: np.ndarray, view_info: Dict[str, Any]) -> List[SlotFeature]:
        """Detect slots using computer vision with strict filtering."""
        slots = []
        
        # Filtering thresholds to reduce false positives
        MIN_CONFIDENCE = 0.65    # Minimum confidence to accept a slot
        MIN_SIZE_PX = 20         # Minimum dimension in pixels
        MAX_SIZE_PX = 500        # Maximum dimension in pixels
        MIN_ASPECT_RATIO = 3.0   # Increased from 2.0 - must be clearly elongated

        try:
            # Preprocess image
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            blurred = cv2.GaussianBlur(gray, (3, 3), 0)

            # Edge detection
            edges = cv2.Canny(blurred, 50, 150)

            # Find contours
            contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            
            # Track accepted slot bboxes to avoid duplicates
            accepted_slots = []

            for contour in contours:
                # Approximate contour
                epsilon = 0.02 * cv2.arcLength(contour, True)
                approx = cv2.approxPolyDP(contour, epsilon, True)

                # Check if it looks like a slot (elongated rectangle with rounded ends)
                if len(approx) >= 6:  # Polygonal approximation of rounded rectangle
                    # Calculate bounding box
                    x, y, w, h = cv2.boundingRect(contour)
                    
                    # Size filtering
                    if min(w, h) < MIN_SIZE_PX or max(w, h) > MAX_SIZE_PX:
                        continue

                    # Check if elongated (slot-like) with stricter ratio
                    aspect_ratio = max(w, h) / min(w, h)
                    if aspect_ratio < MIN_ASPECT_RATIO:
                        continue

                    # Calculate confidence based on shape properties
                    confidence = self._calculate_slot_confidence(image, x, y, w, h, contour)
                    
                    # Skip low confidence slots
                    if confidence < MIN_CONFIDENCE:
                        continue
                    
                    # Check for duplicates/overlapping slots
                    is_duplicate = False
                    for ax, ay, aw, ah in accepted_slots:
                        # Check overlap using IoU
                        inter_x1 = max(x, ax)
                        inter_y1 = max(y, ay)
                        inter_x2 = min(x + w, ax + aw)
                        inter_y2 = min(y + h, ay + ah)
                        if inter_x1 < inter_x2 and inter_y1 < inter_y2:
                            inter_area = (inter_x2 - inter_x1) * (inter_y2 - inter_y1)
                            union_area = w * h + aw * ah - inter_area
                            iou = inter_area / union_area if union_area > 0 else 0
                            if iou > 0.3:  # More than 30% overlap
                                is_duplicate = True
                                break
                    
                    if is_duplicate:
                        continue

                    # Determine orientation
                    orientation = "axial" if w > h else "radial"

                    # Store pixel geometry
                    geometry_px = {
                        'bbox': [float(x), float(y), float(x + w), float(y + h)],
                        'width': float(w),
                        'length': float(h),
                        'aspect_ratio': float(aspect_ratio)
                    }

                    slot = SlotFeature(
                        confidence=confidence,
                        source_page=view_info['page'],
                        source_view_index=view_info['view_index'],
                        width=float(w),  # Will be converted to inches later
                        length=float(h),  # Will be converted to inches later
                        orientation=orientation,
                        geometry_px=geometry_px,
                        notes=f"CV-detected slot at ({x}, {y}) with size {w}x{h}px"
                    )
                    slots.append(slot)
                    accepted_slots.append((x, y, w, h))

        except Exception as e:
            print(f"Warning: CV slot detection failed: {e}")

        return slots

    def _calculate_hole_confidence(self, image: np.ndarray, center_x: int, center_y: int, radius: int) -> float:
        """Calculate confidence score for detected hole."""
        try:
            # Check circularity by sampling points on the circumference
            angles = np.linspace(0, 2*np.pi, 16, endpoint=False)
            circle_points = []
            height, width = image.shape[:2]

            for angle in angles:
                x = int(center_x + radius * np.cos(angle))
                y = int(center_y + radius * np.sin(angle))

                if 0 <= x < width and 0 <= y < height:
                    # Check edge strength at this point
                    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) > 2 else image
                    edge_strength = cv2.Canny(gray[y-1:y+2, x-1:x+2], 50, 150).sum()
                    circle_points.append(edge_strength > 0)

            # Calculate circularity score
            edge_points = sum(circle_points)
            circularity = edge_points / len(circle_points) if circle_points else 0

            # Base confidence on circularity and size
            confidence = 0.5 + (circularity * 0.4)  # 0.5 to 0.9 range

            # Size factor - smaller circles are harder to detect reliably
            size_factor = min(radius / 20.0, 1.0)  # Normalize by typical size
            confidence *= (0.7 + 0.3 * size_factor)

            return min(confidence, 1.0)

        except Exception:
            return 0.5  # Default confidence

    def _calculate_slot_confidence(self, image: np.ndarray, x: int, y: int, w: int, h: int, contour) -> float:
        """Calculate confidence score for detected slot."""
        try:
            # Calculate contour properties
            area = cv2.contourArea(contour)
            perimeter = cv2.arcLength(contour, True)
            bbox_area = w * h

            # Compactness (how closely the shape matches its bounding box)
            compactness = area / bbox_area if bbox_area > 0 else 0

            # Rectangularity (how rectangular the shape is)
            rect_perimeter = 2 * (w + h)
            rectangularity = rect_perimeter / perimeter if perimeter > 0 else 0

            # Edge strength along the contour
            edge_mask = np.zeros(image.shape[:2], dtype=np.uint8)
            cv2.drawContours(edge_mask, [contour], 0, 255, 2)
            edge_pixels = cv2.countNonZero(edge_mask)
            edge_density = edge_pixels / perimeter if perimeter > 0 else 0

            # Combine factors for confidence
            confidence = (
                0.4 * compactness +      # Shape fills bounding box well
                0.3 * rectangularity +   # Shape is rectangular
                0.3 * edge_density       # Strong edges
            )

            return min(max(confidence, 0.3), 1.0)  # Clamp to reasonable range

        except Exception:
            return 0.4  # Default confidence

    def _get_scale_info(self, job_id: str, file_storage) -> Dict[str, Any]:
        """Get scale information for dimension conversion."""
        try:
            outputs_path = file_storage.get_outputs_path(job_id)
            scale_file = outputs_path / "scale_report.json"

            if scale_file.exists():
                with open(scale_file, 'r') as f:
                    scale_data = json.load(f)
                return scale_data
        except Exception:
            pass

        return {"inch_per_pixel": 0.01}  # Default fallback

    def _convert_geometry_to_inches(self, geometry_px: Dict[str, Any], inch_per_pixel: float) -> Dict[str, Any]:
        """Convert pixel geometry to inches."""
        geometry_in = {}

        for key, value in geometry_px.items():
            if isinstance(value, (int, float)):
                geometry_in[key] = value * inch_per_pixel
            elif isinstance(value, list):
                geometry_in[key] = [v * inch_per_pixel if isinstance(v, (int, float)) else v for v in value]
            else:
                geometry_in[key] = value

        return geometry_in