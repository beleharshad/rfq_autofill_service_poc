import json

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api import part_spec as routes
from app.models.part_spec import DimensionCandidate, SourceDocument
from app.security import require_api_key
from app.services.part_spec_service import PartSpecService, adapt_legacy_summary
from app.storage.file_storage import FileStorage

JOB = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
SOURCE = SourceDocument(document_id="summary", path="outputs/part_summary.json",
                        sha256="test", role="legacy_summary")


def summary(unit="in"):
    return {"units": {"length": unit}, "segments": [{"od_diameter": 2}, {"od_diameter": 3}],
            "totals": {"total_length_in": 1}, "inference_metadata": {"overall_confidence": 1}}


def test_units_and_provenance_never_promote_legacy_confidence():
    spec = adapt_legacy_summary(JOB, summary(), [SOURCE])
    assert [d.value for d in spec.dimensions] == pytest.approx([76.2, 25.4])
    assert spec.dimensions[0].evidence[0].json_pointer == "/segments/1/od_diameter"
    assert all(d.acceptance == "unverified" for d in spec.dimensions)
    assert spec.readiness.geometry == spec.readiness.quote == "not_validated"
    assert spec.part_id is None and spec.revision is None


def test_mm_segments_do_not_reinterpret_explicit_inch_total():
    spec = adapt_legacy_summary(JOB, summary("mm"), [SOURCE])
    assert [d.value for d in spec.dimensions] == [3, 25.4]


@pytest.mark.parametrize("bad", [None, True, -1, 0, "3", float("nan"), float("inf"), 10**400])
def test_invalid_segment_blocks_partial_maximum(bad):
    data = summary()
    data["segments"][0]["od_diameter"] = bad
    spec = adapt_legacy_summary(JOB, data, [SOURCE])
    assert [d.name for d in spec.dimensions] == ["axial_length"]
    assert "invalid_segment_od" in [i.code for i in spec.issues]


def test_missing_units_not_invented_and_no_id_inferred():
    spec = adapt_legacy_summary(JOB, summary("pixels"), [SOURCE])
    assert [d.name for d in spec.dimensions] == ["axial_length"]
    assert "unknown_segment_units" in [i.code for i in spec.issues]


def test_empty_job_has_no_fabricated_dimensions():
    spec = adapt_legacy_summary(JOB, None, [])
    assert spec.dimensions == []
    assert spec.readiness.measurements == "unresolved"


def test_contract_rejects_nonfinite_or_accepted_candidates():
    data = adapt_legacy_summary(JOB, summary(), [SOURCE]).dimensions[0].model_dump()
    with pytest.raises(ValidationError):
        DimensionCandidate(**{**data, "value": float("inf")})
    with pytest.raises(ValidationError):
        DimensionCandidate(**{**data, "acceptance": "accepted"})


@pytest.fixture
def service(tmp_path):
    storage = FileStorage(tmp_path)
    storage.read_base_paths = [tmp_path]
    storage.ensure_job_directories(JOB)
    return PartSpecService(storage)


def test_snapshot_identity_changes_with_sources_and_ignores_lock_files(service):
    storage = service.files
    source = storage.get_inputs_path(JOB) / "drawing.pdf"
    source.write_bytes(b"drawing version one")
    (storage.get_inputs_path(JOB) / "~$quote.xlsx").write_bytes(b"lock")
    path = storage.get_outputs_path(JOB) / "part_summary.json"
    path.write_text(json.dumps(summary()))
    first = service.get_snapshot(JOB)
    assert first.snapshot_id == service.get_snapshot(JOB).snapshot_id
    assert len(first.sources) == 2
    source.write_bytes(b"drawing version two")
    assert first.snapshot_id != service.get_snapshot(JOB).snapshot_id
    path.write_text(json.dumps(summary("mm")))
    assert first.snapshot_id != service.get_snapshot(JOB).snapshot_id


@pytest.mark.parametrize("content", [b"{", b"[]", b"\xff", b"x" * (8 * 1024 * 1024 + 1)])
def test_bad_summary_returns_review_issue_not_500(service, content):
    (service.files.get_outputs_path(JOB) / "part_summary.json").write_bytes(content)
    spec = service.get_snapshot(JOB)
    assert not spec.dimensions
    assert "invalid_summary" in [i.code for i in spec.issues]


def test_multiple_inputs_are_not_silently_one_part(service):
    for name in ["finished.pdf", "process.pdf"]:
        (service.files.get_inputs_path(JOB) / name).write_bytes(b"pdf")
    assert "source_association_required" in [i.code for i in service.get_snapshot(JOB).issues]


def test_api_auth_job_validation_and_response(monkeypatch, service):
    monkeypatch.setenv("INTERNAL_API_KEY", "test-key")
    monkeypatch.setattr(routes, "PartSpecService", lambda: service)
    class Jobs:
        def get_job(self, job_id):
            return object() if job_id == JOB else None
    monkeypatch.setattr(routes, "JobStorage", Jobs)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1/jobs", dependencies=[Depends(require_api_key)])
    client = TestClient(app)
    assert client.get(f"/api/v1/jobs/{JOB}/part-spec").status_code == 401
    headers = {"X-API-Key": "test-key"}
    assert client.get("/api/v1/jobs/not-a-uuid/part-spec", headers=headers).status_code == 400
    assert client.get(f"/api/v1/jobs/{'f'*8}-bbbb-cccc-dddd-eeeeeeeeeeee/part-spec", headers=headers).status_code == 404
    response = client.get(f"/api/v1/jobs/{JOB}/part-spec", headers=headers)
    assert response.status_code == 200
    assert response.json()["schema_version"] == "0.1"
    assert response.json()["readiness"]["geometry"] == "not_validated"
