"""Tests for path traversal safety."""

import pytest
import tempfile
import shutil
from pathlib import Path
from fastapi.testclient import TestClient
from app.main import app
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


class TestPathTraversalSafety:
    """Test path traversal protection."""
    
    def test_sanitize_filename_removes_path_components(self, temp_storage):
        """Test that filename sanitization removes path components."""
        assert temp_storage._sanitize_filename("../../../etc/passwd") == "passwd"
        assert temp_storage._sanitize_filename("..\\..\\windows\\system32") == "system32"
        assert temp_storage._sanitize_filename("/absolute/path/file.pdf") == "file.pdf"
    
    def test_sanitize_filename_handles_dangerous_chars(self, temp_storage):
        """Test that dangerous characters are removed."""
        assert ".." not in temp_storage._sanitize_filename("file..pdf")
        assert "/" not in temp_storage._sanitize_filename("file/pdf")
        assert "\\" not in temp_storage._sanitize_filename("file\\pdf")
    
    def test_is_safe_path_rejects_path_traversal(self, temp_storage):
        """Test that path traversal attempts are rejected."""
        assert not temp_storage._is_safe_path("../etc/passwd")
        assert not temp_storage._is_safe_path("..\\windows\\system32")
        assert not temp_storage._is_safe_path("inputs/../../../etc/passwd")
        assert not temp_storage._is_safe_path("inputs/..\\..\\windows")
    
    def test_is_safe_path_rejects_absolute_paths(self, temp_storage):
        """Test that absolute paths are rejected."""
        assert not temp_storage._is_safe_path("/etc/passwd")
        assert not temp_storage._is_safe_path("C:\\windows\\system32")
        assert not temp_storage._is_safe_path("D:\\data")
    
    def test_is_safe_path_accepts_valid_paths(self, temp_storage):
        """Test that valid relative paths are accepted."""
        assert temp_storage._is_safe_path("inputs/file.pdf")
        assert temp_storage._is_safe_path("outputs/result.json")
        assert temp_storage._is_safe_path("inputs/subfolder/file.pdf")
    
    def test_get_file_info_prevents_path_traversal(self, temp_storage):
        """Test that get_file_info prevents path traversal."""
        import uuid
        job_id = str(uuid.uuid4())
        temp_storage.ensure_job_directories(job_id)
        
        # Create a test file
        test_file = temp_storage.get_inputs_path(job_id) / "test.pdf"
        test_file.write_bytes(b"test content")
        
        # Valid path should work
        path, name, size = temp_storage.get_file_info(job_id, "inputs/test.pdf")
        assert path.exists()
        
        # Path traversal should fail
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            temp_storage.get_file_info(job_id, "../inputs/test.pdf")
        assert exc_info.value.status_code == 400
        
        with pytest.raises(HTTPException) as exc_info:
            temp_storage.get_file_info(job_id, "inputs/../../../etc/passwd")
        assert exc_info.value.status_code == 400
    
    def test_download_endpoint_prevents_path_traversal(self, client):
        """Test that download endpoint prevents path traversal."""
        # Create a job
        response = client.post("/api/v1/jobs", files=[])
        assert response.status_code == 201
        job_id = response.json()["job_id"]
        
        # Try path traversal
        response = client.get(f"/api/v1/jobs/{job_id}/download?path=../../../etc/passwd")
        assert response.status_code == 400
        
        response = client.get(f"/api/v1/jobs/{job_id}/download?path=..\\..\\windows")
        assert response.status_code == 400
        
        # Try absolute path
        response = client.get(f"/api/v1/jobs/{job_id}/download?path=/etc/passwd")
        assert response.status_code == 400

