"""PDF processing service for Assisted Manual Mode."""

import json
import shutil
from pathlib import Path
from typing import List, Dict

# Optional imports
try:
    import fitz  # PyMuPDF
    _PYMUPDF_AVAILABLE = True
except ImportError:
    _PYMUPDF_AVAILABLE = False
    fitz = None

try:
    import cv2
    _OPENCV_AVAILABLE = True
except ImportError:
    _OPENCV_AVAILABLE = False
    cv2 = None

from app.storage.file_storage import FileStorage


class PDFService:
    """Service for PDF processing operations."""
    
    def __init__(self):
        """Initialize PDF service."""
        self.file_storage = FileStorage()
        self.dpi = 300  # Render resolution
    
    def upload_and_render_pdf(self, job_id: str, pdf_file: Path) -> Dict:
        """Upload PDF and render page images.
        
        Requires PyMuPDF (fitz) to be installed.
        """
        if not _PYMUPDF_AVAILABLE:
            raise ImportError("PyMuPDF (fitz) is required for PDF rendering. Install with: pip install PyMuPDF")
        """Upload PDF and render page images.
        
        Args:
            job_id: Job identifier
            pdf_file: Path to uploaded PDF file
            
        Returns:
            Dictionary with page count and rendered image paths
        """
        # Save PDF to inputs/source.pdf
        inputs_path = self.file_storage.get_inputs_path(job_id)
        inputs_path.mkdir(parents=True, exist_ok=True)
        
        source_pdf_path = inputs_path / "source.pdf"
        shutil.copy2(pdf_file, source_pdf_path)
        
        # Create outputs/pdf_pages directory
        outputs_path = self.file_storage.get_outputs_path(job_id)
        pages_dir = outputs_path / "pdf_pages"
        pages_dir.mkdir(parents=True, exist_ok=True)
        
        # Open PDF and render pages
        doc = fitz.open(str(source_pdf_path))
        page_images = []
        
        for page_num in range(len(doc)):
            page = doc[page_num]
            
            # Render page at 300 DPI
            # fitz uses 72 DPI as base, so scale factor = 300/72
            mat = fitz.Matrix(300/72, 300/72)
            pix = page.get_pixmap(matrix=mat)
            
            # Save as PNG
            image_path = pages_dir / f"page_{page_num}.png"
            pix.save(str(image_path))
            page_images.append(f"pdf_pages/page_{page_num}.png")
        
        page_count = len(doc)
        doc.close()
        
        return {
            "page_count": page_count,
            "page_images": page_images,
            "source_pdf": "inputs/source.pdf"
        }
    
    def detect_views(self, job_id: str) -> List[Dict]:
        """Detect candidate view rectangles on rendered PDF pages.

        Uses OpenCV when available.  Falls back to a single full-page synthetic
        view per page (using PIL for dimensions) so the LLM pipeline can proceed
        on headless servers where libGL / cv2 is unavailable.
        """
        outputs_path = self.file_storage.get_outputs_path(job_id)
        pages_dir = outputs_path / "pdf_pages"
        views_dir = outputs_path / "pdf_views"
        views_dir.mkdir(parents=True, exist_ok=True)

        if not pages_dir.exists():
            raise FileNotFoundError(f"PDF pages not found for job {job_id}. Upload PDF first.")

        page_files = sorted(pages_dir.glob("page_*.png"))

        # ── cv2-based detection ──────────────────────────────────────────────
        if _OPENCV_AVAILABLE:
            return self._detect_views_cv2(page_files, views_dir)

        # ── PIL fallback: one full-page view per page ────────────────────────
        return self._detect_views_fallback(page_files, views_dir)

    def _detect_views_cv2(self, page_files, views_dir) -> List[Dict]:
        """OpenCV-based view detection (original implementation)."""
        
        all_views = []
        
        for page_file in page_files:
            page_num = int(page_file.stem.split("_")[1])
            
            # Load image
            img = cv2.imread(str(page_file))
            if img is None:
                continue
            
            # Convert to grayscale
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            
            # Detect rectangles using contour detection
            # Apply threshold to get binary image
            _, thresh = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY_INV)
            
            # Find contours
            contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            
            # Filter contours by size and aspect ratio to find likely view rectangles
            views = []
            img_height, img_width = gray.shape
            
            for contour in contours:
                # Get bounding rectangle
                x, y, w, h = cv2.boundingRect(contour)
                
                # Filter by size (must be reasonably large)
                min_area = (img_width * img_height) * 0.01  # At least 1% of image
                area = w * h
                
                if area < min_area:
                    continue
                
                # Filter by aspect ratio (views are typically rectangular, not too elongated)
                aspect_ratio = w / h if h > 0 else 0
                if aspect_ratio < 0.1 or aspect_ratio > 10:
                    continue
                
                # Store view rectangle (normalized coordinates: 0-1)
                view = {
                    "bbox": [
                        x / img_width,  # x_min (normalized)
                        y / img_height,  # y_min (normalized)
                        (x + w) / img_width,  # x_max (normalized)
                        (y + h) / img_height  # y_max (normalized)
                    ],
                    "bbox_pixels": [x, y, w, h],  # Pixel coordinates
                    "area": area,
                    "confidence": 0.5  # Placeholder confidence
                }
                views.append(view)
            
            # Sort by area (largest first)
            views.sort(key=lambda v: v["area"], reverse=True)
            
            # Limit to top 10 views per page
            views = views[:10]
            
            # Save views JSON for this page
            views_file = views_dir / f"page_{page_num}_views.json"
            with open(views_file, 'w') as f:
                json.dump({
                    "page": page_num,
                    "views": views,
                    "image_size": [img_width, img_height]
                }, f, indent=2)
            
            all_views.append({
                "page": page_num,
                "views": views,
                "image_size": [img_width, img_height]
            })
        
        return all_views

    def _detect_views_fallback(self, page_files, views_dir) -> List[Dict]:
        """PIL-based fallback: one full-page synthetic view per page.

        Used when cv2/libGL is unavailable (headless server).  The LLM pipeline
        only needs a bounding box to crop from — a full-page box works fine.
        """
        try:
            from PIL import Image as _PIL_Image
        except ImportError:
            _PIL_Image = None

        all_views = []
        for page_file in page_files:
            page_num = int(page_file.stem.split("_")[1])

            # Get image dimensions
            if _PIL_Image is not None:
                try:
                    with _PIL_Image.open(str(page_file)) as im:
                        img_width, img_height = im.size
                except Exception:
                    img_width, img_height = 2480, 3508  # A4 at 300 DPI fallback
            else:
                img_width, img_height = 2480, 3508

            # One full-page view covering the entire image
            views = [{
                "bbox": [0.0, 0.0, 1.0, 1.0],
                "bbox_pixels": [0, 0, img_width, img_height],
                "area": img_width * img_height,
                "confidence": 0.4,
                "source": "fallback_fullpage",
            }]

            views_file = views_dir / f"page_{page_num}_views.json"
            with open(views_file, "w") as f:
                json.dump({
                    "page": page_num,
                    "views": views,
                    "image_size": [img_width, img_height],
                }, f, indent=2)

            all_views.append({
                "page": page_num,
                "views": views,
                "image_size": [img_width, img_height],
            })

        return all_views
