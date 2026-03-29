"""Auto-detect turned part views from PDF images."""

from __future__ import annotations  # defer np.ndarray annotation evaluation

import os
import json
from pathlib import Path
from typing import List, Dict, Tuple, Optional
from app.storage.file_storage import FileStorage

try:
    import cv2
    import numpy as np
    _CV2_AVAILABLE = True
except ImportError:
    cv2 = None  # type: ignore[assignment]
    _CV2_AVAILABLE = False
    try:
        import numpy as np
    except Exception:
        np = None  # type: ignore[assignment]

# Optional EasyOCR import
try:
    # Workaround for Windows OpenMP runtime conflicts (torch/numpy).
    # Safe for our use: prevents import-time crash; worst-case is reduced performance.
    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
    import easyocr
    _EASYOCR_AVAILABLE = True
except Exception:
    _EASYOCR_AVAILABLE = False
    easyocr = None


class AutoDetectService:
    """Service for auto-detecting turned part views in PDF images."""
    
    def __init__(self):
        """Initialize auto-detect service."""
        self.file_storage = FileStorage()
        self.confidence_threshold = 0.65
        self.easyocr_reader = None
        
        # Initialize EasyOCR if available (lazy loading)
        if _EASYOCR_AVAILABLE:
            try:
                self.easyocr_reader = easyocr.Reader(['en'], gpu=False)
            except Exception:
                # If initialization fails, continue without OCR
                self.easyocr_reader = None
    
    def crop_view(self, image: np.ndarray, bbox_pixels: List[int]) -> np.ndarray:
        """Crop image to view bounding box.
        
        Args:
            image: Full page image
            bbox_pixels: [x, y, width, height] in pixels
            
        Returns:
            Cropped image
        """
        x, y, w, h = bbox_pixels
        return image[y:y+h, x:x+w]
    
    def preprocess_image(self, crop: np.ndarray) -> np.ndarray:
        """Preprocess image for better detection.
        
        Args:
            crop: Cropped view image
            
        Returns:
            Preprocessed grayscale image
        """
        # Convert to grayscale
        if len(crop.shape) == 3:
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        else:
            gray = crop
        
        # Apply Gaussian blur to reduce noise
        gray = cv2.GaussianBlur(gray, (5, 5), 0)
        
        # Enhance contrast using CLAHE (Contrast Limited Adaptive Histogram Equalization)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        gray = clahe.apply(gray)
        
        # Apply morphological operations to clean up
        kernel = np.ones((3, 3), np.uint8)
        gray = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, kernel)
        
        return gray
    
    def detect_axis_candidate(self, crop: np.ndarray) -> Tuple[Optional[Dict], float]:
        """Detect long axis candidate using improved HoughLinesP with multiple candidates.
        
        Args:
            crop: Cropped view image
            
        Returns:
            Tuple of (axis_info, confidence)
            axis_info: {line: [x1, y1, x2, y2], angle: float, length: float} or None
            confidence: 0.0 to 1.0
        """
        # Preprocess image
        gray = self.preprocess_image(crop)
        
        # Apply adaptive edge detection with multiple thresholds (enhanced for small features)
        edges1 = cv2.Canny(gray, 20, 60, apertureSize=3)   # Very sensitive for faint lines
        edges2 = cv2.Canny(gray, 30, 100, apertureSize=3)  # Lower threshold for faint lines
        edges3 = cv2.Canny(gray, 50, 150, apertureSize=3)  # Standard threshold
        edges4 = cv2.Canny(gray, 80, 200, apertureSize=3)  # Higher threshold for strong edges
        edges = cv2.bitwise_or(cv2.bitwise_or(edges1, edges2), cv2.bitwise_or(edges3, edges4))
        
        # Detect lines using HoughLinesP with multiple parameter sets
        h, w = gray.shape
        max_dim = max(h, w)
        min_line_length = max_dim * 0.25  # At least 25% of image dimension
        
        # Try multiple thresholds to find best lines (including lower thresholds for small features)
        all_lines = []
        for threshold in [10, 20, 30, 50, 70]:  # Added lower thresholds (10, 20) to catch short lines
            lines = cv2.HoughLinesP(
                edges,
                rho=1,
                theta=np.pi / 180,
                threshold=threshold,
                minLineLength=int(min_line_length * 0.5),  # Reduced to catch shorter lines
                maxLineGap=max_dim * 0.02  # Reduced from 5% to 2% to connect closer segments
            )
            if lines is not None:
                all_lines.extend(lines.reshape(-1, 4).tolist())
        
        if len(all_lines) == 0:
            return None, 0.0
        
        # Convert to numpy array
        lines = np.array(all_lines)
        
        # Calculate line properties
        dx = lines[:, 2] - lines[:, 0]
        dy = lines[:, 3] - lines[:, 1]
        lengths = np.sqrt(dx**2 + dy**2)
        angles = np.arctan2(dy, dx) * 180 / np.pi
        
        # Normalize angles to 0-90 degrees
        angles_abs = np.abs(angles)
        angles_abs = np.where(angles_abs > 90, 180 - angles_abs, angles_abs)
        
        # Score each line:
        # 1. Length (longer is better)
        # 2. Verticality (closer to 90° or 0° is better for turned parts)
        # 3. Position (closer to center is better)
        length_scores = lengths / max_dim
        vertical_scores = 1.0 - np.minimum(angles_abs, 90 - angles_abs) / 45.0
        
        # Check if line passes near center
        center_x, center_y = w // 2, h // 2
        line_centers_x = (lines[:, 0] + lines[:, 2]) / 2
        line_centers_y = (lines[:, 1] + lines[:, 3]) / 2
        dist_to_center = np.sqrt((line_centers_x - center_x)**2 + (line_centers_y - center_y)**2)
        max_dist = max_dim * 0.3
        center_scores = np.maximum(0, 1.0 - dist_to_center / max_dist)
        
        # Combined score for each line
        line_scores = (
            length_scores * 0.4 +
            vertical_scores * 0.4 +
            center_scores * 0.2
        )
        
        # Find best line
        best_idx = np.argmax(line_scores)
        best_line = lines[best_idx]
        best_score = line_scores[best_idx]
        
        x1, y1, x2, y2 = best_line
        length = lengths[best_idx]
        angle = angles[best_idx]
        
        # Additional confidence boost if multiple similar lines found (consensus)
        # Check for lines with similar angles and positions
        similar_angles = np.abs(angles - angle) < 5  # Within 5 degrees
        similar_lengths = np.abs(lengths - length) / length < 0.3  # Within 30% length
        consensus_count = np.sum(similar_angles & similar_lengths)
        consensus_boost = min(0.2, consensus_count * 0.05)  # Up to 20% boost
        
        axis_conf = min(1.0, max(0.0, best_score + consensus_boost))
        
        axis_info = {
            "line": [int(x1), int(y1), int(x2), int(y2)],
            "angle": float(angle),
            "length": float(length)
        }
        
        return axis_info, axis_conf
    
    def compute_symmetry_score(self, crop: np.ndarray, axis_info: Optional[Dict]) -> float:
        """Compute improved symmetry score around detected axis.
        
        Args:
            crop: Cropped view image
            axis_info: Axis information from detect_axis_candidate
            
        Returns:
            Symmetry confidence score (0.0 to 1.0)
        """
        if axis_info is None:
            return 0.0
        
        # Use preprocessed image for better results
        gray = self.preprocess_image(crop)
        h, w = gray.shape
        
        # Get axis line
        x1, y1, x2, y2 = axis_info["line"]
        
        # Compute line equation: ax + by + c = 0
        dx = x2 - x1
        dy = y2 - y1
        length = np.sqrt(dx**2 + dy**2)
        
        if length < 1e-6:
            return 0.0
        
        angle = axis_info["angle"]
        angle_abs = abs(angle)
        
        # Create reflected image using optimized method
        reflected = np.zeros_like(gray)
        
        if angle_abs < 15 or angle_abs > 165:  # Nearly horizontal axis
            # Horizontal symmetry: reflect around horizontal line through center
            mid_y = h // 2
            reflected = np.flipud(gray)
            # Align to center
            if mid_y * 2 < h:
                reflected = np.pad(reflected, ((0, h - reflected.shape[0]), (0, 0)), mode='edge')[:h, :]
        elif 75 < angle_abs < 105:  # Nearly vertical axis (most common for turned parts)
            # Vertical symmetry: reflect around vertical line through center
            mid_x = w // 2
            reflected = np.fliplr(gray)
        else:
            # For non-vertical/horizontal axes, compute actual reflection
            center_x, center_y = w // 2, h // 2
            
            # Check if axis line passes near center
            a = -dy
            b = dx
            c = -(a * x1 + b * y1)
            dist_to_center = abs(a * center_x + b * center_y + c) / (length + 1e-6)
            
            # If axis is far from center, lower confidence
            max_dist = min(w, h) * 0.25  # 25% of image dimension
            if dist_to_center > max_dist:
                return 0.2  # Low confidence
            
            # Use vertical symmetry as approximation (most turned parts have vertical axis)
            reflected = np.fliplr(gray)
        
        # Multiple symmetry metrics for better confidence
        
        # 1. Normalized cross-correlation (existing method)
        gray_norm = gray.astype(np.float32) / 255.0
        reflected_norm = reflected.astype(np.float32) / 255.0
        
        mean_gray = np.mean(gray_norm)
        mean_reflected = np.mean(reflected_norm)
        
        numerator = np.sum((gray_norm - mean_gray) * (reflected_norm - mean_reflected))
        denom_gray = np.sqrt(np.sum((gray_norm - mean_gray) ** 2))
        denom_reflected = np.sqrt(np.sum((reflected_norm - mean_reflected) ** 2))
        
        if denom_gray < 1e-6 or denom_reflected < 1e-6:
            corr_score = 0.0
        else:
            correlation = numerator / (denom_gray * denom_reflected)
            corr_score = max(0.0, min(1.0, (correlation + 1.0) / 2.0))
        
        # 2. Structural Similarity Index (SSIM-like metric)
        # Compute mean squared error in symmetric regions
        if angle_abs < 15 or angle_abs > 165:
            # Horizontal: compare top and bottom halves
            half_h = h // 2
            top_half = gray[:half_h, :]
            bottom_half = np.flipud(gray[half_h:, :])
            if top_half.shape[0] != bottom_half.shape[0]:
                min_h = min(top_half.shape[0], bottom_half.shape[0])
                top_half = top_half[:min_h, :]
                bottom_half = bottom_half[:min_h, :]
        else:
            # Vertical: compare left and right halves
            half_w = w // 2
            left_half = gray[:, :half_w]
            right_half = np.fliplr(gray[:, half_w:])
            if left_half.shape[1] != right_half.shape[1]:
                min_w = min(left_half.shape[1], right_half.shape[1])
                left_half = left_half[:, :min_w]
                right_half = right_half[:, :min_w]
            top_half = left_half
            bottom_half = right_half
        
        if top_half.size > 0 and bottom_half.size > 0:
            mse = np.mean((top_half.astype(np.float32) - bottom_half.astype(np.float32)) ** 2)
            max_val = 255.0
            ssim_score = 1.0 - min(1.0, mse / (max_val ** 2))
        else:
            ssim_score = 0.0
        
        # 3. Edge symmetry (check if edges are symmetric)
        edges = cv2.Canny(gray, 50, 150)
        edges_reflected = cv2.Canny(reflected, 50, 150)
        edge_overlap = np.sum((edges > 0) & (edges_reflected > 0))
        edge_total = np.sum((edges > 0) | (edges_reflected > 0))
        edge_score = edge_overlap / (edge_total + 1e-6)
        
        # Combine multiple symmetry metrics
        sym_conf = (
            corr_score * 0.5 +
            ssim_score * 0.3 +
            edge_score * 0.2
        )
        
        return min(1.0, max(0.0, sym_conf))
    
    def detect_text_hints(self, crop: np.ndarray) -> Tuple[float, float]:
        """Detect text hints for diameter (Ø) and section view indicators.
        
        Args:
            crop: Cropped view image
            
        Returns:
            Tuple of (dia_text_conf, section_conf)
        """
        if not _EASYOCR_AVAILABLE or self.easyocr_reader is None:
            return 0.0, 0.0
        
        try:
            # Run OCR
            results = self.easyocr_reader.readtext(crop)
            
            dia_text_conf = 0.0
            section_conf = 0.0
            
            # Look for diameter symbol (Ø) and section keywords
            for (bbox, text, conf) in results:
                text_lower = text.lower()
                
                # Check for diameter symbol
                if 'ø' in text_lower or 'Ø' in text or 'dia' in text_lower or 'diameter' in text_lower:
                    dia_text_conf = max(dia_text_conf, conf)
                
                # Check for section view indicators
                if 'section' in text_lower or 'view' in text_lower or 'a-a' in text_lower:
                    section_conf = max(section_conf, conf)
            
            return dia_text_conf, section_conf
        except Exception:
            # If OCR fails, return zero confidence
            return 0.0, 0.0
    
    def analyze_profile_shape(self, crop: np.ndarray, axis_info: Optional[Dict]) -> float:
        """Analyze if the profile looks like a turned part (steps, contours, etc.)."""
        if axis_info is None:
            return 0.0
        
        gray = self.preprocess_image(crop)
        h, w = gray.shape
        # Enhanced edge detection with multiple thresholds
        edges1 = cv2.Canny(gray, 20, 60)   # Very sensitive
        edges2 = cv2.Canny(gray, 50, 150)  # Standard
        edges = cv2.bitwise_or(edges1, edges2)
        # Use RETR_TREE to capture nested contours (holes, threads)
        contours, hierarchy = cv2.findContours(edges, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        
        if len(contours) == 0:
            return 0.3  # Base score even without contours (assume it's a view)
        
        largest_contour = max(contours, key=cv2.contourArea)
        angle = axis_info["angle"]
        angle_abs = abs(angle)
        profile_score = 0.3  # Base score for having a detected view
        
        if 75 < angle_abs < 105:  # Vertical axis
            horizontal_lines = cv2.HoughLinesP(edges, rho=1, theta=np.pi/180, threshold=15, minLineLength=int(w*0.15), maxLineGap=10)
            if horizontal_lines is not None and len(horizontal_lines) > 0:
                profile_score += min(0.3, len(horizontal_lines) * 0.08)
        
        x, y, bw, bh = cv2.boundingRect(largest_contour)
        bbox_area = bw * bh
        contour_area = cv2.contourArea(largest_contour)
        if bbox_area > 0:
            fill_ratio = contour_area / bbox_area
            if fill_ratio > 0.2:  # Lower threshold
                profile_score += 0.2
        
        vertical_lines = cv2.HoughLinesP(edges, rho=1, theta=np.pi/180, threshold=15, minLineLength=int(h*0.15), maxLineGap=10)
        if vertical_lines is not None and len(vertical_lines) > 0:
            profile_score += min(0.2, len(vertical_lines) * 0.05)
        
        if h > 0 and w > 0:
            aspect_ratio = h / w
            if 1.0 < aspect_ratio < 6.0:  # Wider range
                profile_score += 0.2
        
        return min(1.0, max(0.0, profile_score))
    
    def create_axis_overlay(self, crop: np.ndarray, axis_info: Optional[Dict]) -> np.ndarray:
        """Create debug image with axis overlay.
        
        Args:
            crop: Cropped view image
            axis_info: Axis information
            
        Returns:
            Image with axis line drawn
        """
        overlay = crop.copy()
        
        if axis_info is not None:
            x1, y1, x2, y2 = axis_info["line"]
            cv2.line(overlay, (x1, y1), (x2, y2), (0, 255, 0), 2)
            # Draw endpoints
            cv2.circle(overlay, (x1, y1), 5, (0, 255, 0), -1)
            cv2.circle(overlay, (x2, y2), 5, (0, 255, 0), -1)
        
        return overlay
    
    def create_symmetry_overlay(self, crop: np.ndarray, axis_info: Optional[Dict]) -> np.ndarray:
        """Create debug image with symmetry visualization.
        
        Args:
            crop: Cropped view image
            axis_info: Axis information
            
        Returns:
            Image with symmetry visualization
        """
        overlay = crop.copy()
        
        if axis_info is not None:
            x1, y1, x2, y2 = axis_info["line"]
            # Draw axis line
            cv2.line(overlay, (x1, y1), (x2, y2), (255, 0, 0), 2)
            
            # Draw mirrored region (simplified visualization)
            h, w = crop.shape[:2]
            angle = axis_info["angle"]
            angle_abs = abs(angle)
            
            if 75 < angle_abs < 105:  # Vertical axis
                mid_x = w // 2
                cv2.line(overlay, (mid_x, 0), (mid_x, h), (0, 0, 255), 1)
        
        return overlay
    
    def auto_detect_turned_view(self, job_id: str) -> Dict:
        """Auto-detect turned part view from PDF pages.
        
        Args:
            job_id: Job identifier
            
        Returns:
            Dictionary with ranked views, best view, and debug artifacts
        """
        outputs_path = self.file_storage.get_outputs_path(job_id)
        pages_dir = outputs_path / "pdf_pages"
        views_dir = outputs_path / "pdf_views"
        debug_dir = outputs_path / "pdf_auto_detect_debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        
        if not pages_dir.exists():
            raise FileNotFoundError(f"PDF pages not found for job {job_id}")
        
        if not views_dir.exists():
            raise FileNotFoundError(f"Detected views not found for job {job_id}. Run detect_views first.")
        
        # Load all page images and views
        page_files = sorted(pages_dir.glob("page_*.png"))
        all_ranked_views = []
        
        for page_file in page_files:
            page_num = int(page_file.stem.split("_")[1])
            
            # Load page image
            page_img = cv2.imread(str(page_file))
            if page_img is None:
                continue
            
            # Load views for this page
            views_file = views_dir / f"page_{page_num}_views.json"
            if not views_file.exists():
                continue
            
            with open(views_file, 'r') as f:
                page_views_data = json.load(f)
            
            views = page_views_data.get("views", [])
            image_size = page_views_data.get("image_size", [page_img.shape[1], page_img.shape[0]])
            
            # Process each view
            for view_idx, view in enumerate(views):
                bbox_pixels = view.get("bbox_pixels", [])
                if len(bbox_pixels) != 4:
                    continue
                
                # Crop view
                crop = self.crop_view(page_img, bbox_pixels)
                if crop.size == 0:
                    continue
                
                # Detect axis
                axis_info, axis_conf = self.detect_axis_candidate(crop)
                
                # Compute symmetry
                sym_conf = self.compute_symmetry_score(crop, axis_info)
                
                # Detect text hints (optional)
                dia_text_conf, section_conf = self.detect_text_hints(crop)
                
                # Add profile shape analysis
                profile_score = self.analyze_profile_shape(crop, axis_info)
                
                # Combine confidence scores (weighted)
                # axis_conf: 35%, sym_conf: 35%, profile: 20%, text hints: 10% (5% each)
                view_conf = (
                    axis_conf * 0.35 +
                    sym_conf * 0.35 +
                    profile_score * 0.20 +
                    dia_text_conf * 0.05 +
                    section_conf * 0.05
                )
                
                # Debug logging
                print(f"[AutoDetect] View {view_idx} scores: axis={axis_conf:.3f}, sym={sym_conf:.3f}, profile={profile_score:.3f}, dia_text={dia_text_conf:.3f}, section={section_conf:.3f}, view_conf={view_conf:.3f}")
                
                # Create debug overlays
                axis_overlay = self.create_axis_overlay(crop, axis_info)
                sym_overlay = self.create_symmetry_overlay(crop, axis_info)
                
                # Save debug images
                axis_overlay_path = debug_dir / f"page_{page_num}_view_{view_idx}_axis.png"
                sym_overlay_path = debug_dir / f"page_{page_num}_view_{view_idx}_symmetry.png"
                cv2.imwrite(str(axis_overlay_path), axis_overlay)
                cv2.imwrite(str(sym_overlay_path), sym_overlay)
                
                # Store ranked view
                ranked_view = {
                    "page": page_num,
                    "view_index": view_idx,
                    "bbox": view.get("bbox"),
                    "bbox_pixels": bbox_pixels,
                    "scores": {
                        "axis_conf": float(axis_conf),
                        "sym_conf": float(sym_conf),
                        "profile_score": float(profile_score),
                        "dia_text_conf": float(dia_text_conf),
                        "section_conf": float(section_conf),
                        "view_conf": float(view_conf)
                    },
                    "axis_info": axis_info,
                    "debug_artifacts": {
                        "axis_overlay": f"pdf_auto_detect_debug/page_{page_num}_view_{view_idx}_axis.png",
                        "symmetry_overlay": f"pdf_auto_detect_debug/page_{page_num}_view_{view_idx}_symmetry.png"
                    }
                }
                all_ranked_views.append(ranked_view)
        
        # Sort by view_conf (highest first)
        all_ranked_views.sort(key=lambda v: v["scores"]["view_conf"], reverse=True)
        
        # Find best view if confidence >= threshold
        best_view = None
        if all_ranked_views and all_ranked_views[0]["scores"]["view_conf"] >= self.confidence_threshold:
            best_view = all_ranked_views[0]
        
        # Save results
        results_file = outputs_path / "auto_detect_results.json"
        with open(results_file, 'w') as f:
            json.dump({
                "job_id": job_id,
                "ranked_views": all_ranked_views,
                "best_view": best_view,
                "confidence_threshold": self.confidence_threshold,
                "total_views_analyzed": len(all_ranked_views)
            }, f, indent=2)
        
        return {
            "job_id": job_id,
            "ranked_views": all_ranked_views,
            "best_view": best_view,
            "confidence_threshold": self.confidence_threshold,
            "total_views_analyzed": len(all_ranked_views)
        }

