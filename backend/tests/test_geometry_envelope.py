"""Unit tests for Geometry Envelope v1 service."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.rfq_envelope import EnvelopeRequest
from app.services.geometry_envelope_service import GeometryEnvelopeService


def test_compute_envelope_basic():
    """Test basic envelope computation with valid geometry."""
    svc = GeometryEnvelopeService()

    request = EnvelopeRequest(
        rfq_id="test-123",
        part_no="TEST001",
        source={
            "part_summary": {
                "units": {"length": "in"},
                "z_range": [0.0, 3.0],
                "segments": [
                    {"z_start": 0.0, "z_end": 1.0, "od_diameter": 2.0, "id_diameter": 1.0},
                    {"z_start": 1.0, "z_end": 2.0, "od_diameter": 2.1, "id_diameter": 1.0},
                    {"z_start": 2.0, "z_end": 3.0, "od_diameter": 2.0, "id_diameter": 1.0},
                ],
                "scale_report": {"method": "anchor_dimension", "validation_passed": True},
                "inference_metadata": {"overall_confidence": 0.95},
            }
        },
        allowances={"od_in": 0.125, "len_in": 0.25},
        rounding={"od_step": 0.05, "len_step": 0.10},
    )

    response = svc.compute_envelope(request)

    assert response.part_no == "TEST001"
    assert response.status == "AUTO_FILLED"
    assert response.fields.finish_max_od_in.value == 2.1
    assert response.fields.finish_len_in.value == 3.0
    assert response.fields.raw_max_od_in.value == 2.25  # 2.1 + 0.125 + rounded up to 0.05 step
    assert response.fields.raw_len_in.value == 3.3   # 3.0 + 0.25 + rounded up to 0.10 step
    assert response.fields.finish_max_od_in.source == "part_summary.max_od"
    assert response.fields.raw_max_od_in.source == "rule.allowance+rounding"


def test_compute_envelope_scale_estimated_needs_review():
    """Test that scale_method=estimated triggers NEEDS_REVIEW."""
    svc = GeometryEnvelopeService()

    request = EnvelopeRequest(
        rfq_id="test-123",
        part_no="TEST001",
        source={
            "part_summary": {
                "units": {"length": "in"},
                "z_range": [0.0, 2.0],
                "segments": [{"z_start": 0.0, "z_end": 2.0, "od_diameter": 1.5}],
                "scale_report": {"method": "estimated", "validation_passed": True},
                "inference_metadata": {"overall_confidence": 0.9},
            }
        },
        allowances={"od_in": 0.1, "len_in": 0.2},
        rounding={"od_step": 0.05, "len_step": 0.10},
    )

    response = svc.compute_envelope(request)

    assert response.status == "NEEDS_REVIEW"
    assert "SCALE_ESTIMATED" in response.reasons


def test_compute_envelope_validation_failed_rejected():
    """Test that validation_passed=false triggers REJECTED."""
    svc = GeometryEnvelopeService()

    request = EnvelopeRequest(
        rfq_id="test-123",
        part_no="TEST001",
        source={
            "part_summary": {
                "units": {"length": "in"},
                "z_range": [0.0, 1.0],
                "segments": [{"z_start": 0.0, "z_end": 1.0, "od_diameter": 1.0}],
                "scale_report": {"method": "anchor_dimension", "validation_passed": False},
                "inference_metadata": {"overall_confidence": 0.8},
            }
        },
        allowances={"od_in": 0.1, "len_in": 0.2},
        rounding={"od_step": 0.05, "len_step": 0.10},
    )

    response = svc.compute_envelope(request)

    assert response.status == "REJECTED"
    assert "VALIDATION_FAILED" in response.reasons


def test_compute_envelope_missing_od_rejected():
    """Test rejection when no valid OD segments found."""
    svc = GeometryEnvelopeService()

    request = EnvelopeRequest(
        rfq_id="test-123",
        part_no="TEST001",
        source={
            "part_summary": {
                "units": {"length": "in"},
                "z_range": [0.0, 1.0],
                "segments": [{"z_start": 0.0, "z_end": 1.0, "id_diameter": 1.0}],  # No OD
                "scale_report": {"method": "anchor_dimension", "validation_passed": True},
                "inference_metadata": {"overall_confidence": 0.8},
            }
        },
        allowances={"od_in": 0.1, "len_in": 0.2},
        rounding={"od_step": 0.05, "len_step": 0.10},
    )

    response = svc.compute_envelope(request)

    assert response.status == "REJECTED"


def test_compute_envelope_low_confidence_filtering():
    """Test that low confidence segments are filtered out when alternatives exist."""
    svc = GeometryEnvelopeService()

    request = EnvelopeRequest(
        rfq_id="test-123",
        part_no="TEST001",
        source={
            "part_summary": {
                "units": {"length": "in"},
                "z_range": [0.0, 2.0],
                "segments": [
                    {"z_start": 0.0, "z_end": 1.0, "od_diameter": 1.0, "flags": ["low_confidence"]},
                    {"z_start": 1.0, "z_end": 2.0, "od_diameter": 2.0},  # No low_confidence flag
                ],
                "scale_report": {"method": "anchor_dimension", "validation_passed": True},
                "inference_metadata": {"overall_confidence": 0.9},
            }
        },
        allowances={"od_in": 0.1, "len_in": 0.2},
        rounding={"od_step": 0.05, "len_step": 0.10},
    )

    response = svc.compute_envelope(request)

    # Should use 2.0 (the non-low-confidence segment), not 1.0
    assert response.fields.finish_max_od_in.value == 2.0


def test_compute_envelope_units_conversion():
    """Test mm to inches conversion."""
    svc = GeometryEnvelopeService()

    request = EnvelopeRequest(
        rfq_id="test-123",
        part_no="TEST001",
        source={
            "part_summary": {
                "units": {"length": "mm"},
                "z_range": [0.0, 76.2],  # 3 inches in mm
                "segments": [{"z_start": 0.0, "z_end": 76.2, "od_diameter": 50.8}],  # 2 inches in mm
                "scale_report": {"method": "anchor_dimension", "validation_passed": True},
                "inference_metadata": {"overall_confidence": 0.9},
            }
        },
        allowances={"od_in": 0.125, "len_in": 0.25},
        rounding={"od_step": 0.05, "len_step": 0.10},
    )

    response = svc.compute_envelope(request)

    # Should convert mm to inches
    assert abs(response.fields.finish_len_in.value - 3.0) < 1e-10  # 76.2mm = 3 inches
    assert abs(response.fields.finish_max_od_in.value - 2.0) < 1e-10  # 50.8mm = 2 inches


def test_compute_envelope_min_length_gate():
    """Test minimum length gate filtering."""
    svc = GeometryEnvelopeService()

    request = EnvelopeRequest(
        rfq_id="test-123",
        part_no="TEST001",
        source={
            "part_summary": {
                "units": {"length": "in"},
                "z_range": [0.0, 3.0],
                "segments": [
                    {"z_start": 0.0, "z_end": 0.01, "od_diameter": 10.0},  # Too short (< min_gate)
                    {"z_start": 0.0, "z_end": 0.5, "od_diameter": 2.0},   # Valid length
                ],
                "scale_report": {"method": "anchor_dimension", "validation_passed": True},
                "inference_metadata": {"overall_confidence": 0.9},
            }
        },
        allowances={"od_in": 0.1, "len_in": 0.2},
        rounding={"od_step": 0.05, "len_step": 0.10},
    )

    response = svc.compute_envelope(request)

    # Should ignore the 10.0 OD (too short segment) and use 2.0
    assert response.fields.finish_max_od_in.value == 2.0


def test_envelope_api_endpoint():
    """Test the /api/v1/rfq/envelope endpoint."""
    client = TestClient(app)

    request_data = {
        "rfq_id": "test-123",
        "part_no": "TEST001",
        "source": {
            "part_summary": {
                "units": {"length": "in"},
                "z_range": [0.0, 2.0],
                "segments": [{"z_start": 0.0, "z_end": 2.0, "od_diameter": 1.5}],
                "scale_report": {"method": "anchor_dimension", "validation_passed": True},
                "inference_metadata": {"overall_confidence": 0.9},
            }
        },
        "allowances": {"od_in": 0.1, "len_in": 0.2},
        "rounding": {"od_step": 0.05, "len_step": 0.10},
    }

    response = client.post("/api/v1/rfq/envelope", json=request_data)
    assert response.status_code == 200

    data = response.json()
    assert data["part_no"] == "TEST001"
    assert data["status"] == "AUTO_FILLED"
    assert "finish_max_od_in" in data["fields"]
    assert "raw_max_od_in" in data["fields"]
    assert data["fields"]["finish_max_od_in"]["value"] == 1.5
    assert data["fields"]["raw_max_od_in"]["value"] == 1.6  # 1.5 + 0.1 = 1.6 (already multiple of 0.05)
