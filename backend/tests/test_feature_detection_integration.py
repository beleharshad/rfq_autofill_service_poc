"""Integration tests for feature detection pipeline."""

import json
import os
import pytest
import shutil
import tempfile
from pathlib import Path
from fastapi.testclient import TestClient
from app.main import app
from app.services.pdf_service import PDFService
from app.services.auto_detect_service import AutoDetectService
from app.services.stack_inference_service import StackInferenceService
from app.services.feature_detection_service import FeatureDetectionService
from app.services.cv_feature_detection_service import CVFeatureDetectionService
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
    """Get the sample PDF for testing."""
    pdf_path = Path(__file__).parent / "assets" / "050dz0014_B.pdf"
    if not pdf_path.exists():
        pytest.skip(f"Sample PDF not found at {pdf_path}")
    return pdf_path


class TestFeatureDetectionIntegration:
    """Integration tests for the complete feature detection pipeline."""

    def test_feature_detection_on_sample_pdf(self, client, sample_pdf):
        """Test feature detection on the sample PDF."""
        # Create job
        response = client.post(
            "/api/v1/jobs",
            files={"files": ("test.pdf", b"dummy", "application/pdf")}
        )
        assert response.status_code == 200
        job_id = response.json()["job_id"]

        # Upload PDF and run basic processing
        with open(sample_pdf, "rb") as f:
            response = client.post(
                f"/api/v1/jobs/{job_id}/pdf/upload",
                files={"file": ("source.pdf", f, "application/pdf")}
            )
        assert response.status_code == 200

        # Run view detection and stack inference
        client.post(f"/api/v1/jobs/{job_id}/pdf/detect_views")
        client.post(f"/api/v1/jobs/{job_id}/pdf/infer_stack_from_view")

        # Run text-based feature detection
        response = client.post(f"/api/v1/jobs/{job_id}/pdf/detect_features_text")
        assert response.status_code == 200
        result = response.json()
        assert "success" in result

        # Verify features were created and merged
        file_storage = FileStorage()
        outputs_path = file_storage.get_outputs_path(job_id)
        summary_file = outputs_path / "part_summary.json"

        assert summary_file.exists()
        with open(summary_file, 'r') as f:
            part_summary = json.load(f)

        assert "features" in part_summary
        features = part_summary["features"]

        # Verify feature structure
        assert "holes" in features
        assert "slots" in features
        assert "chamfers" in features
        assert "fillets" in features
        assert "threads" in features
        assert "meta" in features

        # Store snapshot for regression testing
        self._store_snapshot(job_id, "text_only")

    def _is_opencv_available(self):
        """Check if OpenCV is available."""
        try:
            import cv2
            return True
        except ImportError:
            return False

    def _store_snapshot(self, job_id: str, suffix: str):
        """Store output snapshot for regression testing."""
        file_storage = FileStorage()
        outputs_path = file_storage.get_outputs_path(job_id)

        # Create snapshots directory
        snapshots_dir = Path(__file__).parent / "snapshots"
        snapshots_dir.mkdir(exist_ok=True)

        # Store part_summary with features
        summary_file = outputs_path / "part_summary.json"
        if summary_file.exists():
            snapshot_path = snapshots_dir / f"sample_050dz0014_B_part_summary_{suffix}.json"
            shutil.copy2(summary_file, snapshot_path)

    def _is_opencv_available(self):
        """Check if OpenCV is available."""
        try:
            import cv2
            return True
        except ImportError:
            return False

    def test_cv_detection_graceful_skip(self, client, sample_pdf):
        """Test that CV detection is properly skipped when OpenCV unavailable."""
        cv_available = self._is_opencv_available()

        if not cv_available:
            # Create job and run basic processing
            response = client.post(
                "/api/v1/jobs",
                files={"files": ("test.pdf", b"dummy", "application/pdf")}
            )
            job_id = response.json()["job_id"]

            # Upload and process
            with open(sample_pdf, "rb") as f:
                client.post(
                    f"/api/v1/jobs/{job_id}/pdf/upload",
                    files={"file": ("source.pdf", f, "application/pdf")}
                )

            # CV detection should be skipped gracefully
            response = client.post(f"/api/v1/jobs/{job_id}/pdf/detect_features_cv")
            assert response.status_code == 200

            result = response.json()
            assert not result["success"]
            assert "OpenCV" in result["error"]