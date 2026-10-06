from app.services.turned_profile_validation import validate_turned_profile
from app.services.turned_profile_validation import require_review_for_issues


def test_reported_false_accept_with_empty_profile_requires_review():
    data = dict(bore_type='through', id_in=.993, od_in=2.25,
                length_in=4.88, axial_profile=[])
    validation = dict(recommendation='ACCEPT', overall_confidence=.9,
                      cross_checks=['id_in is less than od_in - OK'])
    issues = validate_turned_profile(data)
    require_review_for_issues(validation, issues)
    assert issues
    assert validation['recommendation'] == 'REVIEW'
    assert issues[0] in validation['cross_checks']
    # Do not invent a solid profile merely because extraction failed.
    assert data['axial_profile'] == []


def test_through_bore_cannot_have_a_solid_interval():
    data = dict(bore_type='through', length_in=2, axial_profile=[dict(z_start=0, z_end=2, od_diameter=2, id_diameter=0)])
    assert validate_turned_profile(data)


def test_solid_does_not_keep_scalar_bore():
    data = dict(bore_type='solid', id_in=.993, max_id_in=.999)
    assert validate_turned_profile(data) == []
    assert data['id_in'] is None and data['max_id_in'] is None


def test_conflicting_solid_profile_is_rejected():
    data = dict(bore_type='solid', length_in=2, axial_profile=[dict(z_start=0, z_end=2, od_diameter=2, id_diameter=1)])
    assert validate_turned_profile(data)
    assert data['axial_profile'] == []


def test_blind_profile_retains_solid_bottom():
    data = dict(bore_type='blind', length_in=2, axial_profile=[dict(z_start=0, z_end=1, od_diameter=2, id_diameter=1), dict(z_start=1, z_end=2, od_diameter=2, id_diameter=0)])
    assert validate_turned_profile(data) == []
    assert len(data['axial_profile']) == 2


def test_wrong_overall_length_is_rejected():
    data = dict(length_in=3, axial_profile=[dict(z_start=0, z_end=2, od_diameter=2, id_diameter=0)])
    assert validate_turned_profile(data)
    assert data['axial_profile'] == []
