"""
PDF Specification Extractor Service
Extracts dimensions and metadata from engineering drawing PDFs.
"""

import re
import os
from typing import Dict, Any, Optional, List, Tuple
from pathlib import Path


class PDFSpecExtractor:
    """Extract specifications from engineering drawing PDFs."""
    
    def __init__(self):
        # Dimension patterns (supports both inches and mm, handles ranges)
        # Patterns are ordered by specificity (most specific first)
        # Note: Patterns capture tolerance ranges (e.g., 0.723-0.727) - we'll extract MAX value
        self.od_patterns = [
            # OCR format: D.089, D.102, @.102 (D/@ = diameter symbol, .089 = value) - HIGHEST PRIORITY
            r'[D@]\s*\.(\d{2,4})\s*(?:X|[-–]|\s|$)',
            # OCR format: D.089, @.102 without suffix
            r'[D@]\s*\.(\d{2,4})(?:\s|$)',
            # Finish OD with explicit label
            r'FINISH\s*OD[:\s]*[Ã˜âˆ…Ø]?\s*([\d.]+(?:[-–][\d.]+)?)\s*(?:\[[\d.]+\])?\s*(?:"|IN|INCH)?',
            # Finish OD annotation pointing to dimension
            r'FINISH\s*OD.*?[Ã˜âˆ…Ø]\s*([\d.]+(?:[-–][\d.]+)?)\s*(?:\[[\d.]+\])?',
            # OD with diameter symbol before value
            r'[Ã˜âˆ…Ø]\s*([\d.]+(?:[-–][\d.]+)?)\s*(?:\[[\d.]+\])?\s*(?:"|IN|INCH)?\s*(?:MAX\s*)?(?:OD|O\.D\.|FINISH\s*OD)',
            # OD with label before value
            r'(?:MAX\s*)?(?:OD|O\.D\.|OUTER\s*DIA(?:METER)?)[:\s]*[Ã˜âˆ…Ø]?\s*([\d.]+(?:[-–][\d.]+)?)\s*(?:\[[\d.]+\])?\s*(?:"|IN|INCH)?',
            # Maximum diameter
            r'(?:MAX|MAXIMUM)\s*(?:DIAMETER|DIA)[:\s]*[Ã˜âˆ…Ø]?\s*([\d.]+(?:[-–][\d.]+)?)\s*(?:\[[\d.]+\])?',
            # Standalone diameter values that look like diameters (0.5 to 10 inches)
            r'\b([1-9]\.\d{2,3}(?:[-–]\d\.\d{2,3})?)\s*(?:\[[\d.]+\])?\s*(?:IN|INCH|")?',
            # Leading decimal diameters (.089, .102) in context of diameter keywords
            r'(?:OD|DIA|DIAMETER|OUTER)[:\s]*\.(\d{2,3}(?:[-–]\.\d{2,3})?)',
        ]
        
        self.id_patterns = [
            # Finish ID with explicit label (highest priority)
            r'FINISH\s*ID[:\s]*[Ã˜âˆ…Ø]?\s*([\d.]+(?:[-–][\d.]+)?)\s*(?:\[[\d.]+\])?\s*(?:"|IN|INCH)?',
            # Finish ID annotation pointing to dimension
            r'FINISH\s*ID.*?[Ã˜âˆ…Ø]\s*([\d.]+(?:[-–][\d.]+)?)\s*(?:\[[\d.]+\])?',
            # ID with diameter symbol before value
            r'[Ã˜âˆ…Ø]\s*([\d.]+(?:[-–][\d.]+)?)\s*(?:\[[\d.]+\])?\s*(?:"|IN|INCH)?\s*(?:ID|I\.D\.|BORE|FINISH\s*ID)',
            # ID with label before value
            r'(?:ID|I\.D\.|INNER\s*DIA(?:METER)?|BORE)[:\s]*[Ã˜âˆ…Ø]?\s*([\d.]+(?:[-–][\d.]+)?)\s*(?:\[[\d.]+\])?\s*(?:"|IN|INCH)?',
        ]
        
        self.length_patterns = [
            # Finish length with explicit label (highest priority)
            r'FINISH\s*(?:LENGTH|LEN)[:\s]*([\d.]+(?:[-–][\d.]+)?)\s*(?:\[[\d.]+\])?\s*(?:"|IN|INCH)?',
            # Finish length annotation pointing to dimension
            r'FINISH\s*(?:LENGTH|LEN).*?([\d.]+(?:[-–][\d.]+)?)\s*(?:\[[\d.]+\])?',
            # Length with label before value
            r'(?:OVERALL\s*)?(?:LENGTH|LG|L)[:\s]*([\d.]+(?:[-–][\d.]+)?)\s*(?:\[[\d.]+\])?\s*(?:"|IN|INCH)?',
            # Maximum length
            r'(?:MAX|MAXIMUM)\s*LENGTH[:\s]*([\d.]+(?:[-–][\d.]+)?)\s*(?:\[[\d.]+\])?',
        ]
        
        # Part metadata patterns
        self.part_no_patterns = [
            r'(?:PART\s*(?:NO|NUMBER|#))[:\s]*([A-Z0-9_-]+)',
            r'(?:P/N|PN)[:\s]*([A-Z0-9_-]+)',
            r'(?:DWG\s*(?:NO|NUMBER))[:\s]*([A-Z0-9_-]+)',
        ]
        
        self.material_patterns = [
            r'(?:MAT(?:ERIAL)?|MATL)[:\s]*([A-Z0-9\s\-]+?)(?:\s|$)',
            r'(?:GRADE)[:\s]*([A-Z0-9\s\-]+?)(?:\s|$)',
            r'(\d{2,3}-\d{2}-\d{2})',  # Pattern like 65-45-12 (ductile iron)
            r'(4\d{3})\s*(?:STEEL)?',  # 4140, 4340 steel
            r'(1\d{3,4})\s*(?:STEEL)?',  # 1018, 1045, 12L14 steel
            r'(A\d{3})\s*(?:ALUMINUM)?',  # A356 aluminum
            r'(6\d{3})\s*(?:ALUMINUM)?',  # 6061 aluminum
            r'(304|316|17-4|15-5)\s*(?:SS|STAINLESS)?',  # Stainless steel grades
            r'(?:ASTM|SAE|AMS|AISI|MIL)[- ]?([A-Z]?[\d\-]+)',
        ]
        
        self.qty_patterns = [
            r'(?:QTY|QUANTITY)[:\s]*(\d+)',
            r'(?:MOQ)[:\s]*(\d+)',
        ]
        
        self.revision_patterns = [
            r'(?:REV|REVISION)[:\s]*([A-Z0-9]+)',
        ]
        
        # Part name patterns - common names in engineering drawings
        self.part_name_patterns = [
            r'(?:PART\s*NAME|DESCRIPTION|TITLE)[:\s]*([A-Z][A-Z0-9\s\-_]+)',
            r'(?:NAME)[:\s]*([A-Z][A-Z0-9\s\-_]{2,30})',
            # Common part names
            r'\b(PISTON|SHAFT|SLEEVE|BUSHING|HOUSING|FLANGE|ADAPTER|COUPLING|NUT|BOLT|CAP|COVER|RING|PLUG|VALVE|BODY|FITTING|CONNECTOR|SPACER|INSERT|RETAINER|BLOCK|PLATE|ARM|BRACKET|MOUNT|BASE|CYLINDER|SPINDLE|WHEEL|GEAR|PULLEY|HUB|AXLE|PIN|ROD|TUBE)\b',
        ]
        
        # Material specification patterns
        self.material_spec_patterns = [
            r'(?:ASTM|SAE|AMS|AISI|MIL)[- ]?[A-Z]?[\d\-]+',
            r'(?:SPEC(?:IFICATION)?)[:\s]*([A-Z0-9\-]+)',
        ]
    
    def extract_from_file(self, pdf_path: str) -> Dict[str, Any]:
        """
        Extract specifications from a PDF file.
        
        Args:
            pdf_path: Path to PDF file
            
        Returns:
            Dictionary with extracted specs
        """
        # Try to extract text from PDF
        text = self._extract_text_from_pdf(pdf_path)
        
        if not text:
            return {
                "success": False,
                "error": "Could not extract text from PDF",
                "extracted_specs": {}
            }
        
        # Extract all specifications
        specs = self._extract_specifications(text, pdf_path)
        
        return {
            "success": True,
            "extracted_specs": specs,
            "raw_text_preview": text[:500]  # First 500 chars for debugging
        }
    
    def _extract_text_from_pdf(self, pdf_path: str) -> str:
        """Extract text from PDF using available libraries, with OCR fallback."""
        # Try regular text extraction first
        text = self._try_text_extraction(pdf_path)
        
        # If no text found (likely image-based PDF), try OCR
        if not text or len(text.strip()) < 50:
            print(f"Regular text extraction failed or insufficient text. Trying OCR...")
            text = self._extract_text_with_ocr(pdf_path)
        
        return text.upper()  # Normalize to uppercase for pattern matching
    
    def _try_text_extraction(self, pdf_path: str) -> str:
        """Try to extract text using pdfplumber or PyPDF2."""
        try:
            # Try pdfplumber first (better for technical drawings)
            import pdfplumber
            
            text = ""
            with pdfplumber.open(pdf_path) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
            
            return text
            
        except ImportError:
            # Fallback to PyPDF2
            try:
                import PyPDF2
                
                text = ""
                with open(pdf_path, 'rb') as file:
                    reader = PyPDF2.PdfReader(file)
                    for page in reader.pages:
                        page_text = page.extract_text()
                        if page_text:
                            text += page_text + "\n"
                
                return text
                
            except ImportError:
                return ""
        except Exception as e:
            print(f"Error in text extraction: {e}")
            return ""
    
    def _extract_text_with_ocr(self, pdf_path: str) -> str:
        """Extract text from image-based PDF using OCR."""
        # Try PyMuPDF built-in text extraction first (works for most PDFs)
        try:
            import fitz  # PyMuPDF
            
            print(f"[OCR] Trying PyMuPDF text extraction...")
            doc = fitz.open(pdf_path)
            text = ""
            for page in doc:
                page_text = page.get_text()
                if page_text:
                    text += page_text + "\n"
            doc.close()
            
            if text and len(text.strip()) > 50:
                print(f"[OCR] PyMuPDF extracted {len(text)} characters.")
                return text
        except Exception as e:
            print(f"[OCR] PyMuPDF text extraction failed: {e}")
        
        # Try pytesseract OCR (simpler, doesn't need PyTorch)
        try:
            import fitz  # PyMuPDF for PDF to image
            import pytesseract
            from PIL import Image
            import io
            
            print(f"[OCR] Using Tesseract OCR...")
            
            # Open PDF with PyMuPDF
            doc = fitz.open(pdf_path)
            
            text = ""
            for i, page in enumerate(doc):
                print(f"[OCR] Processing page {i+1}/{len(doc)}...")
                
                # Render page to image at 300 DPI
                mat = fitz.Matrix(300/72, 300/72)  # 300 DPI
                pix = page.get_pixmap(matrix=mat)
                
                # Convert to PIL Image
                img_data = pix.tobytes("png")
                img = Image.open(io.BytesIO(img_data))
                
                # Perform OCR with pytesseract
                page_text = pytesseract.image_to_string(img)
                text += page_text + "\n"
            
            doc.close()
            print(f"[OCR] Tesseract extracted {len(text)} characters.")
            return text
            
        except ImportError as e:
            print(f"[OCR] pytesseract not available: {e}")
            print("Install with: pip install pytesseract")
            print("Also need Tesseract-OCR: https://github.com/UB-Mannheim/tesseract/wiki")
        except Exception as e:
            print(f"[OCR] Tesseract error: {e}")
        
        # Fallback: Try EasyOCR (needs PyTorch, may have DLL issues on Windows)
        try:
            os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
            
            import fitz
            import easyocr
            import numpy as np
            from PIL import Image
            import io
            
            print(f"[OCR] Trying EasyOCR (fallback)...")
            doc = fitz.open(pdf_path)
            reader = easyocr.Reader(['en'], gpu=False, verbose=False)
            
            text = ""
            for i, page in enumerate(doc):
                mat = fitz.Matrix(300/72, 300/72)
                pix = page.get_pixmap(matrix=mat)
                img_data = pix.tobytes("png")
                img = Image.open(io.BytesIO(img_data))
                img_array = np.array(img)
                
                if len(img_array.shape) == 3 and img_array.shape[2] == 4:
                    img_array = img_array[:, :, :3]
                
                results = reader.readtext(img_array)
                for (bbox, text_content, confidence) in results:
                    if confidence > 0.4:
                        text += text_content + " "
                text += "\n"
            
            doc.close()
            print(f"[OCR] EasyOCR extracted {len(text)} characters.")
            return text
            
        except Exception as e:
            print(f"[OCR] EasyOCR failed: {e}")
        
        return ""
    
    def _extract_specifications(self, text: str, pdf_path: str) -> Dict[str, Any]:
        """Extract all specifications from text."""
        specs = {}
        
        # Extract part number (try from filename first, then from text)
        part_no_from_file = self._extract_part_no_from_filename(pdf_path)
        part_no_from_text = self._extract_with_patterns(text, self.part_no_patterns)
        specs["part_no"] = part_no_from_file or part_no_from_text
        
        # Extract dimensions (inches) - try context-aware extraction first
        specs["finish_od_in"] = self._extract_dimension_with_context(text, "FINISH OD", self.od_patterns)
        specs["finish_id_in"] = self._extract_dimension_with_context(text, "FINISH ID", self.id_patterns)
        finish_len_context = self._extract_dimension_with_context(text, "FINISH LENGTH", self.length_patterns)
        if not finish_len_context:
            finish_len_context = self._extract_dimension_with_context(text, "FINISH LEN", self.length_patterns)
        specs["finish_len_in"] = finish_len_context
        
        # Fallback to regular extraction if context-aware didn't find anything
        if not specs["finish_od_in"]:
            specs["finish_od_in"] = self._extract_dimension(text, self.od_patterns, is_length=False)
        if not specs["finish_id_in"]:
            specs["finish_id_in"] = self._extract_dimension(text, self.id_patterns, is_length=False)
        if not specs["finish_len_in"]:
            specs["finish_len_in"] = self._extract_dimension(text, self.length_patterns, is_length=True)
        
        # Extract metadata
        specs["material_grade"] = self._extract_material_grade(text)
        specs["qty_moq"] = self._extract_quantity(text)
        specs["revision"] = self._extract_with_patterns(text, self.revision_patterns, max_len=5)
        specs["part_name"] = self._clean_part_name(text)
        specs["material_spec"] = self._extract_with_patterns(text, self.material_spec_patterns, max_len=30)
        
        # Add confidence scores
        specs["confidence"] = self._calculate_confidence(specs)
        
        return specs
    
    def _extract_part_no_from_filename(self, pdf_path: str) -> Optional[str]:
        """Extract part number from PDF filename."""
        filename = Path(pdf_path).stem  # Get filename without extension
        
        # Pattern: 050dz0017_C -> 050DZ0017
        match = re.search(r'([A-Z0-9]+)', filename.upper())
        if match:
            part_no = match.group(1)
            # Remove trailing revision letter if present (e.g., _C, _R1)
            part_no = re.sub(r'_[A-Z0-9]+$', '', part_no)
            return part_no
        
        return None
    
    def _extract_dimension_with_context(self, text: str, context_label: str, patterns: List[str], context_window: int = 200) -> Optional[float]:
        """Extract dimension value near a specific context label.
        
        This method looks for dimensions that appear near labels like "FINISH OD",
        "FINISH ID", etc., to avoid matching unrelated dimensions on the drawing.
        
        Args:
            text: Full text to search
            context_label: Label to look for (e.g., "FINISH OD")
            patterns: List of regex patterns to match dimensions
            context_window: Number of characters to search around the label
            
        Returns:
            Extracted dimension value or None
        """
        # Find all occurrences of the context label
        label_positions = []
        for match in re.finditer(re.escape(context_label), text, re.IGNORECASE):
            label_positions.append(match.start())
        
        if not label_positions:
            return None
        
        # Search for dimensions near each label occurrence
        for label_pos in label_positions:
            # Extract context window around the label
            start = max(0, label_pos - context_window)
            end = min(len(text), label_pos + context_window)
            context_text = text[start:end]
            
            # Try to extract dimension from this context
            for pattern in patterns:
                matches = re.finditer(pattern, context_text, re.IGNORECASE)
                for match in matches:
                    try:
                        value_str = match.group(1).strip()
                        
                        # Handle dimension ranges (tolerance ranges like 0.723-0.727, .185-.190)
                        # Check for both regular hyphen and en-dash
                        range_match = re.search(r'(\d+\.\d+)\s*[-–]\s*(\d+\.\d+)', value_str) or \
                                     re.search(r'\.(\d+)\s*[-–]\s*\.(\d+)', value_str)
                        
                        if range_match:
                            try:
                                if range_match.lastindex == 2:
                                    # Full format: 0.723-0.727
                                    val1 = float(range_match.group(1))
                                    val2 = float(range_match.group(2))
                                else:
                                    # Leading decimal: .185-.190
                                    val1_str = '0.' + range_match.group(1)
                                    val2_str = '0.' + range_match.group(2)
                                    val1 = float(val1_str)
                                    val2 = float(val2_str)
                                
                                # For length patterns, use average; for diameters, use max
                                # Check context_label to determine dimension type
                                if 'LENGTH' in context_label.upper() or 'LEN' in context_label.upper():
                                    value = (val1 + val2) / 2.0
                                else:
                                    value = max(val1, val2)  # MAX for diameters (conservative)
                                
                                if 0.01 < value < 1000:
                                    return round(value, 4)
                            except (ValueError, IndexError):
                                continue
                        
                        # Also check for simple hyphen split (fallback)
                        if '-' in value_str and not value_str.startswith('-') and not range_match:
                            parts = value_str.split('-', 1)
                            if len(parts) == 2:
                                try:
                                    val1_str = parts[0].strip()
                                    val2_str = parts[1].strip()
                                    
                                    # Handle leading decimal point
                                    if val1_str.startswith('.'):
                                        val1_str = '0' + val1_str
                                    if val2_str.startswith('.'):
                                        val2_str = '0' + val2_str
                                    
                                    val1 = float(val1_str)
                                    val2 = float(val2_str)
                                    
                                    # For length patterns, use average; for diameters, use max
                                    if 'LENGTH' in context_label.upper() or 'LEN' in context_label.upper():
                                        value = (val1 + val2) / 2.0
                                    else:
                                        value = max(val1, val2)
                                    
                                    if 0.01 < value < 1000:
                                        return round(value, 4)
                                except (ValueError, IndexError):
                                    continue
                        
                        # Handle single value
                        # Handle leading decimal point (e.g., ".089" -> "0.089")
                        if value_str.startswith('.'):
                            value_str = '0' + value_str
                        # Handle OCR format where pattern captures only digits after decimal (e.g., "089" from "D.089")
                        elif not '.' in value_str and len(value_str) >= 2 and value_str.isdigit():
                            # This might be from OCR pattern like "D.089" where we captured "089"
                            value_str = '0.' + value_str
                        
                        value = float(value_str)
                        
                        # For diameters, reasonable range is 0.01 to 10 inches
                        # For lengths, reasonable range is 0.01 to 100 inches
                        max_value = 100 if 'LENGTH' in context_label.upper() or 'LEN' in context_label.upper() else 10
                        if 0.01 < value < max_value:
                            return round(value, 4)
                    except (ValueError, IndexError):
                        continue
        
        return None
    
    def _extract_dimension(self, text: str, patterns: List[str], is_length: bool = False) -> Optional[float]:
        """Extract dimension value from text using multiple patterns.
        
        Handles dimension ranges (e.g., "1.006-1.008") by taking the maximum value.
        For length dimensions, takes the average of the range.
        
        Args:
            text: Text to search
            patterns: List of regex patterns to match dimensions
            is_length: True if extracting length dimension, False for OD/ID
        """
        for pattern_idx, pattern in enumerate(patterns):
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                try:
                    value_str = match.group(1).strip()
                    matched_text = match.group(0)
                    
                    # Handle OCR format where pattern captures only digits after decimal (e.g., "089" from "D.089")
                    # Check if this is from an OCR pattern (D.089 or @.102 format) - first 2 patterns
                    is_ocr_decimal_format = pattern_idx < 2 and not '.' in value_str and len(value_str) >= 2 and len(value_str) <= 4
                    if is_ocr_decimal_format:
                        # Convert "089" -> "0.089"
                        value_str = '0.' + value_str
                    
                    # Handle dimension ranges (tolerance ranges like 0.723-0.727, .185-.190)
                    # Check for both regular hyphen and en-dash
                    range_match = re.search(r'(\d+\.\d+)\s*[-–]\s*(\d+\.\d+)', value_str) or \
                                 re.search(r'\.(\d+)\s*[-–]\s*\.(\d+)', value_str)
                    
                    if range_match:
                        try:
                            if range_match.lastindex == 2:
                                # Full format: 0.723-0.727
                                val1 = float(range_match.group(1))
                                val2 = float(range_match.group(2))
                            else:
                                # Leading decimal: .185-.190
                                val1_str = '0.' + range_match.group(1)
                                val2_str = '0.' + range_match.group(2)
                                val1 = float(val1_str)
                                val2 = float(val2_str)
                            
                            # For OD/ID patterns, use max value (conservative)
                            # For length patterns, use average
                            if is_length:
                                value = (val1 + val2) / 2.0  # Average for length
                            else:
                                value = max(val1, val2)  # Max for diameters
                            
                            # Sanity check: dimensions should be reasonable
                            if 0.01 < value < 1000:  # Inches
                                return round(value, 4)
                        except (ValueError, IndexError):
                            continue
                    
                    # Also check for simple hyphen split (fallback)
                    if '-' in value_str and not value_str.startswith('-') and not range_match:
                        parts = value_str.split('-', 1)
                        if len(parts) == 2:
                            try:
                                val1_str = parts[0].strip()
                                val2_str = parts[1].strip()
                                
                                # Handle leading decimal point (e.g., ".185-.190")
                                if val1_str.startswith('.'):
                                    val1_str = '0' + val1_str
                                if val2_str.startswith('.'):
                                    val2_str = '0' + val2_str
                                
                                val1 = float(val1_str)
                                val2 = float(val2_str)
                                
                                # For OD/ID patterns, use max value (conservative)
                                # For length patterns, use average
                                if is_length:
                                    value = (val1 + val2) / 2.0  # Average for length
                                else:
                                    value = max(val1, val2)  # Max for diameters
                                
                                # Sanity check: dimensions should be reasonable
                                if 0.01 < value < 1000:  # Inches
                                    return round(value, 4)
                            except (ValueError, IndexError):
                                continue
                    
                    # Handle single value
                    # Handle leading decimal point (e.g., ".185")
                    if value_str.startswith('.'):
                        value_str = '0' + value_str
                    # OCR format already handled above (converted "089" -> "0.089")
                    
                    value = float(value_str)
                    
                    # Sanity check: dimensions should be reasonable
                    # For diameters, reasonable range is 0.01 to 10 inches
                    # For lengths, reasonable range is 0.01 to 100 inches
                    max_value = 100 if is_length else 10
                    if 0.01 < value < max_value:
                        return round(value, 4)
                except (ValueError, IndexError):
                    continue
        
        return None
    
    def _extract_with_patterns(self, text: str, patterns: List[str], max_len: int = 50) -> Optional[str]:
        """Extract text value using multiple patterns."""
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                value = match.group(1).strip() if match.lastindex else match.group(0).strip()
                # Limit length and clean up
                value = value[:max_len]
                # Remove trailing noise
                value = re.sub(r'\s+(REMOVE|ALL|AND|THE|OR|FOR|WITH).*$', '', value, flags=re.IGNORECASE)
                # Clean up whitespace
                value = ' '.join(value.split())
                if len(value) >= 2:
                    return value
        
        return None
    
    def _extract_material_grade(self, text: str) -> Optional[str]:
        """Extract material grade with better validation."""
        # Try specific material patterns first
        material_keywords = {
            # Steel grades
            r'4140': '4140 Steel',
            r'4340': '4340 Steel',
            r'1018': '1018 Steel',
            r'1045': '1045 Steel',
            r'12L14': '12L14 Steel',
            r'8620': '8620 Steel',
            # Stainless steel
            r'304\s*(?:SS)?': '304 SS',
            r'316\s*(?:SS)?': '316 SS',
            r'17-4\s*(?:PH)?': '17-4 PH SS',
            r'15-5\s*(?:PH)?': '15-5 PH SS',
            # Aluminum
            r'6061': '6061 Aluminum',
            r'7075': '7075 Aluminum',
            r'A356': 'A356 Aluminum',
            # Ductile iron (65-45-12 pattern)
            r'(\d{2})-(\d{2})-(\d{2})': None,  # Will be handled specially
            # Cast iron
            r'(?:CAST\s*IRON|CI)': 'Cast Iron',
            # Bronze
            r'(?:BRONZE|C93\d{3})': 'Bronze',
            # Brass
            r'(?:BRASS|C36\d{3})': 'Brass',
        }
        
        text_upper = text.upper()
        
        for pattern, grade in material_keywords.items():
            match = re.search(pattern, text_upper, re.IGNORECASE)
            if match:
                if grade is None:  # Ductile iron pattern
                    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
                return grade
        
        # Fallback to generic pattern matching
        return self._extract_with_patterns(text, self.material_patterns, max_len=30)
    
    def _clean_part_name(self, text: str) -> Optional[str]:
        """Extract clean part name from OCR text."""
        # Common part names in machined parts
        part_names = [
            'PISTON', 'SHAFT', 'SLEEVE', 'BUSHING', 'HOUSING', 'FLANGE', 'ADAPTER',
            'COUPLING', 'NUT', 'BOLT', 'CAP', 'COVER', 'RING', 'PLUG', 'VALVE', 'BODY',
            'FITTING', 'CONNECTOR', 'SPACER', 'INSERT', 'RETAINER', 'BLOCK', 'PLATE',
            'ARM', 'BRACKET', 'MOUNT', 'BASE', 'CYLINDER', 'SPINDLE', 'WHEEL', 'GEAR',
            'PULLEY', 'HUB', 'AXLE', 'PIN', 'ROD', 'TUBE', 'GLAND', 'HEAD', 'SEAL',
            'BEARING', 'COLLAR', 'GUIDE', 'SUPPORT', 'CLAMP', 'LEVER', 'LINK', 'HANDLE'
        ]
        
        # Look for compound names like "HEAD GLAND", "PISTON ROD"
        for name1 in part_names:
            for name2 in part_names:
                compound = f"{name1} {name2}"
                if compound in text.upper():
                    return compound.title()
        
        # Look for single names
        for name in part_names:
            if name in text.upper():
                return name.title()
        
        return None
    
    def _extract_quantity(self, text: str) -> Optional[int]:
        """Extract quantity as integer."""
        value_str = self._extract_with_patterns(text, self.qty_patterns)
        if value_str:
            try:
                return int(value_str)
            except ValueError:
                pass
        return None
    
    def _calculate_confidence(self, specs: Dict[str, Any]) -> Dict[str, float]:
        """Calculate confidence scores for extracted values."""
        confidence = {}
        
        # High confidence if value is found
        confidence["part_no"] = 0.95 if specs.get("part_no") else 0.0
        confidence["finish_od_in"] = 0.85 if specs.get("finish_od_in") else 0.0
        confidence["finish_id_in"] = 0.75 if specs.get("finish_id_in") else 0.0
        confidence["finish_len_in"] = 0.85 if specs.get("finish_len_in") else 0.0
        confidence["material_grade"] = 0.80 if specs.get("material_grade") else 0.0
        confidence["qty_moq"] = 0.80 if specs.get("qty_moq") else 0.0
        
        # Overall confidence is average of dimension confidences
        dim_confidences = [
            confidence["finish_od_in"],
            confidence["finish_len_in"]
        ]
        confidence["overall"] = sum(dim_confidences) / len(dim_confidences) if dim_confidences else 0.0
        
        return confidence
    
    def extract_all_dimension_candidates(self, pdf_path: str) -> List[Dict[str, Any]]:
        """Scrape ALL plausible dimension candidates from the raw OCR text.

        Returns a list of dicts suitable for ``raw_dimensions``:
            { value_in, value, text, confidence, kind, unit, is_tolerance }

        Unlike ``extract_from_file`` (which returns a single best OD/ID/LEN),
        this method returns *every* numeric token that looks like an engineering
        dimension, together with a heuristic ``kind`` classification.
        The downstream ``ocr_finish_selector`` will score and pick the best.
        """
        text = self._extract_text_from_pdf(pdf_path)
        if not text or len(text.strip()) < 30:
            return []

        candidates: List[Dict[str, Any]] = []
        seen: set = set()

        # Patterns that capture tolerance ranges and single values in OCR text.
        # We scan line-by-line to preserve context for classification.
        _DIM_RE = re.compile(
            r"""
            (?:                          # tolerance range forms:
              (\d*\.\d+)\s*[-\u2013]\s*(\d*\.\d+)   # 1.006-1.008  or  .185-.190
            )
            |                            # OR single decimal:
            (?<![A-Z])                   # not preceded by letter (avoid "REV" etc.)
            (\d{0,2}\.\d{2,5})          # 0.443  or  1.240  or  .630
            (?!\d)                       # not followed by more digits
            """,
            re.VERBOSE,
        )

        _BRACKET_RE = re.compile(r"\[.*?\]")
        _RAW_KW = re.compile(r"\b(RAW|STOCK|BAR|BLANK|RM)\b", re.IGNORECASE)
        _THREAD_KW = re.compile(r"\b(THREAD|UNC|UNF|PITCH|TAP)\b", re.IGNORECASE)
        _SCALE_KW = re.compile(r"\bSCALE\b", re.IGNORECASE)

        _OD_KW = re.compile(
            r"\b(FINISH\s*OD|OD|O\.D|DIA|DIAMETER|OUTER)\b"
            r"|[D@]\s*\.\d"
            r"|\u00d8|\u2205|\u2300",
            re.IGNORECASE,
        )
        _ID_KW = re.compile(
            r"\b(FINISH\s*ID|ID|I\.D|BORE|INNER)\b", re.IGNORECASE,
        )
        _LEN_KW = re.compile(
            r"\b(FINISH\s*(?:LENGTH|LEN)|LENGTH|LEN|OAL|OVERALL)\b", re.IGNORECASE,
        )

        # Extra pattern: OCR garbles like "BD. 443" or "D. 443" → 0.443
        _GARBLED_DEC_RE = re.compile(
            r"(B?[D@])\.\s+(\d{2,4})", re.IGNORECASE,
        )

        # Garbled Ø prefix: OCR reads Ø as O, G, C, Q, etc.
        # Matches "O1.475", "G1.490", "Q0.750" → extracts the number as a diameter.
        _DIA_PREFIX_RE = re.compile(
            r"(?<![A-Z0-9])([OGCQogcq\u00d8\u2205\u2300])\s*(\d{1,2}\.\d{2,5})(?!\d)",
        )

        for line in text.split("\n"):
            line_stripped = line.strip()
            # Clean OCR artifacts: backslash before dot (e.g., "1\.490" → "1.490")
            line_stripped = re.sub(r'(\d)\\\.', r'\1.', line_stripped)
            if not line_stripped:
                continue
            if _BRACKET_RE.sub("", line_stripped).strip() == "":
                continue
            if _RAW_KW.search(line_stripped) or _THREAD_KW.search(line_stripped):
                continue
            if _SCALE_KW.search(line_stripped):
                continue
            _upper = line_stripped.upper()
            # Skip lines that are metric bracket equivalents or start with [ or (
            if re.match(r"^[\(\[C][\d.,\-\u2013\s]+[\)\]]", line_stripped):
                continue
            # Lines like "C1.6] | C1.78)" are metric equivalents
            if re.match(r"^C\d", line_stripped):
                continue
            # Skip notes / tolerance specification / title-block lines
            if any(kw in _upper for kw in (
                "UNLESS OTHERWISE", "CORNER RADI", "TO BE BROKEN",
                "TOLERANCE", "DUCTILE IRON", "MATERIAL DESCRIPTION",
                "MATERIAL MUST CONFORM", "DIMENSIONS ARE IN",
                "STANDARD TOLERANCE", "ENGINEER", "DESIGNER",
                "CUSTOMER", "MASS (LBS)", "DO NOT SCALE",
            )):
                continue

            # --- Pass 1: garbled-decimal patterns (BD. 443 → 0.443) ---
            for gm in _GARBLED_DEC_RE.finditer(line_stripped):
                prefix = gm.group(1).upper()
                digits = gm.group(2)
                val_s = "0." + digits
                try:
                    val = float(val_s)
                except ValueError:
                    continue
                if val <= 0.009 or val > 30.0:
                    continue
                key = round(val, 5)
                if key in seen:
                    continue
                seen.add(key)

                # "BD" prefix = Bore/Diameter → classify as ID
                ctx = line_stripped
                kind: Optional[str] = None
                conf = 0.75
                if prefix == "BD" or prefix == "B" or _ID_KW.search(ctx):
                    kind = "ID"
                    conf = 0.80
                elif _OD_KW.search(ctx):
                    kind = "OD"
                    conf = 0.75
                else:
                    kind = "OD"
                    conf = 0.60

                candidates.append({
                    "value_in": round(val, 4),
                    "value": round(val, 4),
                    "text": line_stripped[:80].strip(),
                    "confidence": conf,
                    "kind": kind,
                    "unit": "in",
                    "is_tolerance": False,
                })

            # --- Pass 1b: garbled Ø-prefix (O1.475, G1.490 → diameter) ---
            for pm in _DIA_PREFIX_RE.finditer(line_stripped):
                prefix_char = pm.group(1)
                num_s = pm.group(2)
                try:
                    val = float(num_s)
                except ValueError:
                    continue
                if val <= 0.009 or val > 30.0:
                    continue
                key = round(val, 5)
                if key in seen:
                    continue
                seen.add(key)

                kind = "OD"
                conf = 0.75
                if _ID_KW.search(line_stripped):
                    kind = "ID"
                    conf = 0.80
                elif _OD_KW.search(line_stripped):
                    conf = 0.80

                candidates.append({
                    "value_in": round(val, 4),
                    "value": round(val, 4),
                    "text": line_stripped[:80].strip(),
                    "confidence": conf,
                    "kind": kind,
                    "unit": "in",
                    "is_tolerance": False,
                })

            # --- Pass 2: standard dimension patterns ---
            for m in _DIM_RE.finditer(line_stripped):
                is_tol = False
                if m.group(1) and m.group(2):
                    lo_s, hi_s = m.group(1), m.group(2)
                    if lo_s.startswith("."):
                        lo_s = "0" + lo_s
                    if hi_s.startswith("."):
                        hi_s = "0" + hi_s
                    try:
                        lo, hi = float(lo_s), float(hi_s)
                    except ValueError:
                        continue
                    val = max(lo, hi)
                    is_tol = True
                else:
                    single = m.group(3) or ""
                    if single.startswith("."):
                        single = "0" + single
                    try:
                        val = float(single)
                    except ValueError:
                        continue

                if val <= 0.009 or val > 30.0:
                    continue
                start_pos = m.start()
                preceding = line_stripped[:start_pos]
                if preceding.rstrip().endswith("["):
                    continue

                key = round(val, 5)
                if key in seen:
                    continue
                seen.add(key)

                # --- Classification: use LOCAL context near the match ---
                # Take ~30 chars before the match for local context
                local_start = max(0, m.start() - 30)
                local_ctx = line_stripped[local_start:m.end() + 10]

                kind = None
                conf = 0.70

                # Check immediate prefix for D.xxx / @.xxx → likely bore/OD
                imm_prefix = line_stripped[max(0, m.start() - 3):m.start()]
                is_d_prefix = bool(re.search(r"[D@]\s*$", imm_prefix))

                if _ID_KW.search(local_ctx):
                    kind = "ID"
                    conf = 0.80
                elif is_d_prefix and is_tol and val < 1.0:
                    # D.xxx-yyy with small value: likely bore measurement
                    kind = "ID"
                    conf = 0.75
                elif _OD_KW.search(local_ctx):
                    kind = "OD"
                    conf = 0.80
                elif _LEN_KW.search(local_ctx):
                    kind = "LEN"
                    conf = 0.80
                else:
                    if 0.1 <= val <= 5.0:
                        kind = "OD"
                        conf = 0.60
                    elif val > 5.0:
                        kind = "LEN"
                        conf = 0.50
                    else:
                        kind = "OD"
                        conf = 0.50

                text_snip = line_stripped[:80].strip()
                candidates.append({
                    "value_in": round(val, 4),
                    "value": round(val, 4),
                    "text": text_snip,
                    "confidence": conf,
                    "kind": kind,
                    "unit": "in",
                    "is_tolerance": is_tol,
                })

        return candidates

    def format_for_rfq(self, specs: Dict[str, Any], cost_inputs: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Format extracted specs into RFQ autofill format.
        
        Args:
            specs: Extracted specifications
            cost_inputs: Optional cost calculation inputs
            
        Returns:
            Dictionary compatible with RFQAutofillRequest
        """
        # Build source payload
        source = {
            "job_id": None,
            "part_summary": None,
            "step_metrics": None,
            "pdf_extracted": True,  # Flag to indicate PDF source
        }
        
        # Default tolerances
        tolerances = {
            "rm_od_allowance_in": 0.26,  # Based on Excel example
            "rm_len_allowance_in": 0.35,
        }
        
        # Build RFQ request
        rfq_request = {
            "rfq_id": "EXTRACTED",  # Placeholder
            "part_no": specs.get("part_no", "UNKNOWN"),
            "source": source,
            "tolerances": tolerances,
            "mode": "ENVELOPE",  # Use envelope mode for PDF-extracted specs
            "vendor_quote_mode": True,  # Use Excel-exact calculations
            "cost_inputs": cost_inputs,
            "extracted_specs": specs,  # Include raw extracted specs
        }
        
        return rfq_request


def test_extractor():
    """Test the PDF extractor with sample file."""
    extractor = PDFSpecExtractor()
    
    # Test with the provided PDF
    pdf_path = r"C:\Users\beleh\Downloads\drgs data\1\050dz0017_C.pdf"
    
    if os.path.exists(pdf_path):
        result = extractor.extract_from_file(pdf_path)
        print("Extraction Result:")
        print(f"Success: {result['success']}")
        print(f"\nExtracted Specs:")
        for key, value in result.get('extracted_specs', {}).items():
            print(f"  {key}: {value}")
        
        # Format for RFQ
        rfq_data = extractor.format_for_rfq(result['extracted_specs'])
        print(f"\nFormatted for RFQ:")
        print(f"  Part No: {rfq_data['part_no']}")
        print(f"  Mode: {rfq_data['mode']}")
        print(f"  Vendor Quote Mode: {rfq_data['vendor_quote_mode']}")
    else:
        print(f"PDF file not found: {pdf_path}")


if __name__ == "__main__":
    test_extractor()
