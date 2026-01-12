"""Unit tests for job mode metadata."""

import pytest
import tempfile
import shutil
from pathlib import Path
from fastapi.testclient import TestClient
from app.main import app
from app.storage.job_storage import JobStorage
from app.services.job_service import JobService
from app.models.job import JobMode, JobStatus


@pytest.fixture
def temp_db():
    """Create temporary database for testing."""
    temp_dir = tempfile.mkdtemp()
    db_path = Path(temp_dir) / "test_jobs.db"
    storage = JobStorage(db_path=str(db_path))
    yield storage
    shutil.rmtree(temp_dir)


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


class TestJobModeStorage:
    """Test job mode storage in database."""
    
    def test_create_job_with_mode(self, temp_db):
        """Test creating a job with mode."""
        job_id = "test-job-1"
        temp_db.create_job(job_id, name="Test Job", mode=JobMode.ASSISTED_MANUAL)
        
        job = temp_db.get_job(job_id)
        assert job is not None
        assert job.job_id == job_id
        assert job.mode == JobMode.ASSISTED_MANUAL
    
    def test_create_job_without_mode(self, temp_db):
        """Test creating a job without mode (backward compatibility)."""
        job_id = "test-job-2"
        temp_db.create_job(job_id, name="Test Job")
        
        job = temp_db.get_job(job_id)
        assert job is not None
        assert job.mode is None
    
    def test_update_job_mode(self, temp_db):
        """Test updating job mode."""
        job_id = "test-job-3"
        temp_db.create_job(job_id, name="Test Job")
        
        # Initially no mode
        job = temp_db.get_job(job_id)
        assert job.mode is None
        
        # Update mode
        temp_db.update_job_mode(job_id, JobMode.AUTO_CONVERT)
        
        # Verify mode updated
        job = temp_db.get_job(job_id)
        assert job.mode == JobMode.AUTO_CONVERT
    
    def test_job_mode_enum_values(self):
        """Test JobMode enum values."""
        assert JobMode.ASSISTED_MANUAL == "assisted_manual"
        assert JobMode.AUTO_CONVERT == "auto_convert"


class TestJobServiceMode:
    """Test job service mode operations."""
    
    def test_create_job_with_mode(self, temp_db):
        """Test JobService.create_job with mode."""
        service = JobService()
        service.job_storage = temp_db
        
        job_id = service.create_job(name="Test", mode=JobMode.AUTO_CONVERT)
        
        job = service.get_job(job_id)
        assert job.mode == JobMode.AUTO_CONVERT
    
    def test_set_job_mode(self, temp_db):
        """Test JobService.set_job_mode."""
        service = JobService()
        service.job_storage = temp_db
        
        job_id = service.create_job(name="Test")
        
        # Initially no mode
        job = service.get_job(job_id)
        assert job.mode is None
        
        # Set mode
        service.set_job_mode(job_id, JobMode.ASSISTED_MANUAL)
        
        # Verify mode set
        job = service.get_job(job_id)
        assert job.mode == JobMode.ASSISTED_MANUAL
    
    def test_set_job_mode_invalid(self, temp_db):
        """Test JobService.set_job_mode with invalid mode."""
        from fastapi import HTTPException
        
        service = JobService()
        service.job_storage = temp_db
        
        job_id = service.create_job(name="Test")
        
        # Try invalid mode
        with pytest.raises(HTTPException) as exc_info:
            service.set_job_mode(job_id, "invalid_mode")
        
        assert exc_info.value.status_code == 400
        assert "Invalid mode" in str(exc_info.value.detail)
    
    def test_set_job_mode_not_found(self, temp_db):
        """Test JobService.set_job_mode with non-existent job."""
        from fastapi import HTTPException
        
        service = JobService()
        service.job_storage = temp_db
        
        with pytest.raises(HTTPException) as exc_info:
            service.set_job_mode("non-existent", JobMode.ASSISTED_MANUAL)
        
        assert exc_info.value.status_code == 404


class TestJobModeAPI:
    """Test job mode in API responses."""
    
    def test_get_job_returns_mode(self, client):
        """Test that GET /jobs/{id} returns mode in response."""
        # Create job via API
        response = client.post(
            "/api/v1/jobs",
            files={"files": ("test.pdf", b"dummy content", "application/pdf")}
        )
        assert response.status_code == 201
        job_id = response.json()["job_id"]
        
        # Get job
        response = client.get(f"/api/v1/jobs/{job_id}")
        assert response.status_code == 200
        
        job_data = response.json()
        assert "mode" in job_data
        # Mode may be None for backward compatibility
        assert job_data["mode"] is None or job_data["mode"] in [JobMode.ASSISTED_MANUAL, JobMode.AUTO_CONVERT]
    
    def test_job_response_includes_mode_field(self, client):
        """Test that JobResponse includes mode field."""
        response = client.post(
            "/api/v1/jobs",
            files={"files": ("test.pdf", b"dummy content", "application/pdf")}
        )
        assert response.status_code == 201
        
        job_data = response.json()
        assert "mode" in job_data
        # Verify mode is optional (can be None)
        assert job_data["mode"] is None or isinstance(job_data["mode"], str)





