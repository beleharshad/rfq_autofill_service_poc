import io

from app.storage.file_storage import FileStorage
from app.storage.job_storage import JobStorage


class _UploadStub:
    def __init__(self, filename: str, content: bytes):
        self.filename = filename
        self.file = io.BytesIO(content)


def test_file_storage_uses_stable_root_across_cwd_changes(tmp_path, monkeypatch):
    data_root = tmp_path / "backend-data"
    cwd_a = tmp_path / "cwd-a"
    cwd_b = tmp_path / "cwd-b"
    cwd_a.mkdir()
    cwd_b.mkdir()

    monkeypatch.setenv("RFQ_DATA_DIR", str(data_root))
    monkeypatch.chdir(cwd_a)

    storage = FileStorage()
    upload = _UploadStub("sample.step", b"ISO-10303-21;")
    saved_path = storage.save_uploaded_file("job-123", upload)

    assert saved_path == "inputs/sample.step"

    monkeypatch.chdir(cwd_b)
    storage_from_other_cwd = FileStorage()

    assert storage_from_other_cwd.list_input_files("job-123") == ["inputs/sample.step"]
    file_path, filename, size = storage_from_other_cwd.get_file_info("job-123", "inputs/sample.step")

    assert filename == "sample.step"
    assert size > 0
    assert file_path == data_root / "jobs" / "job-123" / "inputs" / "sample.step"


def test_job_storage_uses_stable_db_path_across_cwd_changes(tmp_path, monkeypatch):
    data_root = tmp_path / "backend-data"
    cwd_a = tmp_path / "cwd-a"
    cwd_b = tmp_path / "cwd-b"
    cwd_a.mkdir()
    cwd_b.mkdir()

    monkeypatch.setenv("RFQ_DATA_DIR", str(data_root))
    monkeypatch.chdir(cwd_a)

    storage = JobStorage()
    storage.create_job("job-456", name="Stable Path Test", description="desc", mode="auto_convert")

    monkeypatch.chdir(cwd_b)
    storage_from_other_cwd = JobStorage()
    job = storage_from_other_cwd.get_job("job-456")

    assert job is not None
    assert job.job_id == "job-456"
    assert job.name == "Stable Path Test"
    assert any(item.job_id == "job-456" for item in storage_from_other_cwd.list_jobs())
