"""Unit tests for RFQ AutoFill v1 algorithm (backend only)."""

import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.rfq_autofill_service import RFQAutofillService, ceil_to_step, weighted_percentile, weighted_median
from app.storage.file_storage import FileStorage


def test_ceil_to_step_rounds_up_and_keeps_exact_multiples():
    assert ceil_to_step(2.90, 0.05) == 2.90
    assert ceil_to_step(2.901, 0.05) == 2.95
    assert ceil_to_step(1.0, 0.10) == 1.0
    assert ceil_to_step(1.0000000000001, 0.10) == 1.1


def test_weighted_percentile_p85():
    assert weighted_percentile([1.0, 2.0, 3.0], [1.0, 1.0, 1.0], 0.85) == 3.0
    assert weighted_percentile([1.0, 10.0], [9.0, 1.0], 0.85) == 1.0


def test_status_rejected_on_validation_failed():
    svc = RFQAutofillService()
    resp = svc.autofill(
        part_no="050DZ0017",
        part_summary_dict={
            "units": {"length": "in"},
            "z_range": [0.0, 1.0],
            "segments": [{"z_start": 0.0, "z_end": 1.0, "od_diameter": 1.0, "id_diameter": 0.0, "confidence": 0.9}],
            "scale_report": {"method": "anchor_dimension", "validation_passed": False},
            "inference_metadata": {"overall_confidence": 0.9},
        },
        tolerances={"rm_od_allowance_in": 0.1, "rm_len_allowance_in": 0.35},
        step_metrics=None,
    )
    assert resp.status == "REJECTED"
    assert "VALIDATION_FAILED" in resp.reasons


def test_status_needs_review_on_estimated_scale():
    svc = RFQAutofillService()
    resp = svc.autofill(
        part_no="050DZ0017",
        part_summary_dict={
            "units": {"length": "in"},
            "z_range": [0.0, 1.0],
            "segments": [{"z_start": 0.0, "z_end": 1.0, "od_diameter": 1.0, "id_diameter": 0.25, "confidence": 0.9}],
            "scale_report": {"method": "estimated", "validation_passed": True},
            "inference_metadata": {"overall_confidence": 0.9},
        },
        tolerances={"rm_od_allowance_in": 0.1, "rm_len_allowance_in": 0.35},
        step_metrics=None,
    )
    assert resp.status == "NEEDS_REVIEW"
    assert "SCALE_ESTIMATED" in resp.reasons
    # With estimated scale, confidence won't reach AUTO_FILLED thresholds
    assert resp.fields.finish_od_in.confidence < 0.85
    assert resp.fields.finish_len_in.confidence < 0.85


def test_status_auto_filled_on_good_scale_and_id_conf():
    svc = RFQAutofillService()
    resp = svc.autofill(
        part_no="050DZ0017",
        part_summary_dict={
            "units": {"length": "in"},
            "z_range": [0.0, 2.0],
            "segments": [
                {"z_start": 0.0, "z_end": 2.0, "od_diameter": 1.5, "id_diameter": 0.5, "confidence": 0.95, "flags": []}
            ],
            "scale_report": {"method": "anchor_dimension", "validation_passed": True},
            "inference_metadata": {"overall_confidence": 0.95},
        },
        tolerances={"rm_od_allowance_in": 0.1, "rm_len_allowance_in": 0.35},
        step_metrics=None,
    )
    assert resp.status == "AUTO_FILLED"
    assert "SCALE_ESTIMATED" not in resp.reasons


def test_insufficient_geometry_when_no_segments_and_no_z_range():
    svc = RFQAutofillService()
    resp = svc.autofill(
        part_no="050DZ0017",
        part_summary_dict={"units": {"length": "in"}},
        tolerances={"rm_od_allowance_in": 0.1, "rm_len_allowance_in": 0.35},
        step_metrics=None,
    )
    assert resp.status == "REJECTED"
    assert "INSUFFICIENT_GEOMETRY" in resp.reasons


def test_units_mm_are_converted_to_inches():
    svc = RFQAutofillService()
    resp = svc.autofill(
        part_no="050DZ0017",
        part_summary_dict={
            "units": {"length": "mm"},
            "z_range": [0.0, 25.4],  # 1 inch
            "segments": [{"z_start": 0.0, "z_end": 25.4, "od_diameter": 25.4, "id_diameter": 0.0}],
            "scale_report": {"method": "anchor_dimension", "validation_passed": True},
            "inference_metadata": {"overall_confidence": 0.9},
        },
        tolerances={"rm_od_allowance_in": 0.1, "rm_len_allowance_in": 0.35},
        step_metrics=None,
    )
    assert abs((resp.fields.finish_len_in.value or 0.0) - 1.0) < 1e-9
    assert abs((resp.fields.finish_od_in.value or 0.0) - 1.0) < 1e-9


def test_min_len_gate_and_low_confidence_filtering():
    svc = RFQAutofillService()
    resp = svc.autofill(
        part_no="050DZ0017",
        part_summary_dict={
            "units": {"length": "in"},
            "z_range": [0.0, 10.0],
            "segments": [
                # Long but flagged low_confidence (should be ignored if another remains)
                {"z_start": 0.0, "z_end": 10.0, "od_diameter": 5.0, "id_diameter": 0.0, "flags": ["low_confidence"]},
                # Long and not low_confidence -> should win
                {"z_start": 0.0, "z_end": 10.0, "od_diameter": 3.0, "id_diameter": 0.0, "flags": []},
                # Short segment below gate should be ignored
                {"z_start": 0.0, "z_end": 0.01, "od_diameter": 100.0, "id_diameter": 0.0, "flags": []},
            ],
            "scale_report": {"method": "anchor_dimension", "validation_passed": True},
            "inference_metadata": {"overall_confidence": 0.9},
        },
        tolerances={"rm_od_allowance_in": 0.1, "rm_len_allowance_in": 0.35},
        step_metrics=None,
    )
    assert resp.fields.finish_od_in.value == 3.0


def test_job_id_loading_success(monkeypatch):
    job_id = "test-job-123"
    with tempfile.TemporaryDirectory() as tmp:
        fs = FileStorage(base_path=tmp)
        out = fs.get_outputs_path(job_id)
        out.mkdir(parents=True, exist_ok=True)
        summary_path = out / "part_summary.json"
        summary = {
            "units": {"length": "in"},
            "z_range": [0.0, 1.0],
            "segments": [{"z_start": 0.0, "z_end": 1.0, "od_diameter": 1.0, "id_diameter": 0.0}],
            "scale_report": {"method": "anchor_dimension", "validation_passed": True},
            "inference_metadata": {"overall_confidence": 0.9},
        }
        summary_path.write_text(json.dumps(summary), encoding="utf-8")

        # Patch rfq router module to use our temp FileStorage base path
        import app.api.rfq as rfq_mod

        monkeypatch.setattr(rfq_mod, "FileStorage", lambda: FileStorage(base_path=tmp))

        client = TestClient(app)
        resp = client.post(
            "/api/v1/rfq/autofill",
            json={
                "rfq_id": "RFQ-2025-01369",
                "part_no": "050DZ0017",
                "source": {"job_id": job_id, "part_summary": None, "step_metrics": None},
                "tolerances": {"rm_od_allowance_in": 0.1, "rm_len_allowance_in": 0.35},
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["fields"]["finish_od_in"]["value"] == 1.0


def test_job_id_missing_part_summary_returns_reason(monkeypatch):
    job_id = "test-job-missing"
    with tempfile.TemporaryDirectory() as tmp:
        import app.api.rfq as rfq_mod

        monkeypatch.setattr(rfq_mod, "FileStorage", lambda: FileStorage(base_path=tmp))

        client = TestClient(app)
        resp = client.post(
            "/api/v1/rfq/autofill",
            json={
                "rfq_id": "RFQ-2025-01369",
                "part_no": "050DZ0017",
                "source": {"job_id": job_id, "part_summary": None, "step_metrics": None},
                "tolerances": {"rm_od_allowance_in": 0.1, "rm_len_allowance_in": 0.35},
            },
        )
        assert resp.status_code == 404, resp.text


def test_z_range_is_preferred_over_segment_derived_length():
    svc = RFQAutofillService()
    resp = svc.autofill(
        part_no="050DZ0017",
        part_summary_dict={
            "units": {"length": "in"},
            # z_range indicates 10", but segments only span 0..8"
            "z_range": [0.0, 10.0],
            "segments": [
                {"z_start": 0.0, "z_end": 8.0, "od_diameter": 2.0, "id_diameter": 0.0},
            ],
            "scale_report": {"method": "anchor_dimension", "validation_passed": True},
            "inference_metadata": {"overall_confidence": 0.9},
        },
        tolerances={"rm_od_allowance_in": 0.1, "rm_len_allowance_in": 0.35},
        step_metrics=None,
    )
    assert resp.fields.finish_len_in.value == 10.0
    assert resp.debug.overall_len_in == 10.0


def test_bore_coverage_pct_is_computed_from_bore_segment_lengths():
    svc = RFQAutofillService()
    resp = svc.autofill(
        part_no="050DZ0017",
        part_summary_dict={
            "units": {"length": "in"},
            "z_range": [0.0, 10.0],
            "segments": [
                # Non-bore
                {"z_start": 0.0, "z_end": 2.0, "od_diameter": 2.0, "id_diameter": 0.0},
                # Bore: 3 inches
                {"z_start": 2.0, "z_end": 5.0, "od_diameter": 2.0, "id_diameter": 1.0},
                # Non-bore
                {"z_start": 5.0, "z_end": 7.0, "od_diameter": 2.0, "id_diameter": 0.0},
                # Bore: 1 inch
                {"z_start": 7.0, "z_end": 8.0, "od_diameter": 2.0, "id_diameter": 1.0},
                # Non-bore
                {"z_start": 8.0, "z_end": 10.0, "od_diameter": 2.0, "id_diameter": 0.0},
            ],
            "scale_report": {"method": "anchor_dimension", "validation_passed": True},
            "inference_metadata": {"overall_confidence": 0.9},
        },
        tolerances={"rm_od_allowance_in": 0.1, "rm_len_allowance_in": 0.35},
        step_metrics=None,
    )
    # Bore coverage = (3 + 1) / 10 = 40%
    assert resp.debug.bore_coverage_pct == 40.0


def test_id_noise_filter_ignores_tiny_ids():
    svc = RFQAutofillService()
    resp = svc.autofill(
        part_no="050DZ0017",
        part_summary_dict={
            "units": {"length": "in"},
            "z_range": [0.0, 1.0],
            "segments": [
                {"z_start": 0.0, "z_end": 1.0, "od_diameter": 2.0, "id_diameter": 0.01, "confidence": 0.9},
            ],
            "scale_report": {"method": "anchor_dimension", "validation_passed": True},
            "inference_metadata": {"overall_confidence": 0.9},
        },
        tolerances={"rm_od_allowance_in": 0.1, "rm_len_allowance_in": 0.35},
        step_metrics=None,
    )
    assert resp.fields.finish_id_in.value == 0.0


def test_od_spike_suspect_reason_is_set_when_supported_length_is_tiny():
    svc = RFQAutofillService()
    resp = svc.autofill(
        part_no="050DZ0017",
        part_summary_dict={
            "units": {"length": "in"},
            "z_range": [0.0, 1.0],
            "segments": [
                {"z_start": 0.0, "z_end": 0.03, "od_diameter": 2.0, "id_diameter": 0.0, "confidence": 0.9},
                {"z_start": 0.03, "z_end": 1.0, "od_diameter": 1.0, "id_diameter": 0.0, "confidence": 0.9},
            ],
            "scale_report": {"method": "anchor_dimension", "validation_passed": True},
            "inference_metadata": {"overall_confidence": 0.9},
        },
        tolerances={"rm_od_allowance_in": 0.1, "rm_len_allowance_in": 0.35},
        step_metrics=None,
    )
    assert resp.fields.finish_od_in.value == 2.0
    assert "OD_SPIKE_SUSPECT" in resp.reasons
    assert resp.debug.od_spike_suspect is True
    # Anchor+valid+seg_conf>0.85 => 1.0, spike penalty => 0.9
    assert abs(resp.fields.finish_od_in.confidence - 0.9) < 1e-9


def test_id_auto_clamped_sets_reason_and_debug_flag():
    svc = RFQAutofillService()
    resp = svc.autofill(
        part_no="050DZ0017",
        part_summary_dict={
            "units": {"length": "in"},
            "z_range": [0.0, 1.0],
            "segments": [
                {"z_start": 0.0, "z_end": 1.0, "od_diameter": 1.0, "id_diameter": 0.99, "confidence": 0.9},
            ],
            "scale_report": {"method": "anchor_dimension", "validation_passed": True},
            "inference_metadata": {"overall_confidence": 0.9},
        },
        tolerances={"rm_od_allowance_in": 0.1, "rm_len_allowance_in": 0.35},
        step_metrics=None,
    )
    assert "ID_AUTO_CLAMPED" in resp.reasons
    assert resp.debug.id_auto_clamped is True
    assert resp.fields.finish_id_in.value == 0.98


