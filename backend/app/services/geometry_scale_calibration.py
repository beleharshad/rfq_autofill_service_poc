"""
Geometry Scale Calibration Service

Automatically calibrates geometry scale by matching OCR-extracted dimensions
with geometry segments. Rescales all geometry when a reliable scale factor is found.

Algorithm:
1. Extract OCR diameter annotations (Ø, DIA, OD) from PDF
2. Collect candidate geometry ODs from segments (length > 5% total, confidence > 0.6)
3. Match OCR diameters to geometry ODs (closest match)
4. Calculate ratios = OCR / geometry
5. If 2+ ratios cluster within ±8%, use median as scale_factor
6. Apply scaling to all geometry (segments, z_range, totals)
7. Update scale_report.method = "calibrated_from_ocr"
"""

import re
import logging
from typing import Dict, List, Optional, Tuple, Any
from statistics import median

from app.services.pdf_spec_extractor import PDFSpecExtractor
from app.services.vendor_quote_extraction_service import VendorQuoteExtractionService

logger = logging.getLogger(__name__)


def _as_float(value: Any) -> Optional[float]:
    """Convert value to float, return None if conversion fails."""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def normalize_diameter_tokens(line: str) -> List[Dict[str, Any]]:
    """
    Extract diameter candidates from a text line with confidence scoring.
    
    Returns list of candidates with 'value', 'text', 'confidence'.
    Only returns candidates with confidence >= 0.3.
    
    Rules:
    - Reject bracketed values: [2.38]
    - Reject tolerance ranges unless explicitly parsed
    - Reject thread specs: UNC, UN-, THREAD, Mx, NPT, BSP
    - Reject radii: R0.5, R.5, RAD
    - Prefer values with Ø, DIA, O.D. in same line
    
    Args:
        line: Text line to parse
        
    Returns:
        List of diameter candidate dicts with value, text, confidence
    """
    candidates = []
    
    # Normalize line
    line_upper = line.upper()
    
    # Reject thread specs
    thread_patterns = [
        r'\bUNC\b', r'\bUN-', r'\bTHREAD', r'\bM\d+', r'\bNPT\b', r'\bBSP\b',
        r'\bUNF\b', r'\bUNEF\b', r'\bUNS\b'
    ]
    if any(re.search(p, line_upper) for p in thread_patterns):
        return []  # Reject entire line if thread spec found
    
    # Reject radii
    radius_patterns = [r'\bR\d+\.?\d*\b', r'\bRAD\b', r'\bRADIUS\b']
    if any(re.search(p, line_upper) for p in radius_patterns):
        return []  # Reject entire line if radius found
    
    # TASK 4: Improved regex patterns to capture common OCR patterns
    # Patterns: Ø0.94, DIA 0.94, 0.94 DIA, D .94, DIA.94, O.D. .94, .94 (leading dot)
    
    # Check for bracketed metric values - reject ONLY if entire line is bracketed
    # Allow lines like "Ø1.006-1.008 [25.553-25.603]" (has brackets but also inch values)
    if re.search(r'^\[.*\]$', line.strip()):
        return []  # Reject if entire line is just brackets
    # Don't reject if brackets are present but line also has non-bracketed content
    
    # Check for tolerance range (e.g., "0.723-0.727") - TASK 5: Allow but mark
    has_tolerance_range = bool(re.search(r'\d+\.\d+\s*[-–]\s*\d+\.\d+', line))
    
    # TASK 4: Improved regex to capture diameters with leading dot decimals
    # Pattern 1: Ø0.94, Ø.94, ∅0.94
    dia_symbol_pattern = re.compile(r'[Ø∅]\s*(\d*\.?\d+)', re.IGNORECASE)
    # Pattern 2: DIA 0.94, DIA.94, DIA .94, D .94
    dia_word_pattern = re.compile(r'\bD(?:IA)?\s*\.?\s*(\d*\.?\d+)', re.IGNORECASE)
    # Pattern 3: O.D. 0.94, OD 0.94, O.D. .94
    od_pattern = re.compile(r'\bO\.?D\.?\s*\.?\s*(\d*\.?\d+)', re.IGNORECASE)
    # Pattern 4: 0.94 DIA, .94 DIA (value before keyword)
    value_dia_pattern = re.compile(r'(\d*\.?\d+)\s+D(?:IA)?', re.IGNORECASE)
    # Pattern 5: Generic number near diameter keywords
    number_pattern = re.compile(r'(\d+\.\d+|\d+|\.\d+)')
    
    # Extract numeric values using improved patterns
    numbers = []
    
    # Try diameter symbol patterns first (highest priority)
    for match in dia_symbol_pattern.finditer(line):
        num_str = match.group(1)
        if num_str:
            numbers.append((num_str, "dia_symbol"))
    
    # Try DIA word patterns
    for match in dia_word_pattern.finditer(line):
        num_str = match.group(1)
        if num_str:
            numbers.append((num_str, "dia_word"))
    
    # Try OD patterns
    for match in od_pattern.finditer(line):
        num_str = match.group(1)
        if num_str:
            numbers.append((num_str, "od"))
    
    # Try value-before-DIA patterns
    for match in value_dia_pattern.finditer(line):
        num_str = match.group(1)
        if num_str:
            numbers.append((num_str, "value_dia"))
    
    # Fallback to generic numbers if no pattern matched
    if not numbers:
        for match in number_pattern.finditer(line):
            num_str = match.group(1)
            if num_str:
                numbers.append((num_str, "generic"))
    
    # Calculate confidence scores
    base_confidence = 0.0
    
    # Positive signals
    if re.search(r'[Ø∅]', line):
        base_confidence += 0.3
    if re.search(r'\bDIA\b', line_upper):
        base_confidence += 0.3
    if re.search(r'\bOD\b|\bO\.D\.', line_upper):
        base_confidence += 0.2
    
    # Negative signals (TASK 5: Relaxed - don't reject tolerance entirely)
    if has_tolerance_range:
        base_confidence -= 0.1  # Further reduced penalty (was -0.2)
    # Only penalize brackets if entire line is brackets (already checked above)
    # Don't penalize lines like "Ø1.006-1.008 [25.553]" which have both inch and metric
    
    # Extract diameter values
    for num_str, pattern_type in numbers:
        try:
            # Handle leading dot decimals (.94 -> 0.94)
            if num_str.startswith('.'):
                num_str = '0' + num_str
            
            value = float(num_str)
            # Only accept reasonable diameter values (0.01" to 10")
            if value < 0.01 or value > 10.0:
                continue
            
            # Boost confidence based on pattern type
            pattern_boost = {
                "dia_symbol": 0.1,
                "dia_word": 0.1,
                "od": 0.1,
                "value_dia": 0.05,
                "generic": 0.0
            }
            final_confidence = base_confidence + pattern_boost.get(pattern_type, 0.0)
            
            # Create candidate
            candidate = {
                "value": value,
                "text": line.strip(),
                "confidence": final_confidence,
                "is_tolerance": has_tolerance_range,
                "pattern_type": pattern_type
            }
            
            # TASK 5: Relaxed threshold - allow tolerance lines
            if candidate["confidence"] >= 0.1:  # Lowered from 0.3
                candidates.append(candidate)
        except ValueError:
            continue
    
    return candidates


class GeometryScaleCalibrationService:
    """Service for calibrating geometry scale from OCR dimensions."""
    
    def __init__(self):
        """Initialize scale calibration service."""
        self.pdf_extractor = PDFSpecExtractor()
        self.vendor_extractor = VendorQuoteExtractionService()
    
    def extract_ocr_diameters(
        self,
        part_summary: Dict[str, Any],
        job_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Extract OCR diameter annotations from PDF with quality filtering.
        
        Priority order:
        1. part_summary.inference_metadata (ocr_diameters_in, ocr_dims, raw_dimensions)
        2. Vendor quote extraction service output
        3. PDFSpecExtractor OCR on PDF file
        
        Args:
            part_summary: Part summary dictionary
            job_id: Optional job ID to load PDF from
            
        Returns:
            List of OCR diameter dictionaries with 'value', 'text', 'confidence'
            Sorted by confidence, top 8 candidates
        """
        ocr_diameters = []
        
        # TASK 1: Debug log - count candidates at each stage
        logger.info(f"[RFQ_SCALE_CALIBRATION] Starting OCR diameter extraction (job_id={job_id})")
        
        # METHOD 1: Check part_summary.inference_metadata FIRST (highest priority)
        inference_meta = part_summary.get("inference_metadata") or {}
        if isinstance(inference_meta, dict):
            # Check for ocr_diameters_in or ocr_dims
            ocr_diams_list = inference_meta.get("ocr_diameters_in") or inference_meta.get("ocr_dims") or []
            if isinstance(ocr_diams_list, list) and ocr_diams_list:
                logger.info(f"[RFQ_SCALE_CALIBRATION] Found {len(ocr_diams_list)} OCR diameters in inference_metadata")
                for idx, dia in enumerate(ocr_diams_list[:10]):  # Log first 10
                    if isinstance(dia, dict):
                        value = dia.get("value") or dia.get("diameter")
                        text = dia.get("text", "") or dia.get("label", "")
                        if value:
                            ocr_diameters.append({
                                "value": float(value),
                                "text": text or f"OCR diameter {value}",
                                "confidence": float(dia.get("confidence", 0.7)),
                                "unit": "in",
                                "source": "inference_metadata"
                            })
                            logger.info(f"  [{idx}] inference_metadata: {value:.4f} in - {text}")
            
            # Also check raw_dimensions
            raw_dimensions = inference_meta.get("raw_dimensions") or []
            if isinstance(raw_dimensions, list) and raw_dimensions:
                logger.info(f"[RFQ_SCALE_CALIBRATION] Found {len(raw_dimensions)} raw dimensions in inference_metadata")
                for idx, dim in enumerate(raw_dimensions[:20]):  # Log first 20 for debugging
                    if isinstance(dim, dict):
                        text = dim.get("text", "")
                        value = dim.get("value")
                        unit = dim.get("unit", "in")
                        
                        # DEBUG: Log ALL raw dimensions to see what we're working with
                        logger.debug(f"  [{idx}] raw_dimension: text='{text[:80]}', value={value}, unit={unit}")
                        
                        # CRITICAL: Check for tolerance range FIRST, even if value field exists
                        # The value field might only contain the first number (1.006) but text has "1.006-1.008"
                        is_bracketed = bool(re.search(r'\[[\d.]+\]', text))
                        is_tolerance = bool(re.search(r'\d+\.\d+\s*[-–]\s*\d+\.\d+', text))
                        
                        # Parse tolerance range from text if present
                        value_in = None
                        if is_tolerance:
                            tolerance_match = re.search(r'(\d+\.\d+)\s*[-–]\s*(\d+\.\d+)', text)
                            if tolerance_match:
                                val1 = float(tolerance_match.group(1))
                                val2 = float(tolerance_match.group(2))
                                # Use MAX value (conservative sizing)
                                value_in = max(val1, val2)
                                logger.info(f"  [{idx}] ✓ Parsed tolerance range from text: {val1:.4f}-{val2:.4f}, using MAX={value_in:.4f} in")
                        
                        # If no tolerance range found, use value field
                        if value_in is None and value is not None:
                            # Convert to inches if needed
                            if unit == "mm":
                                value_in = value / 25.4
                            else:
                                value_in = value
                        
                        # Check for diameter symbols or patterns (RELAXED: also check if value looks like diameter)
                        has_dia_symbol = bool(
                            re.search(r'[Ø∅]|DIA|DIAMETER|OD|O\.D\.', text, re.IGNORECASE)
                        )
                        
                        # RELAXED: If we have a tolerance range parsed and value is in diameter range (0.1-10"), 
                        # consider it a diameter even without explicit symbol
                        looks_like_diameter = False
                        if value_in is not None and 0.1 <= value_in <= 10.0:
                            # Check if text contains dimension-like patterns
                            if re.search(r'\d+\.\d+', text):
                                looks_like_diameter = True
                        
                        # Process if we have diameter symbol OR it looks like a diameter
                        if (has_dia_symbol or looks_like_diameter) and value_in is not None and value_in > 0:
                            if not is_bracketed and 0.01 <= value_in <= 10.0:
                                # Check if already added
                                if not any(abs(d["value"] - value_in) < 0.001 for d in ocr_diameters):
                                    candidates = normalize_diameter_tokens(text)
                                    if candidates:
                                        best_cand = max(candidates, key=lambda x: x["confidence"])
                                        ocr_diameters.append({
                                            "value": float(value_in),
                                            "text": text,
                                            "confidence": max(float(dim.get("confidence", 0.7)), best_cand["confidence"]),
                                            "unit": "in",
                                            "source": "inference_metadata.raw_dimensions",
                                            "is_tolerance": is_tolerance
                                        })
                                        logger.info(f"  [{idx}] ✓ Added: {value_in:.4f} in - '{text[:60]}' (has_symbol={has_dia_symbol}, tolerance={is_tolerance})")
                                    else:
                                        # Even if normalize_diameter_tokens fails, add it if we parsed tolerance range OR have diameter symbol
                                        if is_tolerance or has_dia_symbol:
                                            ocr_diameters.append({
                                                "value": float(value_in),
                                                "text": text,
                                                "confidence": float(dim.get("confidence", 0.7)),
                                                "unit": "in",
                                                "source": "inference_metadata.raw_dimensions",
                                                "is_tolerance": is_tolerance
                                            })
                                            logger.info(f"  [{idx}] ✓ Added (fallback): {value_in:.4f} in - '{text[:60]}' (tolerance={is_tolerance}, has_symbol={has_dia_symbol})")
                                        else:
                                            logger.debug(f"  [{idx}] ✗ Skipped: {value_in:.4f} in - '{text[:60]}' (no symbol, not tolerance, normalize failed)")
                            else:
                                logger.debug(f"  [{idx}] ✗ Skipped: bracketed={is_bracketed}, value={value_in} (out of range)")
                        else:
                            logger.debug(f"  [{idx}] ✗ Skipped: has_symbol={has_dia_symbol}, looks_like_dia={looks_like_diameter}, value={value_in}")
        
        logger.info(f"[RFQ_SCALE_CALIBRATION] After inference_metadata: {len(ocr_diameters)} candidates")
        
        # METHOD 2: Vendor quote extraction service (if not enough candidates)
        if len(ocr_diameters) < 2 and job_id:
            try:
                vendor_result = self.vendor_extractor.extract_from_job(job_id)
                if vendor_result.get("success"):
                    pdf_hint = vendor_result.get("pdf_hint", {})
                    if isinstance(pdf_hint, dict):
                        od_val = pdf_hint.get("finish_od_in")
                        id_val = pdf_hint.get("finish_id_in")
                        
                        logger.info(f"[RFQ_SCALE_CALIBRATION] Vendor quote extraction: od={od_val}, id={id_val}")
                        
                        if od_val:
                            od_text = str(od_val)
                            # CRITICAL: Parse tolerance ranges and extract MAX value
                            # Example: "1.006-1.008" -> extract 1.008
                            tolerance_match = re.search(r'(\d+\.\d+)\s*[-–]\s*(\d+\.\d+)', od_text)
                            if tolerance_match:
                                val1 = float(tolerance_match.group(1))
                                val2 = float(tolerance_match.group(2))
                                od_float = max(val1, val2)  # Use MAX value (conservative)
                                logger.info(f"  Parsed vendor OD tolerance range: {val1:.4f}-{val2:.4f}, using MAX={od_float:.4f} in")
                            else:
                                if isinstance(od_val, str):
                                    od_float = _as_float(od_val)
                                else:
                                    od_float = _as_float(od_val)
                            
                            if od_float and 0.01 <= od_float <= 10.0:
                                # Relaxed filtering - allow tolerance but check brackets
                                is_bracketed = bool(re.search(r'\[', od_text))
                                if not is_bracketed:
                                    if not any(abs(d["value"] - od_float) < 0.001 for d in ocr_diameters):
                                        ocr_diameters.append({
                                            "value": od_float,
                                            "text": f"OD {od_text}",
                                            "confidence": 0.75,
                                            "unit": "in",
                                            "source": "vendor_quote_extraction"
                                        })
                                        logger.info(f"  Added vendor OD: {od_float:.4f} in")
                        
                        if id_val:
                            if isinstance(id_val, str):
                                id_float = _as_float(id_val)
                            else:
                                id_float = _as_float(id_val)
                            
                            if id_float and id_float > 0 and 0.01 <= id_float <= 10.0:
                                is_bracketed = isinstance(id_val, str) and bool(re.search(r'\[', id_val))
                                if not is_bracketed:
                                    if not any(abs(d["value"] - id_float) < 0.001 for d in ocr_diameters):
                                        ocr_diameters.append({
                                            "value": id_float,
                                            "text": f"ID {id_val}",
                                            "confidence": 0.75,
                                            "unit": "in",
                                            "source": "vendor_quote_extraction"
                                        })
                                        logger.info(f"  Added vendor ID: {id_float:.4f} in")
            except Exception as e:
                logger.debug(f"Vendor quote extraction failed: {e}")
        
        logger.info(f"[RFQ_SCALE_CALIBRATION] After vendor quote extraction: {len(ocr_diameters)} candidates")
        
        # METHOD 3: PDFSpecExtractor OCR on PDF file (LAST resort)
        # NOTE: Only try PDF extraction if raw_dimensions field doesn't exist (OCR not attempted yet)
        # If raw_dimensions exists but is empty, skip PDF extraction (OCR was attempted but found nothing)
        # PDF extraction can take 30+ seconds and blocks the entire autofill endpoint
        raw_dimensions_exists = "raw_dimensions" in inference_meta
        raw_dimensions_empty = not bool(inference_meta.get("raw_dimensions"))
        # Only try PDF extraction if field doesn't exist (OCR not attempted) OR if we have < 2 candidates and field is empty
        should_try_pdf = not raw_dimensions_exists or (raw_dimensions_empty and len(ocr_diameters) < 2)
        if len(ocr_diameters) < 2 and job_id and should_try_pdf:
            try:
                from app.storage.file_storage import FileStorage
                fs = FileStorage()
                job_path = fs.get_inputs_path(job_id)
                
                # Find PDF file
                if not job_path.exists():
                    logger.warning(f"[RFQ_SCALE_CALIBRATION] Job inputs path does not exist: {job_path}")
                else:
                    pdf_files = list(job_path.glob("*.pdf"))
                    if not pdf_files:
                        logger.warning(f"[RFQ_SCALE_CALIBRATION] No PDF available for OCR in {job_path}")
                    else:
                        pdf_path = pdf_files[0]
                        logger.info(f"[RFQ_SCALE_CALIBRATION] Attempting PDF OCR from: {pdf_path.name}")
                        
                        # Extract using PDFSpecExtractor
                        extract_result = self.pdf_extractor.extract_from_file(str(pdf_path))
                        if extract_result.get("success"):
                            specs = extract_result.get("extracted_specs", {})
                            logger.info(f"[RFQ_SCALE_CALIBRATION] PDFSpecExtractor returned specs keys: {list(specs.keys())}")
                            logger.info(f"[RFQ_SCALE_CALIBRATION] PDFSpecExtractor finish_od_in: {specs.get('finish_od_in')}")
                            logger.info(f"[RFQ_SCALE_CALIBRATION] PDFSpecExtractor finish_id_in: {specs.get('finish_id_in')}")
                            logger.info(f"[RFQ_SCALE_CALIBRATION] PDFSpecExtractor finish_len_in: {specs.get('finish_len_in')}")
                            
                            # Extract OD dimensions with relaxed filtering
                            finish_od = specs.get("finish_od_in")
                            if finish_od:
                                od_text = str(finish_od)
                                # CRITICAL: Parse tolerance ranges and extract MAX value
                                # Example: "1.006-1.008" -> extract 1.008
                                tolerance_match = re.search(r'(\d+\.\d+)\s*[-–]\s*(\d+\.\d+)', od_text)
                                if tolerance_match:
                                    val1 = float(tolerance_match.group(1))
                                    val2 = float(tolerance_match.group(2))
                                    od_val = max(val1, val2)  # Use MAX value (conservative)
                                    logger.info(f"  Parsed PDFSpec OD tolerance range: {val1:.4f}-{val2:.4f}, using MAX={od_val:.4f} in")
                                else:
                                    od_val = _as_float(finish_od)
                                
                                # Check if value is likely in wrong units (too large for inches)
                                # If > 10, might be in mm - convert: value / 25.4
                                if od_val and od_val > 10.0:
                                    od_val_mm = od_val / 25.4
                                    logger.info(f"  PDFSpec OD {od_val:.2f} seems too large for inches, converting from mm: {od_val_mm:.4f} in")
                                    od_val = od_val_mm
                                
                                if od_val and 0.01 <= od_val <= 10.0:
                                    is_bracketed = bool(re.search(r'\[', od_text))
                                    if not is_bracketed:
                                        if not any(abs(d["value"] - od_val) < 0.001 for d in ocr_diameters):
                                            ocr_diameters.append({
                                                "value": od_val,
                                                "text": f"FINISH OD {od_text}",
                                                "confidence": 0.8,
                                                "unit": "in",
                                                "source": "pdf_spec_extractor"
                                            })
                                            logger.info(f"  Added PDFSpec OD: {od_val:.4f} in")
                                else:
                                    logger.warning(f"  PDFSpec OD {od_val} rejected: outside valid range [0.01, 10.0] inches")
                            
                            finish_id = specs.get("finish_id_in")
                            if finish_id:
                                id_val = _as_float(finish_id)
                                if id_val and id_val > 0 and 0.01 <= id_val <= 10.0:
                                    id_text = str(finish_id)
                                    is_bracketed = bool(re.search(r'\[', id_text))
                                    if not is_bracketed:
                                        if not any(abs(d["value"] - id_val) < 0.001 for d in ocr_diameters):
                                            ocr_diameters.append({
                                                "value": id_val,
                                                "text": f"FINISH ID {id_val}",
                                                "confidence": 0.8,
                                                "unit": "in",
                                                "source": "pdf_spec_extractor"
                                            })
                                            logger.info(f"  Added PDFSpec ID: {id_val:.4f} in")
                        else:
                            logger.warning(f"[RFQ_SCALE_CALIBRATION] PDF extraction failed: {extract_result.get('error', 'unknown error')}")
            except Exception as e:
                logger.warning(f"[RFQ_SCALE_CALIBRATION] Failed to extract OCR dimensions from PDF: {e}")
        
        # TASK 1: Debug log - final count and sample
        logger.info(f"[RFQ_SCALE_CALIBRATION] Total extracted OCR diameter candidates: {len(ocr_diameters)}")
        
        # Sort by confidence (descending) and take top 8
        ocr_diameters.sort(key=lambda x: x["confidence"], reverse=True)
        ocr_diameters = ocr_diameters[:8]
        
        # TASK 1: Log sample of first 10 candidates
        logger.info(f"[RFQ_SCALE_CALIBRATION] Top {min(len(ocr_diameters), 10)} OCR diameter candidates:")
        for idx, dia in enumerate(ocr_diameters[:10]):
            source = dia.get("source", "unknown")
            logger.info(f"  [{idx}] {dia['value']:.4f} in - {dia['text'][:50]} (conf: {dia['confidence']:.2f}, source: {source})")
        
        return ocr_diameters
    
    def collect_geometry_ods(
        self,
        segments: List[Dict[str, Any]],
        total_length: Optional[float],
        unit_len: str = "in"
    ) -> List[Dict[str, Any]]:
        """
        Collect candidate cylindrical ODs from geometry segments.
        
        Filters segments:
        - length > 5% of total length
        - confidence > 0.6
        - OD >= 0.08" (reject noise)
        
        Args:
            segments: List of segment dictionaries
            total_length: Total length of part
            unit_len: Length unit ("in" or "mm")
            
        Returns:
            List of geometry OD candidates with 'value', 'segment_index', 'length', 'confidence', 'is_global_max'
        """
        def seg_len_in(seg: Dict[str, Any]) -> float:
            zs = seg.get("z_start", 0.0)
            ze = seg.get("z_end", 0.0)
            if unit_len == "mm":
                return float(ze - zs) / 25.4
            return float(ze - zs)
        
        def to_inches(value: float, unit: str) -> float:
            if unit == "mm":
                return value / 25.4
            return value
        
        geometry_ods = []
        
        if not segments or not total_length or total_length <= 0:
            return geometry_ods
        
        min_length_threshold = total_length * 0.05  # 5% of total length
        min_od_threshold = 0.08  # Reject tiny ODs (noise)
        
        # First pass: collect all valid ODs and find global max
        all_ods = []
        for idx, seg in enumerate(segments):
            if not isinstance(seg, dict):
                continue
            
            seg_len = seg_len_in(seg)
            if seg_len < min_length_threshold:
                continue
            
            confidence = seg.get("confidence", 0.5)
            if confidence < 0.6:
                continue
            
            od_diameter = seg.get("od_diameter")
            if od_diameter is None:
                continue
            
            od_in = to_inches(float(od_diameter), unit_len)
            if od_in >= min_od_threshold:
                all_ods.append(od_in)
        
        if not all_ods:
            return geometry_ods
        
        global_max_od = max(all_ods)
        
        # Second pass: build candidates with is_global_max flag
        for idx, seg in enumerate(segments):
            if not isinstance(seg, dict):
                continue
            
            seg_len = seg_len_in(seg)
            if seg_len < min_length_threshold:
                continue
            
            confidence = seg.get("confidence", 0.5)
            if confidence < 0.6:
                continue
            
            od_diameter = seg.get("od_diameter")
            if od_diameter is None:
                continue
            
            od_in = to_inches(float(od_diameter), unit_len)
            
            if od_in >= min_od_threshold:
                geometry_ods.append({
                    "value": float(od_in),
                    "segment_index": idx,
                    "length": seg_len,
                    "confidence": float(confidence),
                    "is_global_max": abs(od_in - global_max_od) < 0.001
                })
        
        # TASK 1: Debug log - count of geometry ODs
        logger.info(f"[RFQ_SCALE_CALIBRATION] Collected {len(geometry_ods)} geometry OD candidates (global_max={global_max_od:.4f})")
        for idx, od in enumerate(geometry_ods):
            logger.info(f"  [{idx}] Geometry: {od['value']:.4f} in (seg_idx: {od['segment_index']}, len: {od['length']:.4f}, conf: {od['confidence']:.2f}, max_od: {od['is_global_max']})")
        
        return geometry_ods
    
    def match_ocr_to_geometry(
        self,
        ocr_diameters: List[Dict[str, Any]],
        geometry_ods: List[Dict[str, Any]],
        total_length: Optional[float] = None,
        global_max_od: Optional[float] = None
    ) -> Tuple[List[Tuple[float, Dict[str, Any], Dict[str, Any]]], List[Tuple[float, Dict[str, Any], Dict[str, Any]]]]:
        """
        Match OCR diameters to geometry ODs with dominant-ratio clustering.
        
        Only accepts matches to main body segments:
        - geometry_od >= 70% of global_max_geometry_od
        - segment_length >= 40% of total_length
        
        Discards matches to smaller feature ODs (shoulders/grooves).
        
        Args:
            ocr_diameters: List of OCR diameter dictionaries
            geometry_ods: List of geometry OD dictionaries (with is_global_max flag)
            total_length: Total length of part (for filtering by segment length)
            global_max_od: Global maximum OD (for filtering dominant segments)
            
        Returns:
            Tuple of (valid_pairs, discarded_pairs)
            valid_pairs: Matches to main body segments
            discarded_pairs: Matches to small feature segments
        """
        valid_pairs = []
        discarded_pairs = []
        
        if not ocr_diameters or not geometry_ods:
            return valid_pairs, discarded_pairs
        
        # Calculate thresholds for dominant segments
        od_threshold = None
        length_threshold = None
        
        if global_max_od is not None and global_max_od > 0:
            od_threshold = global_max_od * 0.55  # 55% of global max (relaxed from 70% for stepped parts)
        
        if total_length is not None and total_length > 0:
            length_threshold = total_length * 0.12  # 12% of total length (relaxed from 40% for multi-step parts)
        
        _od_thr = f"{od_threshold:.4f}" if od_threshold else "None"
        _len_thr = f"{length_threshold:.4f}" if length_threshold else "None"
        logger.info(f"[RFQ_SCALE_CALIBRATION] Dominant-ratio filtering thresholds: od_threshold={_od_thr}, length_threshold={_len_thr}")
        
        for ocr_dim in ocr_diameters:
            ocr_value = ocr_dim["value"]
            
            # Two-pass matching: prefer dominant matches over closer non-dominant
            best_dominant = None
            best_dominant_diff = float('inf')
            best_any = None
            best_any_diff = float('inf')
            
            for geo_od in geometry_ods:
                geo_value = geo_od["value"]
                geo_length = geo_od.get("length", 0.0)
                
                if geo_od.get("is_global_max", False):
                    continue
                if geo_value < 0.08:
                    continue
                if geo_value > ocr_value * 2.5:
                    continue
                
                ratio = ocr_value / geo_value if geo_value > 0 else float('inf')
                if ratio < 0.3 or ratio > 5.0:
                    continue
                
                is_dominant = True
                if od_threshold is not None and geo_value < od_threshold:
                    is_dominant = False
                if length_threshold is not None and geo_length < length_threshold:
                    is_dominant = False
                
                diff = abs(ocr_value - geo_value)
                if is_dominant and diff < best_dominant_diff:
                    best_dominant_diff = diff
                    best_dominant = geo_od
                if diff < best_any_diff:
                    best_any_diff = diff
                    best_any = geo_od
            
            chosen = best_dominant or best_any
            is_dom = best_dominant is not None
            
            if chosen:
                geo_value = chosen["value"]
                if geo_value > 0:
                    ratio = ocr_value / geo_value
                    if 0.3 <= ratio <= 5.0:
                        pair = (ratio, ocr_dim, chosen)
                        if is_dom:
                            valid_pairs.append(pair)
                            logger.info(f"[RFQ_SCALE_CALIBRATION] Valid match (dominant): OCR {ocr_value:.4f} in / Geometry {geo_value:.4f} in = ratio {ratio:.4f}")
                        else:
                            discarded_pairs.append(pair)
                            logger.info(f"[RFQ_SCALE_CALIBRATION] Discarded match (small feature): OCR {ocr_value:.4f} in / Geometry {geo_value:.4f} in = ratio {ratio:.4f}")
        
        return valid_pairs, discarded_pairs
    
    def calculate_scale_factor(
        self,
        valid_pairs: List[Tuple[float, Dict[str, Any], Dict[str, Any]]],
        discarded_pairs: List[Tuple[float, Dict[str, Any], Dict[str, Any]]]
    ) -> Tuple[Optional[float], float, List[Tuple[float, Dict[str, Any], Dict[str, Any]]], List[float]]:
        """
        Calculate scale factor from valid matched pairs (dominant-ratio clustering).
        
        After filtering to main body segments only:
        - If at least 1 valid pair exists → calibrate using median ratio
        - Remove "spread rejection" when only one dominant cluster exists
        
        Args:
            valid_pairs: List of (ratio, ocr_dim, geometry_od) tuples from dominant segments
            discarded_pairs: List of (ratio, ocr_dim, geometry_od) tuples from small features
            
        Returns:
            Tuple of (scale_factor, confidence, valid_pairs, ratios)
            scale_factor is None if no valid pairs
        """
        if len(valid_pairs) < 1:
            logger.info(f"[RFQ_SCALE_CALIBRATION] No valid pairs from dominant segments ({len(valid_pairs)}), cannot calibrate")
            ratios = [pair[0] for pair in valid_pairs]
            return None, 0.0, valid_pairs, ratios
        
        ratios = [pair[0] for pair in valid_pairs]
        ratios_sorted = sorted(ratios)
        
        # Use median ratio (dominant cluster)
        selected_ratio = median(ratios)
        scale_factor = selected_ratio
        
        # Confidence based on number of valid pairs
        if len(valid_pairs) >= 3:
            confidence = 0.9
        elif len(valid_pairs) == 2:
            confidence = 0.85
        else:
            confidence = 0.8  # Single pair still acceptable for dominant segment
        
        # TASK 6: Log calibration summary
        logger.info(f"[RFQ_SCALE_CALIBRATION]")
        logger.info(f"valid_pairs={len(valid_pairs)}")
        logger.info(f"discarded_pairs={len(discarded_pairs)}")
        logger.info(f"selected_ratio={selected_ratio:.4f}")
        logger.info(f"scale_factor={scale_factor:.4f}")
        logger.info(f"All valid ratios: {ratios_sorted}")
        
        return scale_factor, confidence, valid_pairs, ratios
    
    def extract_ocr_length(
        self,
        part_summary: Dict[str, Any],
        job_id: Optional[str] = None
    ) -> Optional[float]:
        """
        Extract OCR overall length from part_summary or PDF.
        
        Args:
            part_summary: Part summary dictionary
            job_id: Optional job ID to load PDF from
            
        Returns:
            OCR length in inches, or None if not found
        """
        # Pre-compute geometry total_length for plausibility checks
        geom_total = None
        totals = part_summary.get("totals", {})
        z_range = part_summary.get("z_range")
        if isinstance(totals, dict) and "total_length_in" in totals:
            geom_total = _as_float(totals["total_length_in"])
        elif isinstance(z_range, (list, tuple)) and len(z_range) >= 2:
            z0 = _as_float(z_range[0])
            z1 = _as_float(z_range[1])
            if z0 is not None and z1 is not None:
                geom_total = abs(z1 - z0)

        def _validate_length(val_in: float, source: str) -> Optional[float]:
            """Reject OCR length if it's implausibly far from geometry."""
            if geom_total and geom_total > 0:
                ratio = val_in / geom_total
                if ratio > 5.0 or ratio < 0.1:
                    logger.warning(
                        f"[RFQ_SCALE_CALIBRATION] Rejecting OCR length {val_in:.4f} from {source}: "
                        f"ratio to geometry ({geom_total:.4f}) = {ratio:.2f} (limit 0.1–5.0)"
                    )
                    return None
            return val_in

        # Check inference_metadata first
        inference_meta = part_summary.get("inference_metadata") or {}
        if isinstance(inference_meta, dict):
            raw_dimensions = inference_meta.get("raw_dimensions") or []
            if isinstance(raw_dimensions, list):
                for dim in raw_dimensions:
                    if isinstance(dim, dict):
                        text = dim.get("text", "")
                        value = dim.get("value")
                        unit = dim.get("unit", "in")
                        
                        has_length_keyword = bool(
                            re.search(r'\bLENGTH\b|\bLEN\b|\bOAL\b|\bOVERALL\b', text, re.IGNORECASE)
                        )
                        
                        if has_length_keyword and value is not None:
                            if unit == "mm":
                                value_in = value / 25.4
                            else:
                                value_in = value
                            
                            if value_in > 0 and 0.1 <= value_in <= 20.0:
                                validated = _validate_length(value_in, "raw_dimensions")
                                if validated is not None:
                                    logger.info(f"[RFQ_SCALE_CALIBRATION] Found OCR length in inference_metadata: {validated:.4f} in")
                                    return float(validated)
        
        # Try PDF extraction
        if job_id:
            try:
                from app.storage.file_storage import FileStorage
                fs = FileStorage()
                job_path = fs.get_inputs_path(job_id)
                
                if job_path.exists():
                    pdf_files = list(job_path.glob("*.pdf"))
                    if pdf_files:
                        pdf_path = pdf_files[0]
                        extract_result = self.pdf_extractor.extract_from_file(str(pdf_path))
                        if extract_result.get("success"):
                            specs = extract_result.get("extracted_specs", {})
                            finish_len = specs.get("finish_len_in")
                            if finish_len:
                                len_val = _as_float(finish_len)
                                if len_val and len_val > 0:
                                    validated = _validate_length(len_val, "PDFSpecExtractor")
                                    if validated is not None:
                                        logger.info(f"[RFQ_SCALE_CALIBRATION] Found OCR length in PDF: {validated:.4f} in")
                                        return float(validated)
            except Exception as e:
                logger.debug(f"Failed to extract OCR length from PDF: {e}")
        
        return None
    
    def apply_scaling(
        self,
        part_summary: Dict[str, Any],
        xy_scale: float,
        z_scale: Optional[float] = None,
        unit_len: str = "in"
    ) -> Tuple[Dict[str, Any], bool, bool]:
        """
        Apply axis-specific scaling to geometry in part_summary.
        
        Always scales XY (diameters) by xy_scale.
        Only scales Z (lengths) by z_scale if provided (otherwise z_scale = 1.0).
        
        Scaling formulas:
        - Areas: xy_scale^2
        - Volumes: xy_scale^2 * z_scale
        
        Args:
            part_summary: Part summary dictionary (will be modified)
            xy_scale: Scale factor for XY dimensions (diameters)
            z_scale: Optional scale factor for Z dimensions (lengths). If None, uses 1.0 (no Z scaling)
            unit_len: Length unit ("in" or "mm")
            
        Returns:
            Tuple of (modified_part_summary, scaled_xy, scaled_z)
        """
        def to_inches(value: float, unit: str) -> float:
            if unit == "mm":
                return value / 25.4
            return value
        
        def from_inches(value_in: float, unit: str) -> float:
            if unit == "mm":
                return value_in * 25.4
            return value_in
        
        if z_scale is None:
            z_scale = 1.0
        
        scaled_xy = True  # Always scale XY
        scaled_z = z_scale != 1.0  # Only true if Z is actually scaled
        
        logger.info(f"[RFQ_SCALE_CALIBRATION] Applying axis-specific scaling: xy_scale={xy_scale:.4f}, z_scale={z_scale:.4f}")
        
        # Scale segments
        segments = part_summary.get("segments", [])
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            
            # Scale z coordinates (only if z_scale != 1.0)
            if scaled_z:
                z_start = seg.get("z_start", 0.0)
                z_end = seg.get("z_end", 0.0)
                seg["z_start"] = float(z_start * z_scale)
                seg["z_end"] = float(z_end * z_scale)
            
            # Always scale diameters (XY)
            od_diameter = seg.get("od_diameter")
            id_diameter = seg.get("id_diameter")
            
            if od_diameter is not None:
                seg["od_diameter"] = float(od_diameter * xy_scale)
            
            if id_diameter is not None and id_diameter > 0:
                seg["id_diameter"] = float(id_diameter * xy_scale)
            
            # Recalculate wall_thickness if present
            if "wall_thickness" in seg and od_diameter is not None and id_diameter is not None:
                seg["wall_thickness"] = float((od_diameter * xy_scale - id_diameter * xy_scale) / 2.0)
            
            # Scale volume and area fields in segment
            # Areas scale with xy_scale^2
            area_scale = xy_scale ** 2
            if "od_area_in2" in seg:
                seg["od_area_in2"] = float(seg["od_area_in2"] * area_scale)
            if "id_area_in2" in seg:
                seg["id_area_in2"] = float(seg["id_area_in2"] * area_scale)
            
            # Volumes scale with xy_scale^2 * z_scale
            volume_scale = (xy_scale ** 2) * z_scale
            if "volume_in3" in seg:
                seg["volume_in3"] = float(seg["volume_in3"] * volume_scale)
        
        # Scale z_range (only if z_scale != 1.0)
        if scaled_z:
            z_range = part_summary.get("z_range")
            if isinstance(z_range, (list, tuple)) and len(z_range) >= 2:
                part_summary["z_range"] = [
                    float(z_range[0] * z_scale),
                    float(z_range[1] * z_scale)
                ]
        
        # Scale totals - support both old and new keys
        totals = part_summary.get("totals")
        if isinstance(totals, dict):
            # Scale length (only if z_scale != 1.0)
            if scaled_z and "total_length_in" in totals:
                totals["total_length_in"] = float(totals["total_length_in"] * z_scale)
            
            # Scale areas (area scales with xy_scale^2)
            area_scale = xy_scale ** 2
            area_keys = [
                "od_area_in2", "total_od_area_in2",
                "id_area_in2", "total_id_area_in2",
                "end_face_area_start_in2",
                "end_face_area_end_in2",
                "od_shoulder_area_in2",
                "id_shoulder_area_in2",
                "planar_ring_area_in2",
                "total_surface_area_in2"
            ]
            for key in area_keys:
                if key in totals:
                    totals[key] = float(totals[key] * area_scale)
            
            # Scale volume (volume scales with xy_scale^2 * z_scale)
            volume_scale = (xy_scale ** 2) * z_scale
            if "volume_in3" in totals:
                totals["volume_in3"] = float(totals["volume_in3"] * volume_scale)
            if "total_volume_in3" in totals:
                totals["total_volume_in3"] = float(totals["total_volume_in3"] * volume_scale)
        
        # Scale feature-derived diameters if present (XY only)
        inference_meta = part_summary.get("inference_metadata", {})
        if isinstance(inference_meta, dict):
            features = part_summary.get("features")
            if isinstance(features, dict):
                # Scale holes (diameters are XY)
                holes = features.get("holes", [])
                if isinstance(holes, list):
                    for hole in holes:
                        if isinstance(hole, dict):
                            if "diameter" in hole:
                                hole["diameter"] = float(hole["diameter"] * xy_scale)
                            if "diameter_in" in hole:
                                hole["diameter_in"] = float(hole["diameter_in"] * xy_scale)
                
                # Scale slots (width is XY, length is Z)
                slots = features.get("slots", [])
                if isinstance(slots, list):
                    for slot in slots:
                        if isinstance(slot, dict):
                            # Width scales with XY
                            if "width" in slot:
                                slot["width"] = float(slot["width"] * xy_scale)
                            if "width_in" in slot:
                                slot["width_in"] = float(slot["width_in"] * xy_scale)
                            # Length scales with Z
                            if scaled_z:
                                if "length" in slot:
                                    slot["length"] = float(slot["length"] * z_scale)
                                if "length_in" in slot:
                                    slot["length_in"] = float(slot["length_in"] * z_scale)
        
        logger.info(f"[RFQ_SCALE_CALIBRATION] Geometry scaling complete (scaled_xy={scaled_xy}, scaled_z={scaled_z})")
        
        return part_summary, scaled_xy, scaled_z
    
    def calibrate_geometry_scale(
        self,
        part_summary: Dict[str, Any],
        job_id: Optional[str] = None
    ) -> Tuple[Dict[str, Any], Optional[float], float, List[float]]:
        """
        Main calibration method.
        
        Extracts OCR dimensions, matches to geometry, calculates scale factor,
        and applies scaling if reliable.
        
        Args:
            part_summary: Part summary dictionary
            job_id: Optional job ID for PDF extraction
            
        Returns:
            Tuple of (modified_part_summary, scale_factor, confidence, ratios)
            scale_factor is None if calibration not applied
            ratios is list of computed ratios (empty if calibration failed)
        """
        # Check if scale is already calibrated
        scale_report = part_summary.get("scale_report", {})
        if isinstance(scale_report, dict):
            method = scale_report.get("method", "")
            if method == "calibrated_from_ocr":
                logger.info(f"[RFQ_SCALE_CALIBRATION] Geometry already calibrated, skipping")
                return part_summary, None, 0.0, []
        
        # Extract OCR diameters
        ocr_diameters = self.extract_ocr_diameters(part_summary, job_id)
        if not ocr_diameters:
            logger.info(f"[RFQ_SCALE_CALIBRATION] No OCR diameters found, cannot calibrate")
            return part_summary, None, 0.0, []
        
        # Collect geometry ODs
        segments = part_summary.get("segments", [])
        if not segments:
            logger.info(f"[RFQ_SCALE_CALIBRATION] No segments found, cannot calibrate")
            return part_summary, None, 0.0, []
        
        # Calculate total length
        z_range = part_summary.get("z_range")
        totals = part_summary.get("totals", {})
        total_length = None
        
        if isinstance(totals, dict) and "total_length_in" in totals:
            total_length = totals["total_length_in"]
        elif isinstance(z_range, (list, tuple)) and len(z_range) >= 2:
            total_length = abs(z_range[1] - z_range[0])
        
        unit_len = part_summary.get("units", {}).get("length", "in")
        
        geometry_ods = self.collect_geometry_ods(segments, total_length, unit_len)
        if not geometry_ods:
            logger.info(f"[RFQ_SCALE_CALIBRATION] No valid geometry ODs found, cannot calibrate")
            return part_summary, None, 0.0, []
        
        # Find global_max_od for dominant-ratio filtering
        global_max_od = max(geo_od["value"] for geo_od in geometry_ods) if geometry_ods else None
        
        # Match OCR to geometry with dominant-ratio clustering
        valid_pairs, discarded_pairs = self.match_ocr_to_geometry(
            ocr_diameters, 
            geometry_ods,
            total_length=total_length,
            global_max_od=global_max_od
        )
        
        if not valid_pairs:
            logger.info(f"[RFQ_SCALE_CALIBRATION] No valid matches found to dominant segments (discarded {len(discarded_pairs)} small feature matches)")
            return part_summary, None, 0.0, []
        
        # Calculate scale factor from valid pairs only (this is XY scale)
        xy_scale, confidence, valid_pairs_final, ratios = self.calculate_scale_factor(valid_pairs, discarded_pairs)
        if xy_scale is None:
            return part_summary, None, 0.0, ratios
        
        # Determine Z scale: only scale Z if geometry length is inconsistent with OCR length
        z_scale = None
        scaled_z = False
        
        # Extract OCR length for comparison
        ocr_length = self.extract_ocr_length(part_summary, job_id)
        
        if ocr_length is not None and total_length is not None and total_length > 0:
            # Check if geometry length is consistent with OCR length (within ±10%)
            length_diff_pct = abs(total_length - ocr_length) / ocr_length if ocr_length > 0 else float('inf')
            
            if length_diff_pct <= 0.10:  # Within ±10%
                z_scale = 1.0  # Don't scale Z
                scaled_z = False
                logger.info(f"[RFQ_SCALE_CALIBRATION] Z scale: geometry length {total_length:.4f} in matches OCR length {ocr_length:.4f} in (diff: {length_diff_pct:.2%}), NOT scaling Z")
            else:
                candidate_z = ocr_length / total_length if total_length > 0 else 1.0

                # Anisotropy guard: reject Z scaling if z/xy ratio is extreme.
                # Real engineering drawings with 2-D profile extraction may have
                # moderate anisotropy (up to ~2×) but never 3×+.
                aniso_ratio = candidate_z / xy_scale if xy_scale and xy_scale > 0 else float('inf')
                if aniso_ratio > 3.0 or aniso_ratio < 0.33:
                    z_scale = 1.0
                    scaled_z = False
                    logger.warning(
                        f"[RFQ_SCALE_CALIBRATION] Z scale REJECTED: candidate z_scale={candidate_z:.4f} "
                        f"vs xy_scale={xy_scale:.4f} gives anisotropy ratio {aniso_ratio:.2f} "
                        f"(limit 0.33–3.0). OCR length {ocr_length:.4f} is likely wrong. Keeping Z unscaled."
                    )
                else:
                    z_scale = candidate_z
                    scaled_z = True
                    logger.info(f"[RFQ_SCALE_CALIBRATION] Z scale: geometry length {total_length:.4f} in differs from OCR length {ocr_length:.4f} in (diff: {length_diff_pct:.2%}), scaling Z by {z_scale:.4f}")
        else:
            z_scale = 1.0
            scaled_z = False
            if ocr_length is None:
                logger.info(f"[RFQ_SCALE_CALIBRATION] Z scale: No OCR length found, NOT scaling Z (autofill will infer)")
            else:
                logger.info(f"[RFQ_SCALE_CALIBRATION] Z scale: No geometry length available, NOT scaling Z")
        
        # Apply axis-specific scaling
        scaled_part_summary, scaled_xy_flag, scaled_z_flag = self.apply_scaling(
            part_summary.copy(), 
            xy_scale=xy_scale,
            z_scale=z_scale,
            unit_len=unit_len
        )
        
        # Update scale_report
        scaled_part_summary["scale_report"] = {
            "method": "calibrated_from_ocr",
            "confidence": confidence,
            "scale_factor": xy_scale,  # XY scale factor
            "xy_scale": xy_scale,
            "z_scale": z_scale,
            "scaled_xy": scaled_xy_flag,
            "scaled_z": scaled_z_flag,
            "matched_pairs_count": len(valid_pairs_final),
            "valid_pairs": len(valid_pairs_final),
            "discarded_pairs": len(discarded_pairs),
            "validation_passed": True
        }
        
        # Log calibration summary (already logged in calculate_scale_factor)
        for idx, (ratio, ocr_dim, geo_od) in enumerate(valid_pairs_final):
            logger.info(f"  Valid pair {idx}: OCR {ocr_dim['value']:.4f} in / Geo {geo_od['value']:.4f} in = {ratio:.4f}")
        
        return scaled_part_summary, xy_scale, confidence, ratios
