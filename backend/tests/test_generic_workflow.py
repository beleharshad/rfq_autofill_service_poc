"""Exercise authenticated endpoints through real CAD subprocess and artifact export."""
import json
import sys

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api import part_spec as routes
from app.security import require_api_key
from app.storage.file_storage import FileStorage
from app.storage.accepted_parts import AcceptedParts
from app.workers.generic_queue import drain

JOB = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


@pytest.fixture
def workflow(tmp_path, monkeypatch):
    pytest.importorskip("cadquery")
    files = FileStorage(tmp_path)
    files.read_base_paths = [tmp_path]
    files.save_bytes_file(JOB,"part.pdf",b"engineering reference")
    monkeypatch.setenv("INTERNAL_API_KEY","workflow-key")
    monkeypatch.setenv("GENERIC_GEOMETRY_PYTHON",sys.executable)
    monkeypatch.setenv("GENERIC_GEOMETRY_QUEUE_ONLY","false")
    monkeypatch.setattr(routes,"FileStorage",lambda:files)
    class Jobs:
        def get_job(self,job): return object() if job==JOB else None
    monkeypatch.setattr(routes,"JobStorage",Jobs)
    app=FastAPI()
    app.include_router(routes.router,prefix="/api/v1/jobs",dependencies=[Depends(require_api_key)])
    client=TestClient(app,headers={"X-API-Key":"workflow-key"})
    base=f"/api/v1/jobs/{JOB}"
    docs=client.get(base+"/documents").json()
    document=docs["documents"][0]["document_id"]
    association={"expected_version":docs["version"],"part_number":"BLOCK","revision":"A","role":"finished_drawing","manufacturing_state":"finished"}
    response=client.put(base+f"/documents/{document}/association",json=association)
    assert response.status_code==200
    recipe={"expected_version":0,"expected_registry_version":response.json()["version"],"part_number":"BLOCK","revision":"A",
            "governing_document_id":document,"evidence":[{"document_id":document,"page":1,"locator":"Overall and pocket dimensions"}],
            "base":{"kind":"box","width":20,"height":20,"length":10},
            "features":[{"feature_id":"pocket","kind":"pocket","origin":[0,0,0],"width":8,"height":8,"depth":5}],
            "review_note":"Reviewed analytic reference","completeness":"complete","unresolved":[]}
    return client,base,recipe,files


def test_accept_build_measure_export_correct_invalidate(workflow):
    client,base,recipe,files=workflow
    accepted=client.post(base+"/accepted-parts",json=recipe)
    assert accepted.status_code==201,accepted.text
    spec=accepted.json()
    response=client.post(base+f"/accepted-parts/{spec['spec_id']}/build")
    assert response.status_code==202,response.text
    build_id=response.json()["build_id"]
    status=client.get(base+f"/geometry-builds/{build_id}").json()
    assert status["status"]=="complete",status
    assert status["manifest"]["measurements"]["volume_mm3"]==pytest.approx(4000-320)
    for name in ["model.step","model.glb","rfq_review.xlsx","manifest.json"]:
        artifact=client.get(base+f"/geometry-builds/{build_id}/artifacts/{name}")
        assert artifact.status_code==200 and len(artifact.content)>100
    manifest=status["manifest"]
    assert manifest["spec_id"]==spec["spec_id"]
    assert manifest["quote_status"]=="dimensions_only"
    corrected={**recipe,"expected_version":1,"base":{"kind":"box","width":30,"height":20,"length":10}}
    second=client.post(base+"/accepted-parts",json=corrected)
    assert second.status_code==201
    assert client.get(base+f"/geometry-builds/{build_id}").json()["stale"]
    assert client.get(base+f"/geometry-builds/{build_id}/artifacts/model.glb").status_code==409
    assert client.post(base+"/accepted-parts",json=recipe).status_code==409


def test_durable_queued_task_runs_after_dispatcher_restart(workflow,monkeypatch):
    client,base,recipe,files=workflow
    monkeypatch.setenv("GENERIC_GEOMETRY_QUEUE_ONLY","true")
    spec=client.post(base+"/accepted-parts",json=recipe).json()
    task=client.post(base+f"/accepted-parts/{spec['spec_id']}/build").json()
    assert client.get(base+f"/geometry-builds/{task['build_id']}").json()["status"]=="queued"
    drain(files.base_path)
    assert client.get(base+f"/geometry-builds/{task['build_id']}").json()["status"]=="complete"


def test_invalid_cut_fails_without_success_artifact(workflow):
    client,base,recipe,_=workflow
    recipe["features"][0]["origin"]=[100,100,100]
    spec=client.post(base+"/accepted-parts",json=recipe).json()
    task=client.post(base+f"/accepted-parts/{spec['spec_id']}/build").json()
    result=client.get(base+f"/geometry-builds/{task['build_id']}").json()
    assert result["status"]=="failed"
    assert "removed no material" in result["error"]
    assert client.get(base+f"/geometry-builds/{task['build_id']}/artifacts/model.step").status_code==409


def test_artifact_tampering_is_not_served(workflow):
    client,base,recipe,files=workflow
    spec=client.post(base+"/accepted-parts",json=recipe).json()
    task=client.post(base+f"/accepted-parts/{spec['spec_id']}/build").json()
    (files.get_outputs_path(JOB)/"accepted"/task["build_id"]/"model.glb").write_bytes(b"different")
    assert client.get(base+f"/geometry-builds/{task['build_id']}/artifacts/model.glb").status_code==409
