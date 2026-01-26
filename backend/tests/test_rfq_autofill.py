"""Unit tests for RFQ AutoFill v1 algorithm (backend only)."""

import json
import math
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
        mode="GEOMETRY",
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
        mode="GEOMETRY",
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
        mode="GEOMETRY",
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
        mode="GEOMETRY",
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
        mode="GEOMETRY",
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
        mode="GEOMETRY",
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
        mode="GEOMETRY",
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
        mode="GEOMETRY",
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
        mode="GEOMETRY",
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
        mode="GEOMETRY",
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
        mode="GEOMETRY",
    )
    assert "ID_AUTO_CLAMPED" in resp.reasons
    assert resp.debug.id_auto_clamped is True
    assert resp.fields.finish_id_in.value == 0.98


def test_envelope_mode_with_cost_inputs_returns_estimate_and_math():
    svc = RFQAutofillService()
    resp = svc.autofill(
        part_no="050DZ0017",
        part_summary_dict={
            "units": {"length": "in"},
            "z_range": [0.0, 1.0],
            "segments": [{"z_start": 0.0, "z_end": 1.0, "od_diameter": 1.0, "id_diameter": 0.0, "confidence": 0.9}],
            "scale_report": {"method": "anchor_dimension", "validation_passed": True},
            "inference_metadata": {"overall_confidence": 0.9},
        },
        tolerances={"rm_od_allowance_in": 0.0, "rm_len_allowance_in": 0.0},
        step_metrics=None,
        mode="ENVELOPE",
        cost_inputs={
            "rm_rate_per_kg": 10.0,
            "turning_rate_per_min": 2.0,
            "roughing_cost": 1.0,
            "inspection_cost": 2.0,
            "special_process_cost": 3.0,
            "material_density_kg_m3": 1000.0,
        },
    )
    assert resp.estimate is not None
    assert "ENVELOPE_MODE" in resp.reasons
    assert "PROXY_TIME_MODEL" in resp.reasons
    assert "WEIGHT_SOLID_ASSUMPTION" in resp.reasons

    rm_od = resp.fields.rm_od_in.value
    rm_len = resp.fields.rm_len_in.value
    assert rm_od == 1.0
    assert rm_len == 1.0

    expected_vol_in3 = math.pi * (0.5**2) * 1.0
    expected_weight = expected_vol_in3 * (0.0254**3) * 1000.0
    assert resp.estimate.rm_weight_kg.value == pytest.approx(round(expected_weight, 3), abs=1e-9)

    expected_material = expected_weight * 10.0
    assert resp.estimate.material_cost.value == pytest.approx(round(expected_material, 3), abs=1e-9)

    assert resp.estimate.turning_minutes.value == 15.0
    assert resp.estimate.turning_cost.value == 30.0

    expected_subtotal = expected_material + 30.0 + 1.0 + 2.0 + 3.0
    assert resp.estimate.subtotal.value == pytest.approx(round(expected_subtotal, 3), abs=1e-9)


def test_envelope_mode_without_cost_inputs_returns_no_estimate():
    svc = RFQAutofillService()
    resp = svc.autofill(
        part_no="050DZ0017",
        part_summary_dict={
            "units": {"length": "in"},
            "z_range": [0.0, 1.0],
            "segments": [{"z_start": 0.0, "z_end": 1.0, "od_diameter": 1.0, "id_diameter": 0.0, "confidence": 0.9}],
            "scale_report": {"method": "anchor_dimension", "validation_passed": True},
            "inference_metadata": {"overall_confidence": 0.9},
        },
        tolerances={"rm_od_allowance_in": 0.1, "rm_len_allowance_in": 0.35},
        step_metrics=None,
        mode="ENVELOPE",
        cost_inputs=None,
    )
    assert "ENVELOPE_MODE" in resp.reasons
    assert resp.estimate is None


def test_envelope_estimate_confidence_reduced_when_scale_estimated():
    svc = RFQAutofillService()
    resp = svc.autofill(
        part_no="050DZ0017",
        part_summary_dict={
            "units": {"length": "in"},
            "z_range": [0.0, 1.0],
            "segments": [{"z_start": 0.0, "z_end": 1.0, "od_diameter": 1.0, "id_diameter": 0.0, "confidence": 0.9}],
            "scale_report": {"method": "estimated", "validation_passed": True},
            "inference_metadata": {"overall_confidence": 0.9},
        },
        tolerances={"rm_od_allowance_in": 0.0, "rm_len_allowance_in": 0.0},
        step_metrics=None,
        mode="ENVELOPE",
        cost_inputs={
            "rm_rate_per_kg": 10.0,
            "turning_rate_per_min": 2.0,
            "material_density_kg_m3": 1000.0,
        },
    )
    assert resp.estimate is not None
    assert "SCALE_ESTIMATED" in resp.reasons
    # base=min(od_conf=0.75, len_conf=0.7)=0.7 => weight_conf=0.6, then -0.15 => 0.45
    assert resp.estimate.rm_weight_kg.confidence == pytest.approx(0.45, abs=1e-9)


def test_envelope_mode_does_not_reject_on_od_spike_suspect_only():
    svc = RFQAutofillService()
    # Create an OD spike: a tiny segment has huge OD; envelope mode uses max OD.
    resp = svc.autofill(
        part_no="050DZ0017",
        part_summary_dict={
            "units": {"length": "in"},
            "z_range": [0.0, 10.0],
            "segments": [
                {"z_start": 0.0, "z_end": 9.8, "od_diameter": 2.0, "id_diameter": 0.0, "confidence": 0.9},
                {"z_start": 9.8, "z_end": 9.81, "od_diameter": 5.0, "id_diameter": 0.0, "confidence": 0.8},
            ],
            "scale_report": {"method": "estimated", "validation_passed": True},
            "inference_metadata": {"overall_confidence": 0.9},
        },
        tolerances={"rm_od_allowance_in": 0.1, "rm_len_allowance_in": 0.35},
        step_metrics=None,
        mode="ENVELOPE",
        cost_inputs=None,
    )
    assert "OD_SPIKE_SUSPECT" in resp.reasons
    assert resp.debug.od_spike_suspect is True
    assert resp.status == "NEEDS_REVIEW"


def test_feature_time_model_adds_estimate_fields_and_reason():
    svc = RFQAutofillService()
    resp = svc.autofill(
        part_no="050DZ0017",
        part_summary_dict={
            "units": {"length": "in"},
            "z_range": [0.0, 2.0],
            "segments": [{"z_start": 0.0, "z_end": 2.0, "od_diameter": 2.0, "id_diameter": 0.5, "confidence": 0.9}],
            "scale_report": {"method": "anchor_dimension", "validation_passed": True},
            "inference_metadata": {"overall_confidence": 0.9},
            "features": {
                "holes": [
                    {"diameter": 0.25, "depth": 0.5, "kind": "axial", "count": 6},
                    {"diameter": 0.125, "depth": None, "kind": "cross", "count": 2},
                ],
                "slots": [
                    {"width": 0.25, "length": 0.75, "depth": 0.1, "orientation": "axial", "count": 2},
                ],
                "chamfers": [],
                "fillets": [],
                "threads": [],
                "meta": {"model_version": "v1", "detector_version": "text_v1", "timestamp_utc": "2026-01-24T00:00:00Z", "warnings": []},
            },
        },
        tolerances={"rm_od_allowance_in": 0.1, "rm_len_allowance_in": 0.35},
        step_metrics=None,
        mode="ENVELOPE",
        cost_inputs={
            "rm_rate_per_kg": 100.0,
            "turning_rate_per_min": 4.0,
            "roughing_cost": 162.0,
            "inspection_cost": 10.0,
            "material_density_kg_m3": 7850.0,
            "special_process_cost": None,
        },
        vendor_quote_mode=False,
    )

    assert resp.estimate is not None
    assert resp.estimate.drilling_minutes is not None
    assert resp.estimate.milling_minutes is not None
    assert resp.estimate.drilling_cost is not None
    assert resp.estimate.milling_cost is not None
    assert "FEATURE_TIME_MODEL" in resp.reasons


def test_feature_time_confidence_capped_when_scale_estimated():
    svc = RFQAutofillService()
    resp = svc.autofill(
        part_no="050DZ0017",
        part_summary_dict={
            "units": {"length": "in"},
            "z_range": [0.0, 2.0],
            "segments": [{"z_start": 0.0, "z_end": 2.0, "od_diameter": 2.0, "id_diameter": 0.5, "confidence": 0.9}],
            "scale_report": {"method": "estimated", "validation_passed": True},
            "inference_metadata": {"overall_confidence": 0.9},
            "features": {
                "holes": [{"diameter": 0.25, "depth": 0.5, "kind": "axial", "count": 6}],
                "slots": [{"width": 0.25, "length": 0.75, "depth": 0.1, "orientation": "axial", "count": 2}],
                "chamfers": [],
                "fillets": [],
                "threads": [],
                "meta": {"model_version": "v1", "detector_version": "text_v1", "timestamp_utc": "2026-01-24T00:00:00Z", "warnings": []},
            },
        },
        tolerances={"rm_od_allowance_in": 0.1, "rm_len_allowance_in": 0.35},
        step_metrics=None,
        mode="ENVELOPE",
        cost_inputs={
            "rm_rate_per_kg": 100.0,
            "turning_rate_per_min": 4.0,
            "roughing_cost": 162.0,
            "inspection_cost": 10.0,
            "material_density_kg_m3": 7850.0,
            "special_process_cost": None,
        },
        vendor_quote_mode=False,
    )

    assert resp.estimate is not None
    assert resp.estimate.drilling_minutes is not None
    assert resp.estimate.milling_minutes is not None
    assert resp.estimate.drilling_minutes.confidence <= 0.4
    assert resp.estimate.milling_minutes.confidence <= 0.4
    assert "SCALE_ESTIMATED" in resp.reasons


def test_vendor_quote_mode_preserves_solid_weight_and_feature_time_reason():
    svc = RFQAutofillService()
    resp = svc.autofill(
        part_no="050DZ0017",
        part_summary_dict={
            "units": {"length": "in"},
            "z_range": [0.0, 1.0],
            "segments": [{"z_start": 0.0, "z_end": 1.0, "od_diameter": 2.0, "id_diameter": 1.0, "confidence": 0.9}],
            "scale_report": {"method": "anchor_dimension", "validation_passed": True},
            "inference_metadata": {"overall_confidence": 0.9},
            "features": {
                "holes": [{"diameter": 0.25, "depth": 0.5, "kind": "axial", "count": 4}],
                "slots": [{"width": 0.25, "length": 0.75, "depth": 0.1, "orientation": "axial", "count": 1}],
                "chamfers": [],
                "fillets": [],
                "threads": [],
                "meta": {"model_version": "v1", "detector_version": "text_v1", "timestamp_utc": "2026-01-24T00:00:00Z", "warnings": []},
            },
        },
        tolerances={"rm_od_allowance_in": 0.0, "rm_len_allowance_in": 0.0},
        step_metrics=None,
        mode="ENVELOPE",
        cost_inputs={
            "rm_rate_per_kg": 10.0,
            "turning_rate_per_min": 2.0,
            "roughing_cost": 1.0,
            "inspection_cost": 2.0,
            "material_density_kg_m3": 1000.0,
            "special_process_cost": None,
        },
        vendor_quote_mode=True,
    )

    assert resp.estimate is not None
    assert "VENDOR_QUOTE_SOLID_CYLINDER" in resp.reasons
    assert "FEATURE_TIME_MODEL" in resp.reasons

    rm_od = resp.fields.rm_od_in.value
    rm_len = resp.fields.rm_len_in.value
    expected_vol_in3 = math.pi * ((rm_od / 2.0) ** 2) * rm_len
    expected_weight = expected_vol_in3 * (0.0254**3) * 1000.0
    assert resp.estimate.rm_weight_kg.value == pytest.approx(round(expected_weight, 3), abs=1e-9)


def test_envelope_subtotal_includes_feature_time_costs():
    svc = RFQAutofillService()
    resp = svc.autofill(
        part_no="050DZ0017",
        part_summary_dict={
            "units": {"length": "in"},
            "z_range": [0.0, 1.0],
            "segments": [{"z_start": 0.0, "z_end": 1.0, "od_diameter": 2.0, "id_diameter": 0.0, "confidence": 0.9}],
            "scale_report": {"method": "anchor_dimension", "validation_passed": True},
            "inference_metadata": {"overall_confidence": 0.9},
            "features": {
                "holes": [{"diameter": 0.25, "depth": 0.5, "kind": "axial", "count": 2}],
                "slots": [{"width": 0.25, "length": 0.75, "depth": 0.1, "orientation": "axial", "count": 2}],
                "chamfers": [],
                "fillets": [],
                "threads": [],
                "meta": {"model_version": "v1", "detector_version": "text_v1", "timestamp_utc": "2026-01-24T00:00:00Z", "warnings": []},
            },
        },
        tolerances={"rm_od_allowance_in": 0.0, "rm_len_allowance_in": 0.0},
        step_metrics=None,
        mode="ENVELOPE",
        cost_inputs={
            "rm_rate_per_kg": 10.0,
            "turning_rate_per_min": 2.0,
            "roughing_cost": 1.0,
            "inspection_cost": 2.0,
            "material_density_kg_m3": 1000.0,
            "special_process_cost": None,
        },
        vendor_quote_mode=False,
    )

    assert resp.estimate is not None
    material_cost = resp.estimate.material_cost.value or 0.0
    turning_cost = resp.estimate.turning_cost.value or 0.0
    drilling_cost = resp.estimate.drilling_cost.value or 0.0
    milling_cost = resp.estimate.milling_cost.value or 0.0
    roughing_cost = resp.estimate.roughing_cost.value or 0.0
    inspection_cost = resp.estimate.inspection_cost.value or 0.0
    special_cost = resp.estimate.special_process_cost.value or 0.0

    expected_subtotal = material_cost + turning_cost + drilling_cost + milling_cost + roughing_cost + inspection_cost + special_cost
    assert resp.estimate.subtotal.value == pytest.approx(round(expected_subtotal, 3), abs=1e-9)

