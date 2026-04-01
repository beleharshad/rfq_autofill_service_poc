"""Regression tests for baseline part to prevent breaking changes."""

import pytest
import json
import tempfile
import shutil
import uuid
from pathlib import Path
from fastapi.testclient import TestClient
from app.main import app
from app.storage.file_storage import FileStorage
from app.services.job_service import JobService
from app.geometry.geometry_2d import Profile2D, LineSegment, Point2D

# Baseline dimensions from test_manual_pdf_profile.py
L = 4.25
OD1_diameter = 1.63
OD2_diameter = 0.806
ID1_diameter = 1.13
ID2_diameter = 0.753
OD1_radius = OD1_diameter / 2.0
OD2_radius = OD2_diameter / 2.0
ID1_radius = ID1_diameter / 2.0
ID2_radius = ID2_diameter / 2.0
yS = 3.27  # Shoulder station

# Expected values (from part_summary.json)
EXPECTED_SEGMENTS_COUNT = 2
EXPECTED_VOLUME = 3.607785  # in^3 (tolerance: 0.0001)
EXPECTED_SURFACE_AREA = 36.436083  # in^2 (tolerance: 0.0001)
TOLERANCE = 0.0001

# Expected segment data
EXPECTED_SEGMENT_1 = {
    "z_start": 0.0,
    "z_end": 3.27,
    "od_diameter": 1.63,
    "id_diameter": 1.13,
}

EXPECTED_SEGMENT_2 = {
    "z_start": 3.27,
    "z_end": 4.25,
    "od_diameter": 0.806,
    "id_diameter": 0.753,
}


def create_baseline_profile() -> Profile2D:
    """Create baseline Profile2D from PDF dimensions."""
    profile = Profile2D()
    
    # Segment 1: ID region (main) - vertical from (ID1_radius, 0) to (ID1_radius, yS)
    profile.add_primitive(LineSegment(
        Point2D(ID1_radius, 0.0),
        Point2D(ID1_radius, yS)
    ))
    
    # Segment 2: ID step - horizontal from (ID1_radius, yS) to (ID2_radius, yS)
    profile.add_primitive(LineSegment(
        Point2D(ID1_radius, yS),
        Point2D(ID2_radius, yS)
    ))
    
    # Segment 3: ID region (right end) - vertical from (ID2_radius, yS) to (ID2_radius, L)
    profile.add_primitive(LineSegment(
        Point2D(ID2_radius, yS),
        Point2D(ID2_radius, L)
    ))
    
    # Segment 4: Right face - horizontal from (ID2_radius, L) to (OD2_radius, L)
    profile.add_primitive(LineSegment(
        Point2D(ID2_radius, L),
        Point2D(OD2_radius, L)
    ))
    
    # Segment 5: OD region (right end) - vertical from (OD2_radius, L) to (OD2_radius, yS)
    profile.add_primitive(LineSegment(
        Point2D(OD2_radius, L),
        Point2D(OD2_radius, yS)
    ))
    
    # Segment 6: OD step - horizontal from (OD2_radius, yS) to (OD1_radius, yS)
    profile.add_primitive(LineSegment(
        Point2D(OD2_radius, yS),
        Point2D(OD1_radius, yS)
    ))
    
    # Segment 7: OD region (main) - vertical from (OD1_radius, yS) to (OD1_radius, 0)
    profile.add_primitive(LineSegment(
        Point2D(OD1_radius, yS),
        Point2D(OD1_radius, 0.0)
    ))
    
    # Segment 8: Left face - horizontal from (OD1_radius, 0) to (ID1_radius, 0) (closing loop)
    profile.add_primitive(LineSegment(
        Point2D(OD1_radius, 0.0),
        Point2D(ID1_radius, 0.0)  # Back to start
    ))
    
    return profile


def profile_to_api_format(profile: Profile2D) -> list:
    """Convert Profile2D to API request format."""
    primitives = []
    for prim in profile.get_primitives():
        if isinstance(prim, LineSegment):
            primitives.append({
                "type": "line",
                "start": {"x": prim.start_point.x, "y": prim.start_point.y},
                "end": {"x": prim.end_point.x, "y": prim.end_point.y}
            })
    return primitives


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def job_id(client):
    """Create a test job and return its ID."""
    # Create a minimal job by uploading a dummy file
    response = client.post(
        "/api/v1/jobs",
        files={"files": ("test.pdf", b"dummy pdf content", "application/pdf")}
    )
    assert response.status_code == 200
    return response.json()["job_id"]


class TestBaselineRegression:
    """Regression tests for baseline part."""
    
    def test_profile2d_mode_outputs_exist(self, client, job_id):
        """Test that Profile2D mode produces expected output files."""
        # Create baseline profile
        profile = create_baseline_profile()
        primitives = profile_to_api_format(profile)
        
        # Submit Profile2D request
        response = client.post(
            f"/api/v1/jobs/{job_id}/profile2d",
            json={
                "primitives": primitives,
                "axis_point": {"x": 0.0, "y": 0.0}
            }
        )
        
        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "DONE"
        
        # Check that outputs exist
        file_storage = FileStorage()
        outputs_path = file_storage.get_outputs_path(job_id)
        
        step_file = outputs_path / "model.step"
        summary_file = outputs_path / "part_summary.json"
        
        assert step_file.exists(), f"model.step not found at {step_file}"
        assert summary_file.exists(), f"part_summary.json not found at {summary_file}"
    
    def test_profile2d_mode_numeric_values(self, client, job_id):
        """Test that Profile2D mode produces expected numeric values."""
        # Create baseline profile
        profile = create_baseline_profile()
        primitives = profile_to_api_format(profile)
        
        # Submit Profile2D request
        response = client.post(
            f"/api/v1/jobs/{job_id}/profile2d",
            json={
                "primitives": primitives,
                "axis_point": {"x": 0.0, "y": 0.0}
            }
        )
        
        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "DONE"
        
        # Load part_summary.json
        file_storage = FileStorage()
        outputs_path = file_storage.get_outputs_path(job_id)
        summary_file = outputs_path / "part_summary.json"
        
        with open(summary_file, 'r') as f:
            summary = json.load(f)
        
        # Assert segments count
        assert len(summary["segments"]) == EXPECTED_SEGMENTS_COUNT, \
            f"Expected {EXPECTED_SEGMENTS_COUNT} segments, got {len(summary['segments'])}"
        
        # Assert segment 1 dimensions
        seg1 = summary["segments"][0]
        assert abs(seg1["z_start"] - EXPECTED_SEGMENT_1["z_start"]) < TOLERANCE
        assert abs(seg1["z_end"] - EXPECTED_SEGMENT_1["z_end"]) < TOLERANCE
        assert abs(seg1["od_diameter"] - EXPECTED_SEGMENT_1["od_diameter"]) < TOLERANCE
        assert abs(seg1["id_diameter"] - EXPECTED_SEGMENT_1["id_diameter"]) < TOLERANCE
        
        # Assert segment 2 dimensions
        seg2 = summary["segments"][1]
        assert abs(seg2["z_start"] - EXPECTED_SEGMENT_2["z_start"]) < TOLERANCE
        assert abs(seg2["z_end"] - EXPECTED_SEGMENT_2["z_end"]) < TOLERANCE
        assert abs(seg2["od_diameter"] - EXPECTED_SEGMENT_2["od_diameter"]) < TOLERANCE
        assert abs(seg2["id_diameter"] - EXPECTED_SEGMENT_2["id_diameter"]) < TOLERANCE
        
        # Assert total volume
        total_volume = summary["totals"]["volume_in3"]
        assert abs(total_volume - EXPECTED_VOLUME) < TOLERANCE, \
            f"Expected volume ~{EXPECTED_VOLUME}, got {total_volume}"
        
        # Assert total surface area
        total_surface_area = summary["totals"]["total_surface_area_in2"]
        assert abs(total_surface_area - EXPECTED_SURFACE_AREA) < TOLERANCE, \
            f"Expected surface area ~{EXPECTED_SURFACE_AREA}, got {total_surface_area}"
    
    def test_stack_mode_outputs_exist(self, client, job_id):
        """Test that Stack mode produces expected output files."""
        # Create stack input matching baseline profile
        stack_input = {
            "units": "in",
            "segments": [
                {
                    "z_start": 0.0,
                    "z_end": 3.27,
                    "od_diameter": 1.63,
                    "id_diameter": 1.13
                },
                {
                    "z_start": 3.27,
                    "z_end": 4.25,
                    "od_diameter": 0.806,
                    "id_diameter": 0.753
                }
            ]
        }
        
        # Save stack input
        response = client.post(
            f"/api/v1/jobs/{job_id}/stack-input",
            json=stack_input
        )
        assert response.status_code == 200
        
        # Run analysis
        response = client.post(f"/api/v1/jobs/{job_id}/run")
        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "DONE"
        
        # Check that outputs exist
        file_storage = FileStorage()
        outputs_path = file_storage.get_outputs_path(job_id)
        
        summary_file = outputs_path / "part_summary.json"
        assert summary_file.exists(), f"part_summary.json not found at {summary_file}"
    
    def test_stack_mode_numeric_values(self, client, job_id):
        """Test that Stack mode produces expected numeric values."""
        # Create stack input matching baseline profile
        stack_input = {
            "units": "in",
            "segments": [
                {
                    "z_start": 0.0,
                    "z_end": 3.27,
                    "od_diameter": 1.63,
                    "id_diameter": 1.13
                },
                {
                    "z_start": 3.27,
                    "z_end": 4.25,
                    "od_diameter": 0.806,
                    "id_diameter": 0.753
                }
            ]
        }
        
        # Save stack input
        response = client.post(
            f"/api/v1/jobs/{job_id}/stack-input",
            json=stack_input
        )
        assert response.status_code == 200
        
        # Run analysis
        response = client.post(f"/api/v1/jobs/{job_id}/run")
        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "DONE"
        
        # Load part_summary.json
        file_storage = FileStorage()
        outputs_path = file_storage.get_outputs_path(job_id)
        summary_file = outputs_path / "part_summary.json"
        
        with open(summary_file, 'r') as f:
            summary = json.load(f)
        
        # Assert segments count
        assert len(summary["segments"]) == EXPECTED_SEGMENTS_COUNT, \
            f"Expected {EXPECTED_SEGMENTS_COUNT} segments, got {len(summary['segments'])}"
        
        # Assert total volume
        total_volume = summary["totals"]["volume_in3"]
        assert abs(total_volume - EXPECTED_VOLUME) < TOLERANCE, \
            f"Expected volume ~{EXPECTED_VOLUME}, got {total_volume}"
        
        # Assert total surface area
        total_surface_area = summary["totals"]["total_surface_area_in2"]
        assert abs(total_surface_area - EXPECTED_SURFACE_AREA) < TOLERANCE, \
            f"Expected surface area ~{EXPECTED_SURFACE_AREA}, got {total_surface_area}"
    
    def test_profile2d_and_stack_mode_same_totals(self, client):
        """Test that Profile2D mode and Stack mode produce the same totals."""
        # Create two jobs
        job1_response = client.post(
            "/api/v1/jobs",
            files={"files": ("test.pdf", b"dummy pdf content", "application/pdf")}
        )
        job1_id = job1_response.json()["job_id"]
        
        job2_response = client.post(
            "/api/v1/jobs",
            files={"files": ("test.pdf", b"dummy pdf content", "application/pdf")}
        )
        job2_id = job2_response.json()["job_id"]
        
        # Profile2D mode
        profile = create_baseline_profile()
        primitives = profile_to_api_format(profile)
        
        response = client.post(
            f"/api/v1/jobs/{job1_id}/profile2d",
            json={
                "primitives": primitives,
                "axis_point": {"x": 0.0, "y": 0.0}
            }
        )
        assert response.status_code == 200
        assert response.json()["status"] == "DONE"
        
        # Stack mode
        stack_input = {
            "units": "in",
            "segments": [
                {
                    "z_start": 0.0,
                    "z_end": 3.27,
                    "od_diameter": 1.63,
                    "id_diameter": 1.13
                },
                {
                    "z_start": 3.27,
                    "z_end": 4.25,
                    "od_diameter": 0.806,
                    "id_diameter": 0.753
                }
            ]
        }
        
        response = client.post(
            f"/api/v1/jobs/{job2_id}/stack-input",
            json=stack_input
        )
        assert response.status_code == 200
        
        response = client.post(f"/api/v1/jobs/{job2_id}/run")
        assert response.status_code == 200
        assert response.json()["status"] == "DONE"
        
        # Compare totals
        file_storage = FileStorage()
        summary1_file = file_storage.get_outputs_path(job1_id) / "part_summary.json"
        summary2_file = file_storage.get_outputs_path(job2_id) / "part_summary.json"
        
        with open(summary1_file, 'r') as f:
            summary1 = json.load(f)
        
        with open(summary2_file, 'r') as f:
            summary2 = json.load(f)
        
        # Compare volumes
        vol1 = summary1["totals"]["volume_in3"]
        vol2 = summary2["totals"]["volume_in3"]
        assert abs(vol1 - vol2) < TOLERANCE, \
            f"Volume mismatch: Profile2D={vol1}, Stack={vol2}"
        
        # Compare surface areas
        area1 = summary1["totals"]["total_surface_area_in2"]
        area2 = summary2["totals"]["total_surface_area_in2"]
        assert abs(area1 - area2) < TOLERANCE, \
            f"Surface area mismatch: Profile2D={area1}, Stack={area2}"

