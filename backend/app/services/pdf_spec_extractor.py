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
        # Dimension patterns (supports both inches and mm)
        self.od_patterns = [
            r'(?:MAX\s*)?(?:OD|O\.D\.|OUTER\s*DIA(?:METER)?)[:\s]*[Ã˜âˆ…]?\s*([\d.]+)\s*(?:"|IN|INCH)?',
            r'[Ã˜âˆ…]\s*([\d.]+)\s*(?:"|IN|INCH)?\s*(?:MAX\s*)?(?:OD|O\.D\.)?',
            r'FINISH\s*OD[:\s]*([\d.]+)\s*(?:"|IN)?',
            r'(?:MAX|MAXIMUM)\s*(?:DIAMETER|DIA)[:\s]*[Ã˜âˆ…]?\s*([\d.]+)',
        ]
        
        self.id_patterns = [
            r'(?:ID|I\.D\.|INNER\s*DIA(?:METER)?|BORE)[:\s]*[Ã˜âˆ…]?\s*([\d.]+)\s*(?:"|IN|INCH)?',
            r'[Ã˜âˆ…]\s*([\d.]+)\s*(?:"|IN|INCH)?\s*(?:ID|I\.D\.|BORE)',
            r'FINISH\s*ID[:\s]*([\d.]+)\s*(?:"|IN)?',
        ]
        
        self.length_patterns = [
            r'(?:OVERALL\s*)?(?:LENGTH|LG|L)[:\s]*([\d.]+)\s*(?:"|IN|INCH)?',
            r'(?:MAX|MAXIMUM)\s*LENGTH[:\s]*([\d.]+)',
            r'FINISH\s*(?:LENGTH|LEN)[:\s]*([\d.]+)\s*(?:"|IN)?',
        ]
        
        # Part metadata patterns
        self.part_no_patterns = [
            r'(?:PART\s*(?:NO|NUMBER|#))[:\s]*([A-Z0-9_-]+)',
            r'(?:P/N|PN)[:\s]*([A-Z0-9_-]+)',
            r'(?:DWG\s*(?:NO|NUMBER))[:\s]*([A-Z0-9_-]+)',
        ]
        
        self.material_patterns = [
            r'(?:MAT(?:ERIAL)?|MATL)[:\s]*([A-Z0-9-]+)',
            r'(?:SPEC|SPECIFICATION)[:\s]*([A-Z0-9-]+)',
            r'(\d{2,3}-\d{2}-\d{2})',  # Pattern like 65-45-12
        ]
        
        self.qty_patterns = [
            r'(?:QTY|QUANTITY)[:\s]*(\d+)',
            r'(?:MOQ)[:\s]*(\d+)',
        ]
        
        self.revision_patterns = [
            r'(?:REV|REVISION)[:\s]*([A-Z0-9]+)',
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
        try:
            # Workaround for Windows OpenMP runtime conflicts (torch/numpy).
            os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

            from pdf2image import convert_from_path
            import easyocr
            import numpy as np
            
            print(f"Converting PDF to images...")
            # Convert PDF to images (300 DPI for better quality)
            images = convert_from_path(pdf_path, dpi=300)
            
            print(f"Initializing EasyOCR reader...")
            # Initialize EasyOCR reader (English only for speed)
            reader = easyocr.Reader(['en'], gpu=False)
            
            text = ""
            for i, img in enumerate(images):
                print(f"OCR processing page {i+1}/{len(images)}...")
                
                # Convert PIL Image to numpy array
                img_array = np.array(img)
                
                # Perform OCR
                results = reader.readtext(img_array)
                
                # Extract text from results
                for (bbox, text_content, confidence) in results:
                    # Only use high-confidence results
                    if confidence > 0.5:
                        text += text_content + " "
                
                text += "\n"
            
            print(f"OCR completed. Extracted {len(text)} characters.")
            return text
            
        except ImportError as e:
            print(f"OCR libraries not available: {e}")
            print("Please install: pip install pdf2image easyocr")
            print("Note: pdf2image requires poppler. On Windows, download from:")
            print("  http://blog.alivate.com.au/poppler-windows/")
            return ""
        except Exception as e:
            print(f"Error in OCR extraction: {e}")
            return ""
    
    def _extract_specifications(self, text: str, pdf_path: str) -> Dict[str, Any]:
        """Extract all specifications from text."""
        specs = {}
        
        # Extract part number (try from filename first, then from text)
        part_no_from_file = self._extract_part_no_from_filename(pdf_path)
        part_no_from_text = self._extract_with_patterns(text, self.part_no_patterns)
        specs["part_no"] = part_no_from_file or part_no_from_text
        
        # Extract dimensions (inches)
        specs["finish_od_in"] = self._extract_dimension(text, self.od_patterns)
        specs["finish_id_in"] = self._extract_dimension(text, self.id_patterns)
        specs["finish_len_in"] = self._extract_dimension(text, self.length_patterns)
        
        # Extract metadata
        specs["material_grade"] = self._extract_with_patterns(text, self.material_patterns)
        specs["qty_moq"] = self._extract_quantity(text)
        specs["revision"] = self._extract_with_patterns(text, self.revision_patterns)
        
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
    
    def _extract_dimension(self, text: str, patterns: List[str]) -> Optional[float]:
        """Extract dimension value from text using multiple patterns."""
        for pattern in patterns:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                try:
                    value_str = match.group(1)
                    value = float(value_str)
                    
                    # Sanity check: dimensions should be reasonable
                    if 0.01 < value < 1000:  # Inches
                        return round(value, 4)
                except (ValueError, IndexError):
                    continue
        
        return None
    
    def _extract_with_patterns(self, text: str, patterns: List[str]) -> Optional[str]:
        """Extract text value using multiple patterns."""
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        
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
