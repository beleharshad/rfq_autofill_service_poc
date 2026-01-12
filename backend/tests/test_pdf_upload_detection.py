"""Tests for PDF upload and view detection."""

import pytest
import tempfile
import shutil
from pathlib import Path
from fastapi.testclient import TestClient
from app.main import app
from app.services.pdf_service import PDFService
from app.storage.file_storage import FileStorage


@pytest.fixture
def temp_storage():
    """Create temporary storage for testing."""
    temp_dir = tempfile.mkdtemp()
    storage = FileStorage(base_path=str(Path(temp_dir) / "jobs"))
    yield storage
    shutil.rmtree(temp_dir)


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def sample_pdf():
    """Create a minimal PDF file for testing."""
    try:
        import fitz
        # Create a simple PDF with one page
        doc = fitz.open()
        page = doc.new_page()
        # Add some content (rectangle)
        rect = fitz.Rect(50, 50, 200, 150)
        page.draw_rect(rect, color=(0, 0, 0), width=2)
        # Save to temp file
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')
        temp_path = Path(temp_file.name)
        doc.save(str(temp_path))
        doc.close()
        yield temp_path
        if temp_path.exists():
            temp_path.unlink()
    except ImportError:
        pytest.skip("PyMuPDF not available")


class TestPDFUpload:
    """Test PDF upload and rendering."""
    
    def test_upload_pdf_creates_source_file(self, client, sample_pdf):
        """Test that PDF upload saves source.pdf."""
        # Create job
        response = client.post(
            "/api/v1/jobs",
            files={"files": ("test.pdf", b"dummy", "application/pdf")}
        )
        job_id = response.json()["job_id"]
        
        # Upload PDF
        with open(sample_pdf, "rb") as f:
            response = client.post(
                f"/api/v1/jobs/{job_id}/pdf/upload",
                files={"file": ("source.pdf", f, "application/pdf")}
            )
        
        assert response.status_code == 200
        
        # Verify source.pdf exists
        file_storage = FileStorage()
        source_pdf = file_storage.get_inputs_path(job_id) / "source.pdf"
        assert source_pdf.exists(), "source.pdf should be created"
    
    def test_upload_pdf_renders_page_images(self, client, sample_pdf):
        """Test that PDF upload renders page images."""
        # Create job
        response = client.post(
            "/api/v1/jobs",
            files={"files": ("test.pdf", b"dummy", "application/pdf")}
        )
        job_id = response.json()["job_id"]
        
        # Upload PDF
        with open(sample_pdf, "rb") as f:
            response = client.post(
                f"/api/v1/jobs/{job_id}/pdf/upload",
                files={"file": ("source.pdf", f, "application/pdf")}
            )
        
        assert response.status_code == 200
        result = response.json()
        assert "page_count" in result
        assert "page_images" in result
        assert len(result["page_images"]) > 0
        
        # Verify page images exist
        file_storage = FileStorage()
        outputs_path = file_storage.get_outputs_path(job_id)
        
        for page_image in result["page_images"]:
            image_path = outputs_path / page_image
            assert image_path.exists(), f"Page image {page_image} should exist"
            assert image_path.suffix == ".png", "Page images should be PNG"
    
    def test_upload_pdf_invalid_file_type(self, client):
        """Test that non-PDF files are rejected."""
        # Create job
        response = client.post(
            "/api/v1/jobs",
            files={"files": ("test.pdf", b"dummy", "application/pdf")}
        )
        job_id = response.json()["job_id"]
        
        # Try to upload non-PDF
        response = client.post(
            f"/api/v1/jobs/{job_id}/pdf/upload",
            files={"file": ("test.txt", b"not a pdf", "text/plain")}
        )
        
        assert response.status_code == 400
        assert "PDF" in response.json()["detail"]


class TestViewDetection:
    """Test view detection."""
    
    def test_detect_views_creates_json_files(self, client, sample_pdf):
        """Test that view detection creates JSON files."""
        # Create job
        response = client.post(
            "/api/v1/jobs",
            files={"files": ("test.pdf", b"dummy", "application/pdf")}
        )
        job_id = response.json()["job_id"]
        
        # Upload PDF first
        with open(sample_pdf, "rb") as f:
            client.post(
                f"/api/v1/jobs/{job_id}/pdf/upload",
                files={"file": ("source.pdf", f, "application/pdf")}
            )
        
        # Detect views
        response = client.post(f"/api/v1/jobs/{job_id}/pdf/detect_views")
        
        assert response.status_code == 200
        result = response.json()
        assert "pages" in result
        assert "total_views" in result
        
        # Verify view JSON files exist
        file_storage = FileStorage()
        outputs_path = file_storage.get_outputs_path(job_id)
        views_dir = outputs_path / "pdf_views"
        
        assert views_dir.exists(), "pdf_views directory should exist"
        
        # Check for at least one views JSON file
        view_files = list(views_dir.glob("page_*_views.json"))
        assert len(view_files) > 0, "At least one views JSON file should exist"
    
    def test_detect_views_returns_bboxes(self, client, sample_pdf):
        """Test that view detection returns bounding boxes."""
        # Create job
        response = client.post(
            "/api/v1/jobs",
            files={"files": ("test.pdf", b"dummy", "application/pdf")}
        )
        job_id = response.json()["job_id"]
        
        # Upload PDF first
        with open(sample_pdf, "rb") as f:
            client.post(
                f"/api/v1/jobs/{job_id}/pdf/upload",
                files={"file": ("source.pdf", f, "application/pdf")}
            )
        
        # Detect views
        response = client.post(f"/api/v1/jobs/{job_id}/pdf/detect_views")
        
        assert response.status_code == 200
        result = response.json()
        
        # Check structure
        assert "pages" in result
        for page in result["pages"]:
            assert "page" in page
            assert "views" in page
            assert "image_size" in page
            
            for view in page["views"]:
                assert "bbox" in view, "View should have bbox"
                assert len(view["bbox"]) == 4, "bbox should have 4 values [x_min, y_min, x_max, y_max]"
                assert "bbox_pixels" in view, "View should have bbox_pixels"
                assert "area" in view, "View should have area"
    
    def test_detect_views_requires_uploaded_pdf(self, client):
        """Test that view detection fails if PDF not uploaded."""
        # Create job
        response = client.post(
            "/api/v1/jobs",
            files={"files": ("test.pdf", b"dummy", "application/pdf")}
        )
        job_id = response.json()["job_id"]
        
        # Try to detect views without uploading PDF
        response = client.post(f"/api/v1/jobs/{job_id}/pdf/detect_views")
        
        assert response.status_code == 400
        assert "not found" in response.json()["detail"].lower()


class TestPDFService:
    """Test PDFService directly."""
    
    def test_upload_and_render_pdf(self, temp_storage, sample_pdf):
        """Test PDFService.upload_and_render_pdf."""
        service = PDFService()
        service.file_storage = temp_storage
        
        job_id = "test-job-1"
        temp_storage.ensure_job_directories(job_id)
        
        result = service.upload_and_render_pdf(job_id, sample_pdf)
        
        assert "page_count" in result
        assert "page_images" in result
        assert result["page_count"] > 0
        assert len(result["page_images"]) == result["page_count"]
        
        # Verify files exist
        inputs_path = temp_storage.get_inputs_path(job_id)
        source_pdf = inputs_path / "source.pdf"
        assert source_pdf.exists()
        
        outputs_path = temp_storage.get_outputs_path(job_id)
        for page_image in result["page_images"]:
            image_path = outputs_path / page_image
            assert image_path.exists()
    
    def test_detect_views(self, temp_storage, sample_pdf):
        """Test PDFService.detect_views."""
        service = PDFService()
        service.file_storage = temp_storage
        
        job_id = "test-job-2"
        temp_storage.ensure_job_directories(job_id)
        
        # Upload PDF first
        service.upload_and_render_pdf(job_id, sample_pdf)
        
        # Detect views
        views = service.detect_views(job_id)
        
        assert len(views) > 0
        
        # Verify JSON files exist
        outputs_path = temp_storage.get_outputs_path(job_id)
        views_dir = outputs_path / "pdf_views"
        
        for page_data in views:
            page_num = page_data["page"]
            views_file = views_dir / f"page_{page_num}_views.json"
            assert views_file.exists(), f"Views JSON for page {page_num} should exist"
            
            # Verify JSON content
            import json
            with open(views_file, 'r') as f:
                data = json.load(f)
                assert data["page"] == page_num
                assert "views" in data
                assert "image_size" in data

