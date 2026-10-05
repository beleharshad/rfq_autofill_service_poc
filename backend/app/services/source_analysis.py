"""Bind the existing PDF extractor to one retained source, never a job's first PDF."""
import hashlib
import json
import math
import os
import tempfile
import uuid
from pathlib import Path

from fastapi import HTTPException
from app.storage.document_registry import DocumentRegistry


def analyze_source(registry: DocumentRegistry, document_id: str, extractor=None):
    doc = next((d for d in registry.read()["documents"] if d["document_id"] == document_id), None)
    if doc is None:
        raise HTTPException(404, "Document not registered")
    if not doc["path"].lower().endswith(".pdf"):
        raise HTTPException(422, "This extraction adapter supports PDF sources; use solid import for STEP.")
    association = doc["association"]
    if not association or association["role"] != "finished_drawing" or association["manufacturing_state"] != "finished":
        raise HTTPException(422, "Select an associated finished drawing. Process dimensions cannot populate finished geometry.")
    source = registry.root / "blobs" / doc["sha256"]
    if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != doc["sha256"]:
        raise HTTPException(409, "Retained source integrity check failed")
    if extractor is None:
        from app.services.pdf_llm_pipeline import run_pipeline
        extractor = run_pipeline
    result = extractor(source)
    extracted = result.get("extracted") if isinstance(result, dict) else None
    extracted = extracted if isinstance(extracted, dict) else {}
    # JSON permits neither NaN nor Infinity; preserve them as unknown in diagnostics.
    extracted = json.loads(json.dumps(extracted), parse_constant=lambda _: None)
    candidates = []
    # The legacy parser explicitly specifies inches. Its max_od_in is stock, not finished OD.
    for field, name in [("od_in", "maximum_outer_diameter"), ("length_in", "axial_length")]:
        value = extracted.get(field)
        if isinstance(value, bool) or not isinstance(value, (float, int)):
            continue
        try:
            converted = float(value)*25.4
        except OverflowError:
            continue
        if not math.isfinite(converted) or converted <= 0:
            continue
        candidates.append({"name": name, "value_mm": converted, "source_value": value,
                           "source_unit": "in", "acceptance": "unverified",
                           "json_pointer": f"/extracted/{field}"})
    analysis = {"analysis_id": uuid.uuid4().hex, "document_id": document_id, "sha256": doc["sha256"],
                "registry_version": registry.read()["version"], "adapter": "source-pdf-v1",
                "candidates": candidates, "extracted": extracted,
                "limitations": ["Candidate extraction only; review all dimensions and features against the source.",
                                "No feature positions or solid topology are accepted automatically.",
                                "Raw stock max_od_in/max_length_in are excluded from finished dimensions."]}
    directory = registry.root / "analysis"
    directory.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=directory)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(analysis, stream, allow_nan=False)
        os.replace(name, directory / f"{analysis['analysis_id']}.json")
    finally:
        Path(name).unlink(missing_ok=True)
    return analysis
