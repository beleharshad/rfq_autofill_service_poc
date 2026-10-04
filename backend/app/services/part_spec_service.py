"""Read-only bridge from existing job artifacts to the new part contract.

Legacy summaries lack accepted source/revision/body provenance. Their numbers
remain candidates regardless of confidence score or presence of a STEP/GLB file.
"""

import hashlib
import json
import math

from fastapi import HTTPException

from app.models.part_spec import DimensionCandidate, Evidence, PartSpec, ReviewIssue, SourceDocument
from app.storage.file_storage import FileStorage


def _positive_number(value):
    # bool is an int in Python; never accept it as a dimension.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def adapt_legacy_summary(job_id: str, summary: dict | None,
                         sources: list[SourceDocument]) -> PartSpec:
    spec = PartSpec(job_id=job_id, sources=sources)

    def issue(code, message):
        spec.issues.append(ReviewIssue(code=code, message=message))

    issue("unresolved_identity", "Part, revision, manufacturing state and body selection require review.")
    issue("legacy_geometry_unvalidated", "Existing models and quote results have not been validated by the new pipeline.")
    source = next((s for s in sources if s.role == "legacy_summary"), None)
    if summary is None or source is None:
        issue("missing_summary", "No readable part summary is available yet.")
    else:
        spec.readiness.facts = "needs_review"
        units = summary.get("units")
        length_unit = units.get("length") if isinstance(units, dict) else None
        unit = {"in": "in", "inch": "in", "inches": "in", "mm": "mm"}.get(
            str(length_unit).strip().lower())

        def candidate(name, value, source_unit, pointers):
            normalized = value * (25.4 if source_unit == "in" else 1)
            if not math.isfinite(normalized):
                issue("invalid_dimension", "A dimension exceeds the supported numerical range.")
                return
            spec.dimensions.append(DimensionCandidate(
                name=name, value=normalized, source_value=value, source_unit=source_unit,
                evidence=[Evidence(document_id=source.document_id, json_pointer=p) for p in pointers],
            ))

        segments = summary.get("segments")
        if unit is None:
            issue("unknown_segment_units", "Segment dimensions have missing or unsupported units; no OD was inferred.")
        elif not isinstance(segments, list) or not segments:
            issue("missing_segments", "No segment geometry is available for an OD candidate.")
        else:
            ods = [_positive_number(s.get("od_diameter")) if isinstance(s, dict) else None for s in segments]
            if any(v is None for v in ods):
                issue("invalid_segment_od", "One or more segment diameters are invalid; a partial maximum would be misleading.")
            else:
                maximum = max(ods)
                candidate("maximum_outer_diameter", maximum, unit,
                          [f"/segments/{i}/od_diameter" for i, v in enumerate(ods) if v == maximum])

        totals = summary.get("totals")
        # This existing field explicitly promises inches, even when segments use mm.
        length = _positive_number(totals.get("total_length_in")) if isinstance(totals, dict) else None
        if length is None:
            issue("missing_axial_length", "No valid total_length_in value is available; length was not guessed.")
        else:
            candidate("axial_length", length, "in", ["/totals/total_length_in"])
        if spec.dimensions:
            spec.readiness.measurements = "needs_review"
        issue("candidate_dimensions", "Legacy dimensions are model candidates, not verified finished sizes or tolerance limits.")
    return spec


class PartSpecService:
    def __init__(self, file_storage: FileStorage | None = None):
        self.files = file_storage or FileStorage()

    def get_snapshot(self, job_id: str) -> PartSpec:
        sources = []
        summary = None
        parse_issue = None
        paths = sorted(set(self.files.list_input_files(job_id)))
        paths = [p for p in paths if not p.rsplit("/", 1)[-1].startswith("~$")]
        paths.append("outputs/part_summary.json")
        for relative_path in paths:
            try:
                path, _, _ = self.files.get_file_info(job_id, relative_path)
            except HTTPException as exc:
                if exc.status_code == 404:
                    continue
                raise
            is_summary = relative_path == "outputs/part_summary.json"
            digest = hashlib.sha256()
            if is_summary:
                # Bound reads and don't hash one version then parse a different one.
                with path.open("rb") as stream:
                    content = stream.read(8 * 1024 * 1024 + 1)
                if len(content) > 8 * 1024 * 1024:
                    parse_issue = "Part summary exceeds the 8 MiB adapter limit."
                    continue
                digest.update(content)
                try:
                    summary = json.loads(content)
                    if not isinstance(summary, dict):
                        raise ValueError("Expected an object")
                except (ValueError, UnicodeDecodeError, RecursionError):
                    summary = None
                    parse_issue = "Part summary is incomplete or invalid JSON; retry after processing finishes."
            else:
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
            content_hash = digest.hexdigest()
            document_id = hashlib.sha256(f"{job_id}:{relative_path}:{content_hash}".encode()).hexdigest()
            sources.append(SourceDocument(document_id=document_id, path=relative_path,
                                          sha256=content_hash,
                                          role="legacy_summary" if is_summary else "unclassified"))
        spec = adapt_legacy_summary(job_id, summary, sources)
        if parse_issue:
            spec.issues.append(ReviewIssue(code="invalid_summary", message=parse_issue))
        if len([s for s in sources if s.role == "unclassified"]) > 1:
            spec.issues.append(ReviewIssue(code="source_association_required",
                message="Multiple inputs exist; their part and revision relationships have not been established."))
        payload = json.dumps(spec.model_dump(exclude={"snapshot_id"}), sort_keys=True, separators=(",", ":"))
        spec.snapshot_id = hashlib.sha256(payload.encode()).hexdigest()
        return spec
