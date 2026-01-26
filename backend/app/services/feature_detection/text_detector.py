"""Text-based feature detection using regex patterns."""

import re
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime

try:
    import pdfplumber
    _PDFPLUMBER_AVAILABLE = True
except ImportError:
    _PDFPLUMBER_AVAILABLE = False
    pdfplumber = None

from .schema import (
    DetectedFeatures, HoleFeature, SlotFeature, ChamferFeature,
    FilletFeature, ThreadFeature, create_feature_meta, DetectionResult
)


class TextFeatureDetector:
    """Detector for geometric features from PDF text using regex patterns."""

    # Version tracking - bump when regex patterns change
    DETECTOR_VERSION = "1.0.0"

    def __init__(self):
        """Initialize text feature detector with regex patterns."""
        # Hole detection patterns
        self.hole_patterns = [
            # Drill holes: "Ø0.125 DRILL", "6X Ø0.25 DRILL THRU"
            r'(\d+)\s*[Xx]\s*[Ã˜âˆ…]?\s*([\d.]+)\s*DRILL',
            r'[Ã˜âˆ…]\s*([\d.]+)\s*DRILL',
            # Through holes: "Ø0.5 THRU", "6X Ø0.25 THRU"
            r'(\d+)\s*[Xx]\s*[Ã˜âˆ…]?\s*([\d.]+)\s*THRU',
            r'[Ã˜âˆ…]\s*([\d.]+)\s*THRU',
            # Blind holes: "Ø0.375 DEEP 0.5", "2X Ø0.25 X 0.75 DEEP", "DEPTH 0.5"
            r'(\d+)\s*[Xx]\s*[Ã˜âˆ…]?\s*([\d.]+)\s*[Xx]\s*([\d.]+)\s*(?:DEEP|DEPTH)',
            r'[Ã˜âˆ…]\s*([\d.]+)\s*[Xx]\s*([\d.]+)\s*(?:DEEP|DEPTH)',
            # Countersink: "Ø0.5 CSK", "2X Ø0.25 CSK"
            r'(\d+)\s*[Xx]\s*[Ã˜âˆ…]?\s*([\d.]+)\s*CSK',
            r'[Ã˜âˆ…]\s*([\d.]+)\s*CSK',
        ]

        # Slot/keyway patterns
        self.slot_patterns = [
            # Slots: "SLOT 0.125 X 0.75", "2X SLOT 0.25 X 1.0"
            r'(\d+)\s*[Xx]\s*SLOT\s*([\d.]+)\s*[Xx]\s*([\d.]+)',
            r'SLOT\s*([\d.]+)\s*[Xx]\s*([\d.]+)',
            # Keyways: "KEYWAY 0.187 X 0.5", "KEY 0.125 X 0.75"
            r'(\d+)\s*[Xx]\s*KEY(?:WAY)?\s*([\d.]+)\s*[Xx]\s*([\d.]+)',
            r'KEY(?:WAY)?\s*([\d.]+)\s*[Xx]\s*([\d.]+)',
            # Width/Length: "WIDTH 0.25 LENGTH 1.0", "W0.125 L0.75"
            r'W(?:IDTH)?\s*([\d.]+)\s*L(?:ENGTH)?\s*([\d.]+)',
            # Width/Long: "WIDTH 0.25 LONG 1.0"
            r'W(?:IDTH)?\s*([\d.]+)\s*LONG\s*([\d.]+)',
        ]

        # Chamfer patterns
        self.chamfer_patterns = [
            # "C0.03", "0.03 X 45°", "CHAMFER 0.03 X 45°"
            r'C\s*([\d.]+)',
            r'([\d.]+)\s*[Xx]\s*(\d+)\s*Â°',
            r'CHAMFER\s*([\d.]+)\s*[Xx]\s*(\d+)\s*Â°',
            r'CHAMFER\s*([\d.]+)',
        ]

        # Fillet patterns
        self.fillet_patterns = [
            # "R0.02", "FILLET R0.02", "RADIUS 0.02"
            r'R\s*([\d.]+)',
            r'FILLET\s*R\s*([\d.]+)',
            r'RADIUS\s*([\d.]+)',
        ]

        # Thread patterns
        self.thread_patterns = [
            # "1/4-20 UNC", "M6x1", "1/2-13 NPT"
            r'(\d+(?:\/\d+)?-\d+)\s*(UNC|UNF|NPT|NPS|NPTF)?',
            r'M(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)',
            r'(\d+(?:\/\d+)?-\d+)\s*(LH|RH)?\s*(UNC|UNF|NPT|NPS|NPTF)?',
        ]

    def detect_features(self, pdf_path: Path) -> DetectionResult:
        """
        Detect geometric features from PDF text.

        Args:
            pdf_path: Path to PDF file

        Returns:
            DetectionResult with features and metadata
        """
        if not _PDFPLUMBER_AVAILABLE:
            return DetectionResult(
                success=False,
                error="pdfplumber not available for text extraction"
            )

        if not pdf_path.exists():
            return DetectionResult(
                success=False,
                error=f"PDF file not found: {pdf_path}"
            )

        try:
            # Extract text lines with best-effort bounding boxes per page
            all_text_lines: List[Tuple[int, str, Optional[List[float]]]] = []
            page_count = 0

            with pdfplumber.open(pdf_path) as pdf:
                page_count = len(pdf.pages)
                for page_num, page in enumerate(pdf.pages):
                    try:
                        page_lines = self._extract_page_lines_with_bboxes(page)
                        if page_lines:
                            all_text_lines.extend([(page_num, line_text, line_bbox) for line_text, line_bbox in page_lines])
                        else:
                            # Fallback to raw text lines without bbox
                            text = page.extract_text() or ""
                            lines = [line.strip() for line in text.split('\n') if line.strip()]
                            all_text_lines.extend([(page_num, line, None) for line in lines])
                    except Exception as e:
                        print(f"Warning: Failed to extract text from page {page_num}: {e}")
                        continue

            # Detect features from text
            features = self._detect_features_from_text(all_text_lines)

            return DetectionResult(
                success=True,
                features=features,
                page_count=page_count,
                total_candidates=len(all_text_lines)
            )

        except Exception as e:
            return DetectionResult(
                success=False,
                error=f"Text detection failed: {str(e)}"
            )

    def _detect_features_from_text(self, text_lines: List[Tuple[int, str, Optional[List[float]]]]) -> DetectedFeatures:
        """
        Detect features from extracted text lines.

        Args:
            text_lines: List of (page_num, text_line) tuples

        Returns:
            DetectedFeatures container
        """
        holes = []
        slots = []
        chamfers = []
        fillets = []
        threads = []

        for page_num, line, bbox in text_lines:
            # Detect holes
            hole_features = self._detect_holes(line, page_num, bbox)
            holes.extend(hole_features)

            # Detect slots
            slot_features = self._detect_slots(line, page_num, bbox)
            slots.extend(slot_features)

            # Detect chamfers
            chamfer_features = self._detect_chamfers(line, page_num, bbox)
            chamfers.extend(chamfer_features)

            # Detect fillets
            fillet_features = self._detect_fillets(line, page_num, bbox)
            fillets.extend(fillet_features)

            # Detect threads
            thread_features = self._detect_threads(line, page_num, bbox)
            threads.extend(thread_features)

        # Create metadata
        meta = create_feature_meta(self.DETECTOR_VERSION)

        return DetectedFeatures(
            holes=holes,
            slots=slots,
            chamfers=chamfers,
            fillets=fillets,
            threads=threads,
            meta=meta
        )

    def _detect_holes(self, text: str, page_num: int, bbox: Optional[List[float]]) -> List[HoleFeature]:
        """Detect holes from text."""
        holes = []

        for pattern in self.hole_patterns:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                try:
                    hole = self._parse_hole_match(match, page_num, text, bbox)
                    if hole:
                        holes.append(hole)
                except Exception as e:
                    print(f"Warning: Failed to parse hole match '{match.group()}': {e}")
                    continue

        return holes

    def _parse_hole_match(self, match: re.Match, page_num: int, original_text: str, bbox: Optional[List[float]]) -> Optional[HoleFeature]:
        """Parse a hole regex match into a HoleFeature."""
        groups = match.groups()

        # Extract count (for patterns like "6X Ø0.25")
        count = None
        diameter = None
        depth = None
        kind = "cross"  # Default

        if len(groups) >= 2 and groups[0] and groups[0].isdigit():
            # Pattern: count X diameter
            count = int(groups[0])
            diameter = float(groups[1])
        elif len(groups) >= 1:
            # Pattern: diameter only
            diameter = float(groups[0])

        # Check for depth in blind holes
        if ("DEEP" in original_text.upper() or "DEPTH" in original_text.upper()) and len(groups) >= 3:
            depth = float(groups[2])

        pattern = None
        if "EQUALLY SPACED" in original_text.upper() or "EQ SP" in original_text.upper():
            pattern = "equally_spaced"
        elif count and count > 1:
            pattern = f"{count}X"

        # Determine hole kind
        if "AXIAL" in original_text.upper():
            kind = "axial"

        # Determine confidence based on pattern completeness
        confidence = 0.7  # Base confidence
        if count and count > 1:
            confidence += 0.1  # Patterns are more reliable
        if depth:
            confidence += 0.1  # Blind holes with depth are more specific

        if diameter and diameter > 0:
            return HoleFeature(
                confidence=min(confidence, 1.0),
                source_page=page_num,
                source_bbox=bbox,
                diameter=diameter,
                depth=depth,
                kind=kind,
                count=count,
                pattern=pattern,
                notes=f"Extracted from: {original_text.strip()}"
            )

        return None

    def _detect_slots(self, text: str, page_num: int, bbox: Optional[List[float]]) -> List[SlotFeature]:
        """Detect slots from text."""
        slots = []

        for pattern in self.slot_patterns:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                try:
                    slot = self._parse_slot_match(match, page_num, text, bbox)
                    if slot:
                        slots.append(slot)
                except Exception as e:
                    print(f"Warning: Failed to parse slot match '{match.group()}': {e}")
                    continue

        return slots

    def _parse_slot_match(self, match: re.Match, page_num: int, original_text: str, bbox: Optional[List[float]]) -> Optional[SlotFeature]:
        """Parse a slot regex match into a SlotFeature."""
        groups = match.groups()

        count = None
        width = None
        length = None
        orientation = "axial"  # Default

        if len(groups) >= 3 and groups[0] and groups[0].isdigit():
            # Pattern: count X width X length
            count = int(groups[0])
            width = float(groups[1])
            length = float(groups[2])
        elif len(groups) >= 2:
            # Pattern: width X length
            width = float(groups[0])
            length = float(groups[1])

        # Determine orientation
        if "RADIAL" in original_text.upper():
            orientation = "radial"
        elif "CIRCUMFERENTIAL" in original_text.upper():
            orientation = "circumferential"

        confidence = 0.6  # Base confidence for slots
        if count and count > 1:
            confidence += 0.1

        pattern = None
        if "EQUALLY SPACED" in original_text.upper() or "EQ SP" in original_text.upper():
            pattern = "equally_spaced"
        elif count and count > 1:
            pattern = f"{count}X"

        if width and length and width > 0 and length > 0:
            return SlotFeature(
                confidence=min(confidence, 1.0),
                source_page=page_num,
                source_bbox=bbox,
                width=width,
                length=length,
                orientation=orientation,
                count=count,
                pattern=pattern,
                notes=f"Extracted from: {original_text.strip()}"
            )

        return None

    def _detect_chamfers(self, text: str, page_num: int, bbox: Optional[List[float]]) -> List[ChamferFeature]:
        """Detect chamfers from text."""
        chamfers = []

        for pattern in self.chamfer_patterns:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                try:
                    chamfer = self._parse_chamfer_match(match, page_num, text, bbox)
                    if chamfer:
                        chamfers.append(chamfer)
                except Exception as e:
                    print(f"Warning: Failed to parse chamfer match '{match.group()}': {e}")
                    continue

        return chamfers

    def _parse_chamfer_match(self, match: re.Match, page_num: int, original_text: str, bbox: Optional[List[float]]) -> Optional[ChamferFeature]:
        """Parse a chamfer regex match into a ChamferFeature."""
        groups = match.groups()

        size = None
        angle = 45.0  # Default chamfer angle

        if len(groups) >= 2:
            size = float(groups[0])
            angle = float(groups[1])
        elif len(groups) >= 1:
            size = float(groups[0])

        if size and size > 0:
            return ChamferFeature(
                confidence=0.65,
                source_page=page_num,
                source_bbox=bbox,
                size=size,
                angle=angle,
                edge_location="unknown",  # Could be enhanced with more context
                notes=f"Extracted from: {original_text.strip()}"
            )

        return None

    def _detect_fillets(self, text: str, page_num: int, bbox: Optional[List[float]]) -> List[FilletFeature]:
        """Detect fillets from text."""
        fillets = []

        for pattern in self.fillet_patterns:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                try:
                    fillet = self._parse_fillet_match(match, page_num, text, bbox)
                    if fillet:
                        fillets.append(fillet)
                except Exception as e:
                    print(f"Warning: Failed to parse fillet match '{match.group()}': {e}")
                    continue

        return fillets

    def _parse_fillet_match(self, match: re.Match, page_num: int, original_text: str, bbox: Optional[List[float]]) -> Optional[FilletFeature]:
        """Parse a fillet regex match into a FilletFeature."""
        groups = match.groups()

        radius = None
        if groups and len(groups) >= 1:
            radius = float(groups[0])

        if radius and radius > 0:
            return FilletFeature(
                confidence=0.6,
                source_page=page_num,
                source_bbox=bbox,
                radius=radius,
                edge_location="unknown",  # Could be enhanced with more context
                notes=f"Extracted from: {original_text.strip()}"
            )

        return None

    def _detect_threads(self, text: str, page_num: int, bbox: Optional[List[float]]) -> List[ThreadFeature]:
        """Detect threads from text."""
        threads = []

        for pattern in self.thread_patterns:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                try:
                    thread = self._parse_thread_match(match, page_num, text, bbox)
                    if thread:
                        threads.append(thread)
                except Exception as e:
                    print(f"Warning: Failed to parse thread match '{match.group()}': {e}")
                    continue

        return threads

    def _parse_thread_match(self, match: re.Match, page_num: int, original_text: str, bbox: Optional[List[float]]) -> Optional[ThreadFeature]:
        """Parse a thread regex match into a ThreadFeature."""
        groups = match.groups()

        designation = None
        kind = "external"  # Default assumption

        if groups and len(groups) >= 1:
            designation = groups[0]

            # Determine if internal or external based on context
            if "TAP" in original_text.upper() or "INTERNAL" in original_text.upper():
                kind = "internal"

        confidence = 0.75  # Threads are usually clearly specified

        if designation:
            return ThreadFeature(
                confidence=confidence,
                source_page=page_num,
                source_bbox=bbox,
                designation=designation,
                kind=kind,
                notes=f"Extracted from: {original_text.strip()}"
            )

        return None

    def _extract_page_lines_with_bboxes(self, page) -> List[Tuple[str, List[float]]]:
        """Extract text lines with merged bounding boxes from a pdfplumber page."""
        words = page.extract_words(x_tolerance=2, y_tolerance=2, keep_blank_chars=False, use_text_flow=True) or []
        if not words:
            return []

        page_width = float(getattr(page, "width", 0.0) or 0.0)
        page_height = float(getattr(page, "height", 0.0) or 0.0)

        lines: List[Tuple[List[Dict[str, Any]], float]] = []
        for w in words:
            if not w.get("text"):
                continue
            top = float(w.get("top", 0.0))
            placed = False
            for line_words, line_top in lines:
                if abs(top - line_top) <= 2.0:
                    line_words.append(w)
                    placed = True
                    break
            if not placed:
                lines.append(([w], top))

        result: List[Tuple[str, List[float]]] = []
        for line_words, _ in lines:
            line_words.sort(key=lambda x: float(x.get("x0", 0.0)))
            text = " ".join(w.get("text", "").strip() for w in line_words if w.get("text"))
            if not text.strip():
                continue
            x0 = min(float(w.get("x0", 0.0)) for w in line_words)
            x1 = max(float(w.get("x1", 0.0)) for w in line_words)
            top = min(float(w.get("top", 0.0)) for w in line_words)
            bottom = max(float(w.get("bottom", 0.0)) for w in line_words)
            if page_width > 0 and page_height > 0:
                result.append(
                    (
                        text.strip(),
                        [x0 / page_width, top / page_height, x1 / page_width, bottom / page_height],
                    )
                )
            else:
                result.append((text.strip(), [x0, top, x1, bottom]))

        return result