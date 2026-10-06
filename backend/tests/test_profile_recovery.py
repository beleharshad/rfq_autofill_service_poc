import copy
import io
import json
from PIL import Image
from app.services.profile_recovery import recover_profile, detail_images


def image_bytes():
    out = io.BytesIO()
    Image.new('RGB', (1200, 900), 'white').save(out, 'PNG')
    return out.getvalue()


def candidate():
    return dict(bore_type='solid', od_in=2.25, id_in=None, max_id_in=None,
                length_in=4.88, unresolved=[], evidence=['Two external ends with limit diameter .993/.999; axial ends each 1.25.'],
                axial_profile=[
                    dict(z_start=0, z_end=1.25, od_diameter=.996, id_diameter=0),
                    dict(z_start=1.25, z_end=3.63, od_diameter=2.25, id_diameter=0),
                    dict(z_start=3.63, z_end=4.88, od_diameter=.996, id_diameter=0)])


def test_detail_tiles_preserve_context_and_are_bounded():
    images = detail_images([image_bytes()])
    assert len(images) == 5
    assert Image.open(io.BytesIO(images[0])).size == (1200, 900)
    assert Image.open(io.BytesIO(images[1])).size == (720, 540)


def test_recovery_accepts_supported_stepped_candidate_without_mutating_source():
    source = dict(id_in=.993, bore_type='through', axial_profile=[])
    original = copy.deepcopy(source)
    calls = []
    def generate(prompt, images, **kwargs):
        calls.append((prompt, images))
        return json.dumps(candidate())
    result = recover_profile(source, [image_bytes()], generate, json.loads)
    assert len(result['axial_profile']) == 3
    assert result['id_in'] is None
    assert source == original
    assert len(calls) == 1 and len(calls[0][1]) == 5


def test_recovery_rejects_ambiguity_missing_fields_and_wrong_envelope():
    for mutation in ({'unresolved': ['Unclear witness line']}, {'od_in': 5}, {'axial_profile': []}, {'evidence': []}, {'od_in': float('nan')}):
        data = candidate()
        data.update(mutation)
        assert recover_profile({}, [image_bytes()], lambda *a, **k: json.dumps(data), json.loads) is None
    data = candidate()
    del data['max_id_in']
    assert recover_profile({}, [image_bytes()], lambda *a, **k: json.dumps(data), json.loads) is None
