from app.services.turned_profile_validation import validate_turned_profile


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
