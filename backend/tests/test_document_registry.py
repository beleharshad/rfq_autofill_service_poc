import io
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import Depends, FastAPI, HTTPException, UploadFile
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api import part_spec as routes
from app.models.document_registry import AssociationRequest
from app.security import require_api_key
from app.services.part_spec_service import PartSpecService
from app.storage.document_registry import DocumentRegistry
from app.storage.file_storage import FileStorage

JOB = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


@pytest.fixture
def files(tmp_path):
    result = FileStorage(tmp_path)
    result.read_base_paths = [tmp_path]
    result.ensure_job_directories(JOB)
    return result


def request(version, revision="A", role="finished_drawing", state="finished"):
    return AssociationRequest(expected_version=version, part_number="PART-1", revision=revision,
                              role=role, manufacturing_state=state)


def test_direct_upload_retains_both_same_name_sources(files):
    a = files.save_uploaded_file(JOB, UploadFile(filename="part.pdf", file=io.BytesIO(b"first")))
    b = files.save_uploaded_file(JOB, UploadFile(filename="part.pdf", file=io.BytesIO(b"second")))
    assert a != b
    registry = DocumentRegistry(files.get_job_path(JOB))
    docs = registry.read()["documents"]
    assert len(docs) == 2
    assert { (registry.root / "blobs" / d["sha256"]).read_bytes() for d in docs } == {b"first", b"second"}
    # Changing the legacy input path cannot change retained original bytes.
    (files.get_job_path(JOB) / a).write_bytes(b"changed")
    assert { (registry.root / "blobs" / d["sha256"]).read_bytes() for d in docs } == {b"first", b"second"}


def test_concurrent_same_filename_uploads_do_not_overwrite(files):
    def upload(i):
        return files.save_bytes_file(JOB, "part.pdf", str(i).encode())
    with ThreadPoolExecutor(max_workers=4) as pool:
        paths = list(pool.map(upload, range(8)))
    assert len(set(paths)) == 8
    assert len(DocumentRegistry(files.get_job_path(JOB)).read()["documents"]) == 8


def test_registration_idempotent_and_changed_bytes_get_new_identity(files):
    path = files.save_bytes_file(JOB, "a.pdf", b"one")
    registry = DocumentRegistry(files.get_job_path(JOB))
    first = registry.read()
    registry.register(files.get_job_path(JOB) / path, path)
    assert registry.read() == first
    (files.get_job_path(JOB) / path).write_bytes(b"two")
    registry.register(files.get_job_path(JOB) / path, path)
    assert len(registry.read()["documents"]) == 2


def test_zip_registers_member_origins_and_handles_colliding_basenames(files, tmp_path):
    archive = tmp_path / "upload.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("a/part.pdf", b"a")
        z.writestr("b/part.pdf", b"b")
        z.writestr("~$ignored.pdf", b"lock")
    paths = files.extract_zip(JOB, archive)
    assert len(paths) == 2 and paths[0] != paths[1]
    assert {d["original_name"] for d in DocumentRegistry(files.get_job_path(JOB)).read()["documents"]} == {"a/part.pdf", "b/part.pdf"}


def test_association_history_and_stale_write_rejection(files):
    files.save_bytes_file(JOB, "part.pdf", b"one")
    registry = DocumentRegistry(files.get_job_path(JOB))
    data = registry.read()
    doc = data["documents"][0]["document_id"]
    registry.associate(doc, request(data["version"]))
    with pytest.raises(HTTPException) as error:
        registry.associate(doc, request(data["version"], "B"))
    assert error.value.status_code == 409
    registry.associate(doc, request(registry.read()["version"], "B"))
    assert [h["association"]["revision"] for h in registry.history(doc)] == ["A", "B"]
    assert registry.read()["documents"][0]["association"]["revision"] == "B"


def test_parallel_edit_only_one_can_use_expected_version(files):
    files.save_bytes_file(JOB, "part.pdf", b"one")
    registry = DocumentRegistry(files.get_job_path(JOB))
    data = registry.read()
    doc = data["documents"][0]["document_id"]
    def update(rev):
        try:
            registry.associate(doc, request(data["version"], rev))
            return 200
        except HTTPException as error:
            return error.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(update, ["A", "B"])) == [200, 409]


def test_association_changes_snapshot_without_accepting_geometry(files):
    for name in ["finished.pdf", "process.pdf"]:
        files.save_bytes_file(JOB, name, b"drawing")
    registry = DocumentRegistry(files.get_job_path(JOB))
    first = PartSpecService(files).get_snapshot(JOB)
    for index, doc in enumerate(registry.read()["documents"]):
        registry.associate(doc["document_id"], request(registry.read()["version"], "A" if index == 0 else "B"))
    spec = PartSpecService(files).get_snapshot(JOB)
    assert first.snapshot_id != spec.snapshot_id
    assert "revision_conflict" in [i.code for i in spec.issues]
    assert all(s.association.part_number == "PART-1" for s in spec.sources)
    assert spec.part_id is None
    assert spec.readiness.geometry == "not_validated"


def test_changed_legacy_path_does_not_inherit_old_association(files):
    path = files.save_bytes_file(JOB, "part.pdf", b"first")
    registry = DocumentRegistry(files.get_job_path(JOB))
    data = registry.read()
    registry.associate(data["documents"][0]["document_id"], request(data["version"]))
    (files.get_job_path(JOB) / path).write_bytes(b"different")
    spec = PartSpecService(files).get_snapshot(JOB)
    assert spec.sources[0].association is None
    assert "source_content_changed" in [i.code for i in spec.issues]


def test_association_validates_state_and_blank_part_number():
    with pytest.raises(ValidationError):
        request(0, state="raw")
    with pytest.raises(ValidationError):
        AssociationRequest(expected_version=0, part_number="  ", role="cad", manufacturing_state="finished")
    assert request(0, role="process_drawing", state="unresolved").manufacturing_state == "unresolved"


def test_registry_api_and_retained_download(files, monkeypatch):
    monkeypatch.setenv("INTERNAL_API_KEY", "registry-test")
    monkeypatch.setattr(routes, "FileStorage", lambda: files)
    class Jobs:
        def get_job(self, job):
            return object() if job == JOB else None
    monkeypatch.setattr(routes, "JobStorage", Jobs)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1/jobs", dependencies=[Depends(require_api_key)])
    client = TestClient(app)
    base = f"/api/v1/jobs/{JOB}/documents"
    assert client.get(base).status_code == 401
    headers = {"X-API-Key": "registry-test"}
    path = files.get_inputs_path(JOB) / "legacy.pdf"
    path.write_bytes(b"original")
    data = client.post(base + "/register", headers=headers).json()
    assert data == client.post(base + "/register", headers=headers).json()
    doc = data["documents"][0]["document_id"]
    url = base + f"/{doc}/association"
    assert client.put(url, headers=headers, json=request(data["version"]).model_dump()).status_code == 200
    assert client.put(url, headers=headers, json=request(data["version"]).model_dump()).status_code == 409
    path.write_bytes(b"modified")
    assert client.get(base + f"/{doc}/original", headers=headers).content == b"original"
    assert len(client.get(base + f"/{doc}/history", headers=headers).json()) == 1
    assert client.get(base + "/unknown/original", headers=headers).status_code == 404
    assert client.get(base + "/unknown/history", headers=headers).status_code == 404
