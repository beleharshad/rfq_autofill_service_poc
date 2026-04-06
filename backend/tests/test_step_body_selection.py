import asyncio
import json
from pathlib import Path

import pytest

from app.api import preview3d
from app.services import step_analysis_service as step_module
from app.services.step_analysis_service import StepAnalysisService


def _body_summary(*, length: float, od: float, volume: float, ext_cyl: int, bores: int, segments: int):
    segment_rows = [
        {
            "z_start": float(index),
            "z_end": float(index + 1),
            "od_diameter": od,
            "id_diameter": 0.25 if bores > 0 else 0.0,
            "wall_thickness": max((od / 2.0) - 0.125, 0.05),
            "volume_in3": volume / max(segments, 1),
            "od_area_in2": 1.0,
            "id_area_in2": 0.2 if bores > 0 else 0.0,
        }
        for index in range(segments)
    ]
    return {
        "schema_version": "0.1",
        "generated_at_utc": "2026-04-06T00:00:00Z",
        "units": {"length": "in", "area": "in^2", "volume": "in^3"},
        "z_range": [0.0, length],
        "segments": segment_rows,
        "totals": {
            "volume_in3": volume,
            "od_area_in2": 2.0,
            "id_area_in2": 0.4 if bores > 0 else 0.0,
            "end_face_area_start_in2": 1.0,
            "end_face_area_end_in2": 1.0,
            "od_shoulder_area_in2": 0.5,
            "id_shoulder_area_in2": 0.1 if bores > 0 else 0.0,
            "planar_ring_area_in2": 0.4,
            "total_surface_area_in2": 6.0,
            "total_length_in": length,
            "max_od_in": od,
            "max_id_in": 0.25 if bores > 0 else 0.0,
        },
        "feature_counts": {
            "external_cylinders": ext_cyl,
            "internal_bores": bores,
            "planar_faces": 2,
            "total_faces": 8,
        },
        "inference_metadata": {
            "mode": "auto_detect",
            "overall_confidence": 1.0,
            "source": "uploaded_step_body",
            "input_unit": "in",
            "file_name": "demo.step",
        },
    }


def test_generate_part_summary_selects_best_body(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    service = StepAnalysisService()
    step_file = tmp_path / "demo.step"
    step_file.write_text("ISO-10303-21; END-ISO-10303-21;", encoding="utf-8")

    solid_a = object()
    solid_b = object()

    monkeypatch.setattr(step_module, "_STEP_READER_AVAILABLE", True)
    monkeypatch.setattr(step_module, "OCC_AVAILABLE", True)
    monkeypatch.setattr(service, "_extract_step_metadata", lambda _path: {
        "file_name": "demo.step",
        "file_stem": "demo",
        "product_name": None,
        "product_id": None,
        "schema": "AP214",
        "representation_name": None,
        "representation_context": "TOP_LEVEL_ASSEMBLY_PART",
        "body_count": 2,
    })
    monkeypatch.setattr(service, "_detect_length_unit", lambda _path: ("in", 1.0, "Detected STEP length unit: inch"))
    monkeypatch.setattr(service, "_read_step_shape", lambda _path: object())
    monkeypatch.setattr(service, "_extract_solids", lambda _shape: [solid_a, solid_b])

    summaries = {
        solid_a: _body_summary(length=0.8, od=0.6, volume=0.12, ext_cyl=1, bores=0, segments=1),
        solid_b: _body_summary(length=5.0, od=2.0, volume=8.4, ext_cyl=4, bores=2, segments=5),
    }
    monkeypatch.setattr(
        service,
        "_build_feature_summary",
        lambda solid, *_args, **_kwargs: summaries[solid],
    )
    monkeypatch.setattr(
        service,
        "_build_bbox_summary",
        lambda *_args, **_kwargs: pytest.fail("bbox fallback should not be used in this test"),
    )

    summary = service._generate_part_summary(step_file)

    assert summary["selected_body"]["body_index"] == 1
    assert summary["selected_body"]["dimensions"]["od_in"] == 2.0
    assert len(summary["body_candidates"]) == 2
    assert summary["inference_metadata"]["source"] == "uploaded_step_selected_body"
    assert summary["warnings"]


def test_preview3d_uses_uploaded_step_without_inferred_stack(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    out = tmp_path / "job-output"
    out.mkdir(parents=True, exist_ok=True)
    (out / "model.step").write_text("dummy step content", encoding="utf-8")
    (out / "part_summary.json").write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "generated_at_utc": "2026-04-06T00:00:00Z",
                "units": {"length": "in", "area": "in^2", "volume": "in^3"},
                "z_range": [0.0, 5.0],
                "segments": [{
                    "z_start": 0.0,
                    "z_end": 5.0,
                    "od_diameter": 2.0,
                    "id_diameter": 0.5,
                    "wall_thickness": 0.75,
                    "volume_in3": 8.0,
                    "od_area_in2": 4.0,
                    "id_area_in2": 1.0,
                }],
                "totals": {"volume_in3": 8.0, "max_od_in": 2.0, "max_id_in": 0.5, "total_length_in": 5.0},
                "feature_counts": {"external_cylinders": 4, "internal_bores": 1, "planar_faces": 2, "total_faces": 8},
                "inference_metadata": {"source": "uploaded_step_selected_body"},
            }
        ),
        encoding="utf-8",
    )

    glb_path = out / "model.glb"

    monkeypatch.setattr(preview3d, "_outputs", lambda _job_id: out)
    monkeypatch.setattr(preview3d._glb_converter, "available", True)

    def _fake_convert(step_path: Path, glb_file: Path, check_cache: bool = True):
        assert step_path == out / "model.step"
        glb_file.write_bytes(b"glb")
        return True, None

    monkeypatch.setattr(preview3d._glb_converter, "convert_step_to_glb", _fake_convert)

    status = asyncio.run(preview3d.get_3d_preview_status("job-123"))
    assert status["stack_ready"] is False
    assert status["step_ready"] is True
    assert status["can_generate"] is True
    assert status["source_mode"] == "step_upload"

    response = asyncio.run(preview3d.get_3d_preview("job-123"))
    assert response.path.endswith("model.glb")
    assert glb_path.exists()
