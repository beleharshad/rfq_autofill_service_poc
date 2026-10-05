import math

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.models.accepted_part import AcceptedPartRequest, EvidenceNote, ImportedStep
from app.models.document_registry import AssociationRequest
from app.storage.file_storage import FileStorage
from app.storage.accepted_parts import AcceptedParts
from app.services.generic_geometry import build_geometry

JOB = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


@pytest.fixture
def accepted(tmp_path):
    files = FileStorage(tmp_path)
    files.read_base_paths = [tmp_path]
    files.save_bytes_file(JOB, "drawing.pdf", b"independent reference")
    store = AcceptedParts(files.get_job_path(JOB))
    data = store.registry.read()
    doc = data["documents"][0]["document_id"]
    store.registry.associate(doc, AssociationRequest(expected_version=data["version"], part_number="TEST", revision="A",
                                                     role="finished_drawing", manufacturing_state="finished"))
    request = AcceptedPartRequest(expected_version=0, expected_registry_version=store.registry.read()["version"],
        part_number="TEST", revision="A", governing_document_id=doc,
        evidence=[{"document_id":doc,"page":1,"locator":"Overall and hole dimensions"}],
        base={"kind":"cylinder","diameter":25.4,"length":10}, features=[],
        review_note="Reviewed test geometry", completeness="complete")
    return store, request


def test_correction_history_and_stale_builds(accepted):
    store, request = accepted
    first = store.save(request)
    task = store.enqueue(first["spec_id"])
    with pytest.raises(HTTPException) as error:
        store.save(request)
    assert error.value.status_code == 409
    second = store.save(request.model_copy(update={"expected_version":1}))
    assert second["version"] == 2
    assert store.build(task["build_id"])["stale"]
    with pytest.raises(HTTPException):
        store.enqueue(first["spec_id"])
    assert len(store.list()) == 2


def test_source_change_invalidates_acceptance(accepted):
    store, request = accepted
    first = store.save(request)
    store.registry.associate(request.governing_document_id, AssociationRequest(
        expected_version=store.registry.read()["version"], part_number="TEST", revision="B", role="finished_drawing", manufacturing_state="finished"))
    assert store.get(first["spec_id"], False)["stale"]
    with pytest.raises(HTTPException):
        store.save(request)


def test_raw_source_cannot_govern_finished_geometry(accepted):
    store, request = accepted
    store.registry.associate(request.governing_document_id, AssociationRequest(
        expected_version=store.registry.read()["version"], part_number="TEST", revision="A", role="process_drawing", manufacturing_state="raw"))
    with pytest.raises(HTTPException) as error:
        store.save(request.model_copy(update={"expected_registry_version":store.registry.read()["version"]}))
    assert error.value.status_code == 422


def test_cancelled_build_cannot_publish(accepted):
    store, request = accepted
    spec = store.save(request)
    task = store.enqueue(spec["spec_id"])
    assert store.claim(task["build_id"])
    assert not store.claim(task["build_id"])
    store.cancel(task["build_id"])
    store.finish(task["build_id"], manifest={"wrong":True})
    assert store.build(task["build_id"])["status"] == "cancelled"


def test_unknown_operations_and_incomplete_recipe_rejected(accepted):
    _, request = accepted
    with pytest.raises(ValidationError):
        AcceptedPartRequest(**{**request.model_dump(), "completeness":"partial"})
    with pytest.raises(ValidationError):
        AcceptedPartRequest(**{**request.model_dump(), "features":[{"kind":"execute_python","code":"print(1)"}]})


def run_recipe(accepted, tmp_path, base=None, features=None):
    pytest.importorskip("cadquery")
    store, request = accepted
    raw = request.model_dump()
    if base is not None: raw["base"] = base
    if features is not None: raw["features"] = features
    spec = store.save(AcceptedPartRequest(**raw))
    result = build_geometry(spec, store.registry, tmp_path / "artifacts")
    return result, spec


def test_real_through_hole_and_step_roundtrip(accepted, tmp_path):
    import cadquery as cq
    hole = {"feature_id":"h1","kind":"hole","origin":[0,0,0],"diameter":8,"depth":10,"termination":"through"}
    result, _ = run_recipe(accepted, tmp_path, features=[hole])
    metrics = result["measurements"]
    assert metrics["maximum_outer_cylindrical_diameter_mm"] == pytest.approx(25.4)
    assert metrics["minimum_coaxial_through_bore_mm"] == pytest.approx(8)
    assert metrics["volume_mm3"] == pytest.approx(math.pi*(12.7**2-4**2)*10)
    shape = cq.importers.importStep(str(tmp_path/"artifacts/model.step")).val()
    assert not shape.isInside((0,0,5))
    assert shape.isInside((8,0,5))
    assert shape.Volume() == pytest.approx(metrics["volume_mm3"], rel=1e-7)


def test_prismatic_blind_hole_and_no_fictitious_od(accepted, tmp_path):
    import cadquery as cq
    hole = {"feature_id":"blind","kind":"hole","origin":[0,0,0],"diameter":4,"depth":6,"termination":"blind"}
    result, _ = run_recipe(accepted, tmp_path, base={"kind":"box","width":20,"height":16,"length":10}, features=[hole])
    assert result["measurements"]["maximum_outer_cylindrical_diameter_mm"] is None
    assert result["measurements"]["minimum_coaxial_through_bore_mm"] is None
    assert result["measurements"]["volume_mm3"] == pytest.approx(3200-math.pi*4*6)
    shape = cq.importers.importStep(str(tmp_path/"artifacts/model.step")).val()
    assert shape.isInside((0,0,8)) and not shape.isInside((0,0,3))


@pytest.mark.parametrize("termination,depth", [("through",5),("blind",10)])
def test_wrong_hole_termination_fails(accepted,tmp_path,termination,depth):
    hole = {"feature_id":"bad","kind":"hole","origin":[0,0,0],"diameter":4,"depth":depth,"termination":termination}
    with pytest.raises(ValueError):
        run_recipe(accepted,tmp_path,features=[hole])


def test_real_helical_cut_and_manifest_limitations(accepted,tmp_path):
    import cadquery as cq
    features = [{"feature_id":"bore","kind":"hole","origin":[0,0,0],"diameter":8,"depth":10,"termination":"through"},
        {"feature_id":"thread","kind":"thread","origin":[0,0,0],"side":"internal","major_diameter":10,
         "minor_diameter":8,"pitch":2,"length":10,"groove_width":1,"callout":"Representative test thread"}]
    result,_ = run_recipe(accepted,tmp_path,features=features)
    assert result["features"][1]["removed_volume_mm3"] > 1
    assert any("fit limits" in w for w in result["warnings"])
    shape = cq.importers.importStep(str(tmp_path/"artifacts/model.step")).val()
    # At one angle, alternate groove and land along z. A painted circle cannot satisfy this.
    assert shape.isInside((4.5,0,1)) != shape.isInside((4.5,0,2))


def test_workbook_text_is_not_executable_formula(accepted,tmp_path):
    from openpyxl import load_workbook
    store,req=accepted
    req=req.model_copy(update={"review_note":"=HYPERLINK(\"malicious\")"})
    run_recipe((store,req),tmp_path)
    wb=load_workbook(tmp_path/"artifacts/rfq_review.xlsx")
    cell=next(c for row in wb["Evidence and limitations"] for c in row if c.value == req.review_note)
    assert cell.data_type == "s"


def test_four_holes_and_countersink_preserve_pattern(accepted,tmp_path):
    import cadquery as cq
    features=[]
    for i,(x,y) in enumerate([(7,0),(0,7),(-7,0),(0,-7)]):
        features.append({"feature_id":f"hole{i}","kind":"hole","origin":[x,y,0],"diameter":3,"depth":10,"termination":"through"})
    features.append({"feature_id":"csk","kind":"cone_cut","origin":[7,0,0],"entry_diameter":5,"end_diameter":3,"depth":1})
    result,_=run_recipe(accepted,tmp_path,features=features)
    shape=cq.importers.importStep(str(tmp_path/"artifacts/model.step")).val()
    for x,y in [(7,0),(0,7),(-7,0),(0,-7)]:
        assert not shape.isInside((x,y,5))
    assert shape.isInside((0,0,5))
    assert result["measurements"]["maximum_outer_cylindrical_diameter_mm"]==pytest.approx(25.4)
    assert result["measurements"]["minimum_coaxial_through_bore_mm"] is None


def test_revolved_stepped_profile_and_transverse_bore(accepted,tmp_path):
    import cadquery as cq
    base={"kind":"revolve","profile":[[0,0],[10,0],[10,4],[6,4],[6,12],[0,12]]}
    hole={"feature_id":"cross","kind":"hole","origin":[-6,0,8],"axis":[1,0,0],"diameter":3,"depth":12,"termination":"through"}
    result,_=run_recipe(accepted,tmp_path,base=base,features=[hole])
    shape=cq.importers.importStep(str(tmp_path/"artifacts/model.step")).val()
    assert not shape.isInside((0,0,8))
    assert shape.isInside((0,0,2))
    assert result["measurements"]["axial_length_mm"]==pytest.approx(12)
    assert result["measurements"]["maximum_outer_cylindrical_diameter_mm"]==pytest.approx(20)


def test_external_left_hand_thread_is_real_cut(accepted,tmp_path):
    feature={"feature_id":"external","kind":"thread","origin":[0,0,0],"side":"external","major_diameter":25.4,
             "minor_diameter":23.4,"pitch":2,"length":10,"groove_width":1,"handedness":"left","callout":"Representative LH thread"}
    result,_=run_recipe(accepted,tmp_path,features=[feature])
    assert result["features"][0]["removed_volume_mm3"]>10


def test_step_import_measures_selected_body_not_assembly(accepted,tmp_path):
    import cadquery as cq
    from app.models.document_registry import AssociationRequest
    store,request=accepted
    step=tmp_path/"assembly.step"
    one=cq.Solid.makeBox(5,6,7)
    two=cq.Solid.makeBox(10,11,12).translate((100,0,0))
    cq.exporters.export(cq.Compound.makeCompound([one,two]),str(step))
    doc=store.registry.register(step,"inputs/assembly.step")
    store.registry.associate(doc,AssociationRequest(expected_version=store.registry.read()["version"],part_number="TEST",revision="A",role="cad",manufacturing_state="finished"))
    request=request.model_copy(update={"governing_document_id":doc,"expected_registry_version":store.registry.read()["version"],
        "evidence":[EvidenceNote(document_id=doc,locator="STEP body 1")],
        "base":ImportedStep(kind="step",document_id=doc,body_index=1)})
    result,_=run_recipe((store,request),tmp_path)
    assert result["measurements"]["envelope_width_mm"]==pytest.approx(10)
    assert result["measurements"]["volume_mm3"]==pytest.approx(1320)
