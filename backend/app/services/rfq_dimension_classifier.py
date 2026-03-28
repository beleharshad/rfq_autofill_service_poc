"""
RFQ Dimension Classifier

Deterministic rule-based classifier for selecting machining dimensions from engineering drawings.
Filters out tolerance ranges, metric brackets, and small feature dimensions to identify
the main cylindrical body dimensions (Finish OD, Finish ID, Finish Length).

Rules:
1. Ignore metric bracket values [mm]
2. Ignore tolerance ranges (e.g., 0.723-0.727)
3. Finish OD: Largest OD with Ø symbol, 0.25"-5", belongs to longest segment
4. Finish ID: Largest internal bore < Finish OD, or None if no bore
5. Finish Length: Longest horizontal axial dimension > 0.3", not small shoulder
6. Raw Material OD: max(finish_od + allowance, largest_leftmost_od)
7. Raw Length: finish_length + stock_allowance
"""

import re
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass


@dataclass
class DimensionCandidate:
    """Represents a candidate dimension with metadata."""
    value: float
    text: str
    orientation: str  # "horizontal", "vertical", "unknown"
    position: Optional[Tuple[float, float]] = None
    segment_length: float = 0.0  # Axial coverage for OD/ID, length for Length
    has_diameter_symbol: bool = False
    is_bracketed: bool = False
    is_tolerance: bool = False
    is_small_feature: bool = False
    is_section_view: bool = True  # Assume section view by default
    confidence: float = 0.5


class RFQDimensionClassifier:
    """Rule-based classifier for RFQ machining dimensions."""
    
    def __init__(self):
        """Initialize the classifier."""
        # Valid ranges for dimensions (inches)
        self.min_od_in = 0.25
        self.max_od_in = 5.0
        self.min_id_in = 0.05
        self.max_id_in = 5.0
        self.min_length_in = 0.3
        self.max_length_in = 20.0
        self.small_shoulder_threshold_in = 0.25
        
    def classify_dimensions(
        self,
        dimensions: List[Dict[str, Any]],
        segments: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Optional[float]]:
        """
        Classify dimensions into Finish OD, Finish ID, and Finish Length.
        
        Args:
            dimensions: List of detected dimensions from OCR/parsing
            segments: Optional list of part segments with z_start, z_end, od_diameter, id_diameter
            
        Returns:
            Dictionary with keys: finish_od_in, finish_id_in, finish_len_in
            Values are floats or None (never 0.0 for ID if no bore found)
        """
        # Convert raw dimensions to candidates
        candidates = self._parse_dimensions(dimensions)
        
        # Filter candidates according to rules
        od_candidates = self._filter_od_candidates(candidates)
        id_candidates = self._filter_id_candidates(candidates)
        length_candidates = self._filter_length_candidates(candidates)
        
        # Score and select best candidates
        finish_od = self._select_finish_od(od_candidates, segments)
        finish_id = self._select_finish_id(id_candidates, finish_od)
        finish_len = self._select_finish_length(length_candidates)
        
        return {
            "finish_od_in": finish_od,
            "finish_id_in": finish_id,  # None if no bore, never 0.0
            "finish_len_in": finish_len
        }
    
    def _parse_dimensions(self, dimensions: List[Dict[str, Any]]) -> List[DimensionCandidate]:
        """Parse raw dimensions into DimensionCandidate objects.
        
        For tolerance ranges, extracts the MAX value (conservative approach).
        """
        candidates = []
        
        for dim in dimensions:
            value = dim.get('value')
            if value is None:
                continue
            
            text = dim.get('text', '')
            orientation = dim.get('orientation', 'unknown')
            position = dim.get('position')
            confidence = dim.get('confidence', 0.5)
            
            # Check for diameter symbol
            has_diameter_symbol = bool(
                re.search(r'[Ø∅]|DIA|DIAMETER', text, re.IGNORECASE)
            )
            
            # Check if bracketed (metric)
            is_bracketed = bool(
                re.search(r'\[[\d.]+\]', text) or
                (text.find('[') >= 0 and text.find(']') >= 0)
            )
            
            # Check if tolerance range (e.g., 0.723-0.727, 1.006-1.008, .185-.190)
            tolerance_match = re.search(r'(\d+\.\d+)\s*[-–]\s*(\d+\.\d+)', text) or \
                             re.search(r'\.(\d+)\s*[-–]\s*\.(\d+)', text)  # .185-.190
            
            is_tolerance = tolerance_match is not None
            
            # Extract MAX value from tolerance range
            if is_tolerance and tolerance_match:
                try:
                    if tolerance_match.lastindex == 2:
                        # Full format: 0.723-0.727
                        val1 = float(tolerance_match.group(1))
                        val2 = float(tolerance_match.group(2))
                    else:
                        # Leading decimal: .185-.190
                        val1_str = '0.' + tolerance_match.group(1)
                        val2_str = '0.' + tolerance_match.group(2)
                        val1 = float(val1_str)
                        val2 = float(val2_str)
                    
                    # Use MAX value for OD/ID (conservative), average for length
                    if has_diameter_symbol:
                        value = max(val1, val2)  # MAX for diameters
                    else:
                        value = (val1 + val2) / 2.0  # Average for length
                    
                    # Update text to show we extracted from range
                    text = f"{text} (extracted max: {value:.4f})"
                except (ValueError, IndexError):
                    # If parsing fails, use original value
                    pass
            
            # Check if small feature (for length: < threshold)
            is_small_feature = value < self.small_shoulder_threshold_in
            
            # Get segment length if available
            segment_length = dim.get('segment_length', 0.0)
            
            candidate = DimensionCandidate(
                value=float(value),
                text=text,
                orientation=orientation,
                position=position,
                segment_length=segment_length,
                has_diameter_symbol=has_diameter_symbol,
                is_bracketed=is_bracketed,
                is_tolerance=is_tolerance,
                is_small_feature=is_small_feature,
                confidence=confidence
            )
            
            candidates.append(candidate)
        
        return candidates
    
    def _filter_od_candidates(self, candidates: List[DimensionCandidate]) -> List[DimensionCandidate]:
        """Filter candidates for Finish OD selection.
        
        Note: Tolerance ranges are now parsed to extract MAX value, so we accept them.
        """
        filtered = []
        
        for cand in candidates:
            # Rule 1: Ignore bracketed (metric) values
            if cand.is_bracketed:
                continue
            
            # Rule 2: Tolerance ranges are now parsed to extract MAX value, so accept them
            # (The value has already been converted to MAX in _parse_dimensions)
            
            # Rule 3: Must have diameter symbol
            if not cand.has_diameter_symbol:
                continue
            
            # Rule 4: Value must be in valid range
            if not (self.min_od_in <= cand.value <= self.max_od_in):
                continue
            
            filtered.append(cand)
        
        return filtered
    
    def _filter_id_candidates(self, candidates: List[DimensionCandidate]) -> List[DimensionCandidate]:
        """Filter candidates for Finish ID selection.
        
        Note: Tolerance ranges are now parsed to extract MAX value, so we accept them.
        """
        filtered = []
        
        for cand in candidates:
            # Rule 1: Ignore bracketed (metric) values
            if cand.is_bracketed:
                continue
            
            # Rule 2: Tolerance ranges are now parsed to extract MAX value, so accept them
            # (The value has already been converted to MAX in _parse_dimensions)
            
            # Rule 3: Must have diameter symbol
            if not cand.has_diameter_symbol:
                continue
            
            # Rule 4: Value must be in valid range
            if not (self.min_id_in <= cand.value <= self.max_id_in):
                continue
            
            filtered.append(cand)
        
        return filtered
    
    def _filter_length_candidates(self, candidates: List[DimensionCandidate]) -> List[DimensionCandidate]:
        """Filter candidates for Finish Length selection.
        
        Note: Tolerance ranges are now parsed to extract average value, so we accept them.
        """
        filtered = []
        
        for cand in candidates:
            # Rule 1: Ignore bracketed (metric) values
            if cand.is_bracketed:
                continue
            
            # Rule 2: Tolerance ranges are now parsed to extract average value, so accept them
            # (The value has already been converted to average in _parse_dimensions)
            
            # Rule 3: Must be horizontal (axial) dimension
            if cand.orientation not in ['horizontal', 'unknown']:
                continue
            
            # Rule 4: Must be > minimum length (not small shoulder)
            if cand.value < self.min_length_in:
                continue
            
            # Rule 5: Must be <= maximum length
            if cand.value > self.max_length_in:
                continue
            
            filtered.append(cand)
        
        return filtered
    
    def _select_finish_od(
        self,
        candidates: List[DimensionCandidate],
        segments: Optional[List[Dict[str, Any]]] = None
    ) -> Optional[float]:
        """
        Select Finish OD using scoring system.
        
        Scoring factors:
        - +cylindrical_segment_overlap: Higher if dimension matches longest segment
        - +section_view_presence: Higher if in section view
        - +main_body_size: Prefer dimensions in typical main body range (0.5-2.5")
        - -small_feature_penalty: Penalize very small or very large dimensions
        - +segment_coverage: Prefer dimensions that match segments with high axial coverage
        """
        if not candidates:
            return None
        
        # Calculate total length for coverage calculation
        total_length = 0.0
        if segments:
            for seg in segments:
                seg_len = seg.get('z_end', 0.0) - seg.get('z_start', 0.0)
                total_length += seg_len
        
        # Score each candidate
        scored = []
        for cand in candidates:
            score = 0.0
            
            # Base score from confidence
            score += cand.confidence * 0.25
            
            # Segment overlap bonus (if segments available)
            matching_segment_length = 0.0
            if segments:
                # Find segments with matching OD
                for seg in segments:
                    od_diam = seg.get('od_diameter', 0.0)
                    seg_len = seg.get('z_end', 0.0) - seg.get('z_start', 0.0)
                    
                    # Check if dimension matches this segment's OD (within 0.02" tolerance)
                    if abs(cand.value - od_diam) < 0.02:
                        matching_segment_length += seg_len
                        # Bonus proportional to segment length
                        score += min(seg_len / 10.0, 0.4)  # Max 0.4 bonus per segment
            
            # Coverage bonus: prefer dimensions that match segments with high coverage
            if total_length > 0 and matching_segment_length > 0:
                coverage_ratio = matching_segment_length / total_length
                score += coverage_ratio * 0.3  # Up to 0.3 bonus for high coverage
            
            # Section view bonus (assumed True by default)
            if cand.is_section_view:
                score += 0.15
            
            # Prefer main body dimensions (0.5-2.5 inches typical range)
            # This helps avoid selecting very small features or oversized dimensions
            if 0.5 <= cand.value <= 2.5:
                score += 0.25  # Strong preference for main body range
            elif 0.25 <= cand.value < 0.5:
                score += 0.05  # Small bonus for smaller parts
            elif cand.value > 2.5:
                score -= 0.3  # Strong penalty for very large dimensions (likely wrong)
            elif cand.value < 0.25:
                score -= 0.2  # Penalty for very small dimensions (likely small features)
            
            # Slight penalty for tolerance ranges (prefer explicit single values when available)
            if cand.is_tolerance:
                score -= 0.05
            
            scored.append((score, cand))
        
        # Sort by score descending
        scored.sort(key=lambda x: x[0], reverse=True)
        
        # Return value of highest-scoring candidate
        if scored and scored[0][0] > 0:  # Only return if score is positive
            return scored[0][1].value
        
        return None
    
    def _select_finish_id(
        self,
        candidates: List[DimensionCandidate],
        finish_od: Optional[float]
    ) -> Optional[float]:
        """
        Select Finish ID (largest internal bore).
        
        Returns None if no valid bore found (NOT 0.0).
        """
        if not candidates:
            return None
        
        # Filter: must be smaller than Finish OD
        valid_candidates = []
        for cand in candidates:
            if finish_od is not None and cand.value >= finish_od:
                continue
            
            # Must be significantly smaller (at least 0.02" wall thickness)
            if finish_od is not None and cand.value >= (finish_od - 0.02):
                continue
            
            valid_candidates.append(cand)
        
        if not valid_candidates:
            return None
        
        # Select largest ID (most conservative)
        valid_candidates.sort(key=lambda x: x.value, reverse=True)
        return valid_candidates[0].value
    
    def _select_finish_length(self, candidates: List[DimensionCandidate]) -> Optional[float]:
        """Select Finish Length (longest valid axial dimension).
        
        Prefers dimensions that are:
        - Longer (main body vs small shoulders)
        - Horizontal orientation
        - Not from tolerance ranges (when single values available)
        """
        if not candidates:
            return None
        
        # Score candidates
        scored = []
        for cand in candidates:
            score = 0.0
            
            # Base score from confidence
            score += cand.confidence * 0.25
            
            # Prefer longer dimensions (main body length), but not too long
            if 0.3 <= cand.value <= 5.0:
                score += min(cand.value / 10.0, 0.3)  # Max 0.3 bonus for reasonable lengths
            elif cand.value > 5.0:
                score -= 0.2  # Penalty for very long dimensions (likely wrong)
            
            # Horizontal orientation bonus
            if cand.orientation == 'horizontal':
                score += 0.2
            
            # Slight penalty for tolerance ranges (prefer explicit single values)
            if cand.is_tolerance:
                score -= 0.05
            
            scored.append((score, cand))
        
        # Sort by score descending, then by value descending (prefer longer)
        scored.sort(key=lambda x: (x[0], x[1].value), reverse=True)
        
        # Return value of highest-scoring candidate
        if scored and scored[0][0] > 0:  # Only return if score is positive
            return scored[0][1].value
        
        return None
    
    def compute_raw_dimensions(
        self,
        finish_od: Optional[float],
        finish_len: Optional[float],
        rm_od_allowance_in: float = 0.26,
        rm_len_allowance_in: float = 0.35,
        segments: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Optional[float]]:
        """
        Compute Raw Material dimensions from finish dimensions.
        
        Rules:
        - Raw OD = max(finish_od + allowance, largest_leftmost_od)
        - Raw Length = finish_length + stock_allowance
        """
        raw_od = None
        raw_len = None
        
        # Raw Length: finish_length + allowance
        if finish_len is not None:
            raw_len = finish_len + rm_len_allowance_in
        
        # Raw OD: finish_od + allowance, or largest leftmost OD
        if finish_od is not None:
            raw_od_from_allowance = finish_od + rm_od_allowance_in
            
            # Check for largest leftmost OD in segments
            largest_leftmost_od = None
            if segments:
                # Find leftmost segment (minimum z_start)
                leftmost_seg = min(segments, key=lambda s: s.get('z_start', float('inf')))
                largest_leftmost_od = leftmost_seg.get('od_diameter')
            
            if largest_leftmost_od is not None:
                raw_od = max(raw_od_from_allowance, largest_leftmost_od)
            else:
                raw_od = raw_od_from_allowance
        
        return {
            "rm_od_in": raw_od,
            "rm_len_in": raw_len
        }


def classify_dimensions_from_text(
    text: str,
    segments: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Optional[float]]:
    """
    Convenience function to classify dimensions from OCR text.
    
    Args:
        text: OCR text string
        segments: Optional part segments
        
    Returns:
        Dictionary with finish_od_in, finish_id_in, finish_len_in
    """
    classifier = RFQDimensionClassifier()
    
    # Extract dimensions from text
    dimensions = _extract_dimensions_from_text(text)
    
    return classifier.classify_dimensions(dimensions, segments)


def _extract_dimensions_from_text(text: str) -> List[Dict[str, Any]]:
    """Extract dimension candidates from text."""
    dimensions = []
    
    # Pattern for dimensions with diameter symbol
    dia_pattern = re.compile(
        r'[Ø∅]?\s*(\d+\.\d+)\s*(?:\[[\d.]+\])?\s*(?:[-–]\s*\d+\.\d+)?',
        re.IGNORECASE
    )
    
    # Pattern for length dimensions
    length_pattern = re.compile(
        r'(\d+\.\d+)\s*(?:\[[\d.]+\])?\s*(?:[-–]\s*\d+\.\d+)?',
        re.IGNORECASE
    )
    
    # Extract diameter dimensions
    for match in dia_pattern.finditer(text):
        value_str = match.group(1)
        try:
            value = float(value_str)
            # Check if bracketed or tolerance
            full_match = match.group(0)
            is_bracketed = '[' in full_match and ']' in full_match
            is_tolerance = bool(re.search(r'\d+\.\d+\s*[-–]\s*\d+\.\d+', full_match))
            
            dimensions.append({
                'value': value,
                'text': full_match,
                'orientation': 'vertical',  # Diameters are typically vertical
                'has_diameter_symbol': True,
                'is_bracketed': is_bracketed,
                'is_tolerance': is_tolerance,
                'confidence': 0.7
            })
        except ValueError:
            continue
    
    # Extract length dimensions (horizontal)
    for match in length_pattern.finditer(text):
        value_str = match.group(1)
        try:
            value = float(value_str)
            full_match = match.group(0)
            is_bracketed = '[' in full_match and ']' in full_match
            is_tolerance = bool(re.search(r'\d+\.\d+\s*[-–]\s*\d+\.\d+', full_match))
            
            # Skip if already captured as diameter
            if any(abs(d['value'] - value) < 0.001 for d in dimensions):
                continue
            
            dimensions.append({
                'value': value,
                'text': full_match,
                'orientation': 'horizontal',
                'has_diameter_symbol': False,
                'is_bracketed': is_bracketed,
                'is_tolerance': is_tolerance,
                'confidence': 0.6
            })
        except ValueError:
            continue
    
    return dimensions
