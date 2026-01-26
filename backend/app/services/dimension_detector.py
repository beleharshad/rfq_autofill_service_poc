"""Service for detecting dimension annotations in engineering drawings."""

import re
import cv2
import numpy as np
from typing import List, Dict, Tuple, Optional
import logging

logger = logging.getLogger(__name__)

import os

# Optional EasyOCR import
try:
    # Workaround for Windows OpenMP runtime conflicts (torch/numpy).
    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
    import easyocr
    _EASYOCR_AVAILABLE = True
except Exception:
    _EASYOCR_AVAILABLE = False
    easyocr = None


class DimensionDetector:
    """Detects dimension annotations in engineering drawings using OCR."""
    
    def __init__(self):
        """Initialize dimension detector."""
        self.easyocr_reader = None
        if _EASYOCR_AVAILABLE:
            try:
                self.easyocr_reader = easyocr.Reader(['en'], gpu=False)
            except Exception as e:
                logger.warning(f"Failed to initialize EasyOCR: {e}")
                self.easyocr_reader = None
    
    def extract_dimensions_from_text(self, text: str) -> List[Dict[str, float]]:
        """Extract dimension values from OCR text.
        
        Args:
            text: OCR text string
            
        Returns:
            List of dimension dictionaries with 'value' and 'unit' keys
        """
        dimensions = []
        
        # Pattern for inches: "1.245", "1.245\"", "1.245 in", "1-1/4", etc.
        # Pattern for metric (bracketed): "[31.6]", "[31.6mm]", etc.
        inch_patterns = [
            r'(\d+\.\d+)\s*["\']',  # 1.245"
            r'(\d+\.\d+)\s*in',      # 1.245 in
            r'(\d+\.\d+)',           # 1.245 (standalone)
            r'(\d+)[-–](\d+)/(\d+)', # 1-1/4 (fractional)
        ]
        
        # Extract inch dimensions (ignore bracketed metric)
        for pattern in inch_patterns:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                if pattern == r'(\d+)[-–](\d+)/(\d+)':
                    # Fractional inches
                    whole = float(match.group(1))
                    num = float(match.group(2))
                    den = float(match.group(3))
                    value = whole + (num / den)
                else:
                    value = float(match.group(1))
                
                # Check if this is bracketed (metric) - skip it
                start_pos = match.start()
                end_pos = match.end()
                # Look for brackets around the match
                text_before = text[max(0, start_pos-10):start_pos]
                text_after = text[end_pos:min(len(text), end_pos+10)]
                if '[' in text_before and ']' in text_after:
                    continue  # Skip bracketed metric values
                
                dimensions.append({
                    'value': value,
                    'unit': 'in',
                    'text': match.group(0)
                })
        
        return dimensions
    
    def detect_dimensions_in_view(
        self, 
        image: np.ndarray,
        view_bbox: Tuple[int, int, int, int]
    ) -> List[Dict]:
        """Detect dimension annotations in a view using OCR.
        
        Args:
            image: Full page image
            view_bbox: Bounding box of the view (x, y, width, height)
            
        Returns:
            List of detected dimensions with value, unit, and position
        """
        if not _EASYOCR_AVAILABLE or self.easyocr_reader is None:
            logger.warning("EasyOCR not available, skipping dimension detection")
            return []
        
        x, y, w, h = view_bbox
        # Expand bbox slightly to include nearby dimension text
        margin = 50
        x_start = max(0, x - margin)
        y_start = max(0, y - margin)
        x_end = min(image.shape[1], x + w + margin)
        y_end = min(image.shape[0], y + h + margin)
        
        crop = image[y_start:y_end, x_start:x_end]
        
        if crop.size == 0:
            return []
        
        try:
            # Run OCR
            results = self.easyocr_reader.readtext(crop)
            
            dimensions = []
            for (bbox, text, confidence) in results:
                if confidence < 0.5:  # Low confidence, skip
                    continue
                
                # Extract dimensions from text
                extracted = self.extract_dimensions_from_text(text)
                for dim in extracted:
                    # Calculate position relative to view
                    bbox_center_x = np.mean([p[0] for p in bbox]) + x_start
                    bbox_center_y = np.mean([p[1] for p in bbox]) + y_start
                    
                    dimensions.append({
                        'value': dim['value'],
                        'unit': dim['unit'],
                        'text': dim['text'],
                        'position': (bbox_center_x, bbox_center_y),
                        'confidence': confidence
                    })
            
            return dimensions
        except Exception as e:
            logger.warning(f"Error during OCR dimension detection: {e}")
            return []
    
    def find_anchor_dimension(
        self,
        dimensions: List[Dict],
        normalized_image: np.ndarray,
        od_data: Dict,
        preferred_ranges: Optional[List[Tuple[float, float]]] = None
    ) -> Optional[Dict]:
        """Find the best anchor dimension for scale calibration.
        
        Args:
            dimensions: List of detected dimensions
            normalized_image: Normalized view image
            od_data: OD radius data with axial_positions
            preferred_ranges: List of (min, max) ranges for preferred dimensions
                Default: [(1.245, 1.255), (1.654, 1.660)] for length and OD
        
        Returns:
            Anchor dimension dict with 'name', 'value', 'pixel_length', 'inch_per_pixel'
            or None if no suitable anchor found
        """
        if not dimensions:
            return None
        
        if preferred_ranges is None:
            # Default: prefer overall length 1.245-1.255 or OD 1.654-1.660
            preferred_ranges = [(1.245, 1.255), (1.654, 1.660)]
        
        h, w = normalized_image.shape[:2]
        
        # Calculate measured pixel dimensions from the view
        if not od_data.get("axial_positions"):
            return None
        
        # Overall length in pixels (from first to last axial position)
        axial_positions = od_data["axial_positions"]
        total_length_pixels = axial_positions[-1] - axial_positions[0] if len(axial_positions) > 1 else h * 0.7
        
        # Max OD radius in pixels
        od_radii = od_data.get("od_radii", [])
        max_od_radius_pixels = np.max(od_radii) if len(od_radii) > 0 else 0.0
        max_od_diameter_pixels = max_od_radius_pixels * 2.0
        
        # Try to match detected dimensions to measured pixel dimensions
        candidates = []
        
        for dim in dimensions:
            value = dim['value']
            unit = dim.get('unit', 'in')
            
            if unit != 'in':
                continue
            
            # Check if this matches overall length
            if len(axial_positions) > 1:
                # Try matching to overall length
                for min_val, max_val in preferred_ranges:
                    if min_val <= value <= max_val:
                        # This could be the overall length
                        inch_per_pixel = value / total_length_pixels if total_length_pixels > 0 else None
                        if inch_per_pixel and 0.0001 < inch_per_pixel < 0.1:  # Reasonable range
                            candidates.append({
                                'name': 'overall_length',
                                'value': value,
                                'pixel_length': total_length_pixels,
                                'inch_per_pixel': inch_per_pixel,
                                'confidence': dim.get('confidence', 0.5),
                                'dimension': dim
                            })
            
            # Check if this matches max OD diameter
            if max_od_diameter_pixels > 0:
                for min_val, max_val in preferred_ranges:
                    if min_val <= value <= max_val:
                        # This could be the max OD
                        inch_per_pixel = value / max_od_diameter_pixels if max_od_diameter_pixels > 0 else None
                        if inch_per_pixel and 0.0001 < inch_per_pixel < 0.1:  # Reasonable range
                            candidates.append({
                                'name': 'max_od_diameter',
                                'value': value,
                                'pixel_length': max_od_diameter_pixels,
                                'inch_per_pixel': inch_per_pixel,
                                'confidence': dim.get('confidence', 0.5),
                                'dimension': dim
                            })
        
        # Sort by confidence and return best match
        if candidates:
            candidates.sort(key=lambda x: x['confidence'], reverse=True)
            return candidates[0]
        
        return None





