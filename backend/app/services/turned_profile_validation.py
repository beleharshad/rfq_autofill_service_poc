"""Validate the optional legacy viewer profile without inferring missing geometry."""
import math


def validate_turned_profile(extracted):
    issues = []
    solid = extracted.get('bore_type') == 'solid'
    if solid:
        extracted['id_in'] = None
        extracted['max_id_in'] = None
    profile = extracted.get('axial_profile')
    if not profile:
        if 'axial_profile' in extracted:
            issues.append('Axial profile is missing or empty; geometry cannot be verified.')
        return issues
    try:
        if not isinstance(profile, list):
            raise ValueError('profile must be a list')
        end = 0.0
        for seg in profile:
            values = [seg[k] for k in ('z_start', 'z_end', 'od_diameter', 'id_diameter')]
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
                raise ValueError('profile dimensions must be finite numbers')
            start, stop, od, bore = values
            if abs(start - end) > 1e-6 or stop <= start or od <= 0 or bore < 0 or bore >= od:
                raise ValueError('invalid or discontinuous profile')
            if solid and bore != 0:
                raise ValueError('solid classification conflicts with internal profile')
            if extracted.get('bore_type') == 'through' and bore == 0:
                raise ValueError('through-bore classification conflicts with a solid interval')
            end = stop
        length = extracted.get('length_in')
        if not isinstance(length, (int, float)) or not math.isfinite(length) or abs(end - length) > 0.001:
            raise ValueError('profile does not match overall length')
    except (ValueError, KeyError, TypeError) as exc:
        extracted['axial_profile'] = []
        issues.append(str(exc))
    return issues


def require_review_for_issues(validation, issues):
    """Keep the UI recommendation consistent with deterministic validation."""
    if issues:
        validation['recommendation'] = 'REVIEW'
        checks = list(validation.get('cross_checks') or [])
        checks.extend(issue for issue in issues if issue not in checks)
        validation['cross_checks'] = checks
