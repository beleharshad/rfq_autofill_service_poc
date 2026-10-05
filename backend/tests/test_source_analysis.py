import math
import pytest
from fastapi import HTTPException
from app.storage.file_storage import FileStorage
from app.storage.document_registry import DocumentRegistry
from app.models.document_registry import AssociationRequest
from app.services.source_analysis import analyze_source

JOB="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def test_exact_source_and_finished_fields_only(tmp_path):
    files=FileStorage(tmp_path)
    files.save_bytes_file(JOB,"first.pdf",b"unrelated")
    files.save_bytes_file(JOB,"selected.pdf",b"selected original")
    registry=DocumentRegistry(files.get_job_path(JOB))
    doc=next(d for d in registry.read()["documents"] if d["original_name"]=="selected.pdf")
    registry.associate(doc["document_id"],AssociationRequest(expected_version=registry.read()["version"],part_number="PART",revision="A",role="finished_drawing",manufacturing_state="finished"))
    def extractor(path):
        assert path.read_bytes()==b"selected original"
        return {"valid":True,"extracted":{"od_in":2,"max_od_in":20,"length_in":1,"max_length_in":10},"validation":{"recommendation":"ACCEPT"}}
    result=analyze_source(registry,doc["document_id"],extractor)
    assert [d["value_mm"] for d in result["candidates"]]==[50.8,25.4]
    assert all(d["acceptance"]=="unverified" for d in result["candidates"])
    assert (registry.root/"analysis"/f"{result['analysis_id']}.json").is_file()


def test_process_source_cannot_feed_finished_candidates(tmp_path):
    files=FileStorage(tmp_path);files.save_bytes_file(JOB,"process.pdf",b"raw stock")
    registry=DocumentRegistry(files.get_job_path(JOB));doc=registry.read()["documents"][0]
    registry.associate(doc["document_id"],AssociationRequest(expected_version=registry.read()["version"],part_number="PART",revision="A",role="process_drawing",manufacturing_state="raw"))
    with pytest.raises(HTTPException) as error:
        analyze_source(registry,doc["document_id"],lambda _:pytest.fail("Must not call provider"))
    assert error.value.status_code==422
