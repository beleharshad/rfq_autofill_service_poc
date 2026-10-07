"""Provider-contract regressions: no live model or guessed geometry dimensions."""
from copy import deepcopy

import pytest

from app.models.automatic import DrawingProposal, DrawingAudit
from app.services import drawing_interpreter as di


@pytest.fixture
def proposal(monkeypatch):
    monkeypatch.setattr(di, 'render_source', lambda source: [b'original-page'])
    return dict(part_number='TEST', revision='A', role='finished_drawing', units='in',
        base=dict(kind='cylinder', diameter=2, length=1), complete=True, unresolved=[],
        features=[
            dict(kind='hole', feature_id='hole1', origin=[0,0,0], axis=[0,0,1],
                 diameter=.434, depth=1, termination='through'),
            dict(kind='thread', feature_id='thread1', origin=[0,0,0], axis=[0,0,1],
                 side='internal', major_diameter=.5, minor_diameter=.434,
                 pitch=1/13, length=.6, groove_width=.03, callout='Test groove')],
        evidence=[dict(target=t, page=1, callout='Synthetic test evidence')
                  for t in ['base','hole1','thread1']])


def invalid_copy(proposal):
    data = deepcopy(proposal)
    data['features'][0]['depth'] = 0
    data['features'][1]['length'] = 0
    return data


def audit():
    return dict(agrees=True, part_number='TEST', revision='A', finished_part=True,
        all_features_accounted_for=True, checked_targets=['base','hole1','thread1'],
        dimensions=[
            dict(metric='axial_length_mm', lower=25.4, upper=25.4, page=1, callout='1 in'),
            dict(metric='maximum_outer_cylindrical_diameter_mm', lower=50.8,
                 upper=50.8, page=1, callout='2 in')], issues=[])


@pytest.mark.parametrize('validates_in_provider', [False, True])
def test_zero_extents_retried_then_independently_audited(proposal, validates_in_provider):
    calls = []
    def provider(pages, prompt, schema):
        assert pages == [b'original-page']
        calls.append((prompt, schema))
        data = invalid_copy(proposal) if len(calls) == 1 else (
            proposal if schema is DrawingProposal else audit())
        return schema.model_validate(data) if validates_in_provider else data
    result, check, pages = di.interpret_drawing(None, provider)
    assert [schema for _, schema in calls] == [DrawingProposal, DrawingProposal, DrawingAudit]
    assert 'features.0.hole.depth' in calls[1][0]
    assert 'features.1.thread.length' in calls[1][0]
    normalized, issues = di.normalize_proposal(result, check, pages)
    assert not issues
    assert normalized['features'][0]['depth'] == pytest.approx(25.4)
    assert normalized['features'][1]['length'] == pytest.approx(15.24)


def test_repeated_invalid_extents_stop_with_concise_error(proposal):
    calls = []
    def provider(pages, prompt, schema):
        calls.append(schema)
        return invalid_copy(proposal)
    with pytest.raises(ValueError, match='after one automatic correction') as exc:
        di.interpret_drawing(None, provider)
    assert calls == [DrawingProposal, DrawingProposal]
    assert 'features.0.hole.depth' in str(exc.value)
    assert 'No geometry was accepted' in str(exc.value)
    assert 'errors.pydantic.dev' not in str(exc.value)


def test_unknown_extents_remain_review_required(proposal):
    calls = []
    def provider(pages, prompt, schema):
        calls.append(schema)
        if len(calls) == 1:
            return invalid_copy(proposal)
        return {**proposal, 'features': [], 'complete': False,
                'unresolved': ['hole1 depth and thread1 length cannot be established']}
    result, check, pages = di.interpret_drawing(None, provider)
    normalized, issues = di.normalize_proposal(result, check, pages)
    assert normalized is None and check is None
    assert result.unresolved[0] in issues
    assert len(calls) == 2


def test_provider_outage_does_not_trigger_schema_retry(proposal):
    calls = []
    def provider(*args):
        calls.append(args)
        raise di.ProviderUnavailable('Drawing provider returned HTTP 429')
    with pytest.raises(di.ProviderUnavailable):
        di.interpret_drawing(None, provider)
    assert len(calls) == 1


def test_invalid_audit_gets_one_correction_without_replacing_proposal(proposal):
    calls = []
    def provider(pages, prompt, schema):
        calls.append(schema)
        if schema is DrawingProposal:
            return proposal
        data = audit()
        if len(calls) == 2:
            data['dimensions'][0]['lower'] = 0
        return data
    result, check, _ = di.interpret_drawing(None, provider)
    assert result.features[1].length == .6
    assert check.dimensions[0].lower == 25.4
    assert calls == [DrawingProposal, DrawingAudit, DrawingAudit]


def test_incomplete_finished_drawing_reports_skipped_audit_without_phantom_evidence(proposal):
    result = DrawingProposal.model_validate({**proposal, 'base': None, 'features': [],
        'evidence': [], 'complete': False, 'unresolved': ['Unsupported rim fillet']})
    normalized, issues = di.normalize_proposal(result, None, 1)
    assert normalized is None
    assert 'Unsupported rim fillet' in issues
    assert any('was not run' in issue for issue in issues)
    assert not any('evidence' in issue.lower() for issue in issues)
    assert not any('unavailable' in issue for issue in issues)
    assert not any(issue.startswith('Source ') for issue in issues)


def test_complete_recipe_still_requires_evidence_and_audit(proposal):
    result = DrawingProposal.model_validate({**proposal, 'evidence': proposal['evidence'][:1]})
    normalized, issues = di.normalize_proposal(result, None, 1)
    assert normalized is None
    assert 'Missing source evidence for: hole1, thread1' in issues
    assert 'Second drawing check is unavailable' in issues


def test_unsupported_classification_is_not_reported_as_process_drawing(proposal):
    result = DrawingProposal.model_validate({**proposal, 'role': 'unsupported', 'complete': False})
    _, issues = di.normalize_proposal(result, None, 1)
    assert 'Source classification is unresolved or unsupported; confirm document role' in issues
    assert not any('process-stage' in issue for issue in issues)
