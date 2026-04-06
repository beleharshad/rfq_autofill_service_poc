"""STEP upload processing and analysis helpers."""

from __future__ import annotations

import json
import math
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from app.geometry.feature_extractor import FeatureExtractor, OCC_AVAILABLE
from app.services import llm_service
from app.services.pdf_llm_pipeline import _parse_json_response
from app.services.step_to_glb_converter import StepToGlbConverter
from app.storage.file_storage import FileStorage

try:
	from OCC.Core.BRepBndLib import brepbndlib_Add
	from OCC.Core.Bnd import Bnd_Box
	from OCC.Core.IFSelect import IFSelect_RetDone
	from OCC.Core.STEPControl import STEPControl_Reader
	from OCC.Core.TopAbs import TopAbs_FACE
	from OCC.Core.TopExp import TopExp_Explorer

	_STEP_READER_AVAILABLE = True
except ImportError:
	_STEP_READER_AVAILABLE = False


_STEP_ANALYSIS_PROMPT = """\
You are analyzing a STEP CAD model summary, not a PDF drawing and not OCR text.

Your job is to extract manufacturing-relevant dimensions from CAD geometry only.
Use ONLY the provided STEP metadata, geometry summary, and deterministic candidates.

Do NOT invent or guess:
- material
- finish
- tolerances
- revision
- quantity
- raw-stock / RM dimensions beyond what is explicitly derivable

If a field is not supported by STEP metadata or geometry, return null.

Return ONLY valid JSON with this exact shape:
{
	"extracted": {
		"part_number": <string|null>,
		"part_name": <string|null>,
		"material": <string|null>,
		"quantity": <number|null>,
		"od_in": <number|null>,
		"max_od_in": <number|null>,
		"id_in": <number|null>,
		"max_id_in": <number|null>,
		"length_in": <number|null>,
		"max_length_in": <number|null>,
		"tolerance_od": <string|null>,
		"tolerance_id": <string|null>,
		"tolerance_length": <string|null>,
		"finish": <string|null>,
		"revision": <string|null>
	},
	"validation": {
		"recommendation": "ACCEPT" | "REVIEW" | "REJECT",
		"overall_confidence": <number>,
		"fields": {
			"od_in": {"value": <number|null>, "confidence": <number>, "issue": <string|null>},
			"id_in": {"value": <number|null>, "confidence": <number>, "issue": <string|null>},
			"length_in": {"value": <number|null>, "confidence": <number>, "issue": <string|null>},
			"max_od_in": {"value": <number|null>, "confidence": <number>, "issue": <string|null>},
			"max_id_in": {"value": <number|null>, "confidence": <number>, "issue": <string|null>},
			"max_length_in": {"value": <number|null>, "confidence": <number>, "issue": <string|null>}
		},
		"cross_checks": [<string>, ...]
	}
}

Geometry-first rules:
1. `od_in`
	 - Use the largest finished outer diameter visible in the STEP-derived geometry summary.
	 - Prefer segment OD values / totals over file-name heuristics.

2. `length_in`
	 - Use the full finished axial span from the geometry summary.
	 - Do not confuse local feature lengths with overall part length.

3. `id_in` and `max_id_in`
	 - Consider only positive internal bore diameters.
	 - If one positive bore exists: `id_in = max_id_in = that bore`.
	 - If multiple positive bores exist: `id_in = smallest`, `max_id_in = largest`.
	 - If no positive bore exists, both should be null.

4. `max_od_in` and `max_length_in`
	 - STEP files usually describe finished geometry, not raw stock.
	 - If no explicit raw-stock information exists, set `max_od_in = od_in` and `max_length_in = length_in`.
	 - Mention this fallback in `cross_checks` and the affected field `issue`.

5. Metadata fields
	 - `part_number` may come from a trustworthy STEP product id or file stem.
	 - `part_name` may come from a trustworthy STEP product name or file stem.
	 - Keep unsupported metadata null.

Metadata quality rules:
- Treat blank names, `None`, `-None`, single spaces, and auto-generated CAD export tokens as low-trust metadata.
- If STEP `product_name` or `product_id` looks auto-generated but `file_stem` is human-readable, prefer `file_stem`.
- Do not blindly trust internal CAD-export identifiers as customer-facing part names.

Multi-body / assembly rules:
- If the STEP metadata suggests multiple solids or a top-level assembly-like context, do NOT assume a simple single turned component.
- In that case, still extract available geometry-derived dimensions, but lower confidence and prefer `REVIEW`.
- Mention multi-body / assembly suspicion in `cross_checks`.

Validation rules:
- Reject impossible geometry such as `id_in >= od_in` when both exist.
- Review when only fallback / bbox-style data is available.
- Review when the STEP appears to be a multi-body or assembly-like export rather than one clean turned part.
- Accept when geometry-derived values are internally consistent.
- Prefer deterministic candidates when they already satisfy the rules.

STEP METADATA:
{metadata_json}

STEP GEOMETRY SUMMARY:
{geometry_json}

DETERMINISTIC CANDIDATES:
{candidate_json}
"""


def _looks_generic_step_name(value: Optional[str]) -> bool:
	"""Return True when STEP metadata looks autogenerated or unhelpful."""
	if not value:
		return True
	text = value.strip()
	if not text:
		return True
	lower = text.lower()
	if lower in {"none", "-none", "null", "unknown"}:
		return True
	if lower.endswith("-none"):
		return True
	if re.fullmatch(r"[a-z]{2,8}\d{4,}[a-z0-9]*", lower):
		return True
	if re.fullmatch(r"[a-z0-9_-]{12,}", lower) and not re.search(r"[\s()]", text):
		return True
	return False


class StepAnalysisService:
	"""Process uploaded STEP files into summary + analysis artifacts."""

	def __init__(self) -> None:
		self.file_storage = FileStorage()

	def process_uploaded_step(self, job_id: str, step_input_path: Path) -> Dict[str, Any]:
		"""Generate summary artifacts for an uploaded STEP/STP file."""
		outputs_path = self.file_storage.get_outputs_path(job_id)
		outputs_path.mkdir(parents=True, exist_ok=True)

		canonical_step_path = outputs_path / "model.step"
		shutil.copyfile(step_input_path, canonical_step_path)

		part_summary = self._generate_part_summary(canonical_step_path)
		(outputs_path / "part_summary.json").write_text(
			json.dumps(part_summary, indent=2), encoding="utf-8"
		)

		analysis = self._build_deterministic_result(canonical_step_path, part_summary)
		(outputs_path / "llm_analysis.json").write_text(
			json.dumps(analysis, indent=2), encoding="utf-8"
		)

		glb_path = outputs_path / "model.glb"
		try:
			converter = StepToGlbConverter()
			if converter.available:
				converter.convert_step_to_glb(canonical_step_path, glb_path, check_cache=False)
		except Exception:
			pass

		return {
			"step_path": str(canonical_step_path),
			"part_summary_path": str(outputs_path / "part_summary.json"),
			"analysis_path": str(outputs_path / "llm_analysis.json"),
			"glb_generated": glb_path.exists(),
		}

	def analyze_step_job(self, job_id: str, step_input_path: Path) -> Dict[str, Any]:
		"""Run a STEP-aware analysis prompt for an uploaded STEP/STP job."""
		outputs_path = self.file_storage.get_outputs_path(job_id)
		outputs_path.mkdir(parents=True, exist_ok=True)

		canonical_step_path = outputs_path / "model.step"
		if not canonical_step_path.exists():
			shutil.copyfile(step_input_path, canonical_step_path)

		part_summary_path = outputs_path / "part_summary.json"
		if part_summary_path.exists():
			part_summary = json.loads(part_summary_path.read_text(encoding="utf-8-sig"))
		else:
			part_summary = self._generate_part_summary(canonical_step_path)
			part_summary_path.write_text(json.dumps(part_summary, indent=2), encoding="utf-8")

		deterministic = self._build_deterministic_result(canonical_step_path, part_summary)
		metadata = self._extract_step_metadata(canonical_step_path)

		prompt = _STEP_ANALYSIS_PROMPT.format(
			metadata_json=json.dumps(metadata, indent=2),
			geometry_json=json.dumps(part_summary, indent=2),
			candidate_json=json.dumps(deterministic.get("extracted", {}), indent=2),
		)

		result = deterministic
		try:
			raw = llm_service.generate_text(
				prompt,
				temperature=0.0,
				max_output_tokens=2048,
			)
			parsed = _parse_json_response(raw)
			result = self._merge_prompt_result(parsed, deterministic)
		except Exception:
			result = deterministic

		(outputs_path / "llm_analysis.json").write_text(
			json.dumps(result, indent=2), encoding="utf-8"
		)
		return result

	def _generate_part_summary(self, step_path: Path) -> Dict[str, Any]:
		metadata = self._extract_step_metadata(step_path)
		unit_label, scale_to_in, unit_note = self._detect_length_unit(step_path)

		if not _STEP_READER_AVAILABLE or not OCC_AVAILABLE:
			raise RuntimeError("STEP analysis requires pythonocc-core on the backend")

		solid = self._read_step_solid(step_path)
		try:
			return self._build_feature_summary(solid, scale_to_in, unit_label, unit_note, metadata)
		except Exception:
			return self._build_bbox_summary(solid, scale_to_in, unit_label, unit_note, metadata)

	def _read_step_solid(self, step_path: Path):
		reader = STEPControl_Reader()
		status = reader.ReadFile(str(step_path))
		if status != IFSelect_RetDone:
			raise RuntimeError(f"Failed to read STEP file: {step_path.name}")
		reader.TransferRoots()
		shape = reader.OneShape()
		if shape is None or shape.IsNull():
			raise RuntimeError(f"STEP file produced an empty shape: {step_path.name}")
		return shape

	def _build_feature_summary(
		self,
		solid,
		scale_to_in: float,
		unit_label: str,
		unit_note: str,
		metadata: Dict[str, Any],
	) -> Dict[str, Any]:
		extractor = FeatureExtractor()
		extractor.set_reference_axis(None)
		collection = extractor.extract_features(solid)
		turned_stack = extractor.build_turned_part_stack(collection, tolerance=1e-6)
		if turned_stack is None or not turned_stack.segments:
			raise RuntimeError("Feature extraction produced no turned segments")

		def _len(v: Any) -> float:
			return float(v) * scale_to_in

		def _area(v: Any) -> float:
			return float(v) * (scale_to_in ** 2)

		def _vol(v: Any) -> float:
			return float(v) * (scale_to_in ** 3)

		segments_list = []
		for seg in turned_stack.segments:
			segments_list.append(
				{
					"z_start": _len(seg.z_start),
					"z_end": _len(seg.z_end),
					"od_diameter": _len(seg.od_diameter),
					"id_diameter": _len(seg.id_diameter),
					"wall_thickness": _len(seg.wall_thickness),
					"volume_in3": _vol(seg.volume()),
					"od_area_in2": _area(seg.od_surface_area()),
					"id_area_in2": _area(seg.id_surface_area()),
				}
			)

		z_min = min(seg["z_start"] for seg in segments_list)
		z_max = max(seg["z_end"] for seg in segments_list)

		face_explorer = TopExp_Explorer(solid, TopAbs_FACE)
		total_faces = 0
		while face_explorer.More():
			total_faces += 1
			face_explorer.Next()

		feature_counts = {
			"external_cylinders": len([c for c in collection.cylinders if c.is_external]),
			"internal_bores": len(collection.holes) + len([c for c in collection.cylinders if not c.is_external]),
			"planar_faces": len(collection.planar_faces),
			"total_faces": total_faces,
		}

		positive_ids = [float(seg["id_diameter"]) for seg in segments_list if float(seg["id_diameter"]) > 0]
		max_od = max(float(seg["od_diameter"]) for seg in segments_list)
		max_id = max(positive_ids) if positive_ids else 0.0

		totals = {
			"volume_in3": _vol(turned_stack.total_volume()),
			"od_area_in2": _area(turned_stack.total_od_surface_area()),
			"id_area_in2": _area(turned_stack.total_id_surface_area()),
			"end_face_area_start_in2": _area(turned_stack.end_face_area_start()),
			"end_face_area_end_in2": _area(turned_stack.end_face_area_end()),
			"od_shoulder_area_in2": _area(turned_stack.od_shoulder_area()),
			"id_shoulder_area_in2": _area(turned_stack.id_shoulder_area()),
			"planar_ring_area_in2": _area(turned_stack.total_planar_ring_area()),
			"total_surface_area_in2": _area(turned_stack.total_surface_area()),
			"total_length_in": z_max - z_min,
			"max_od_in": max_od,
			"max_id_in": max_id,
		}

		return {
			"schema_version": "0.1",
			"generated_at_utc": datetime.now(timezone.utc).isoformat(),
			"units": {"length": "in", "area": "in^2", "volume": "in^3"},
			"scale_report": {
				"method": "step_upload",
				"confidence": 1.0,
				"notes": unit_note,
			},
			"z_range": [z_min, z_max],
			"segments": segments_list,
			"totals": totals,
			"feature_counts": feature_counts,
			"inference_metadata": {
				"mode": "auto_detect",
				"overall_confidence": 1.0,
				"source": "uploaded_step",
				"input_unit": unit_label,
				"file_name": metadata.get("file_name"),
			},
		}

	def _build_bbox_summary(
		self,
		solid,
		scale_to_in: float,
		unit_label: str,
		unit_note: str,
		metadata: Dict[str, Any],
	) -> Dict[str, Any]:
		bbox = Bnd_Box()
		brepbndlib_Add(solid, bbox)
		x_min, y_min, z_min, x_max, y_max, z_max = bbox.Get()
		dims = sorted([
			abs(float(x_max) - float(x_min)) * scale_to_in,
			abs(float(y_max) - float(y_min)) * scale_to_in,
			abs(float(z_max) - float(z_min)) * scale_to_in,
		])
		length_in = dims[-1] if dims else 0.0
		od_in = dims[-2] if len(dims) >= 2 else length_in
		volume_in3 = math.pi * ((od_in / 2.0) ** 2) * length_in if od_in > 0 and length_in > 0 else 0.0

		segment = {
			"z_start": 0.0,
			"z_end": length_in,
			"od_diameter": od_in,
			"id_diameter": 0.0,
			"wall_thickness": od_in / 2.0,
			"volume_in3": volume_in3,
			"od_area_in2": math.pi * od_in * length_in if od_in > 0 and length_in > 0 else 0.0,
			"id_area_in2": 0.0,
		}

		return {
			"schema_version": "0.1",
			"generated_at_utc": datetime.now(timezone.utc).isoformat(),
			"units": {"length": "in", "area": "in^2", "volume": "in^3"},
			"scale_report": {
				"method": "step_upload_bbox_fallback",
				"confidence": 0.7,
				"notes": f"{unit_note}. Fell back to STEP bounding-box analysis.",
			},
			"z_range": [0.0, length_in],
			"segments": [segment],
			"totals": {
				"volume_in3": volume_in3,
				"od_area_in2": segment["od_area_in2"],
				"id_area_in2": 0.0,
				"end_face_area_start_in2": math.pi * ((od_in / 2.0) ** 2) if od_in > 0 else 0.0,
				"end_face_area_end_in2": math.pi * ((od_in / 2.0) ** 2) if od_in > 0 else 0.0,
				"od_shoulder_area_in2": 0.0,
				"id_shoulder_area_in2": 0.0,
				"planar_ring_area_in2": 0.0,
				"total_surface_area_in2": (math.pi * od_in * length_in) + (2.0 * math.pi * ((od_in / 2.0) ** 2)) if od_in > 0 and length_in > 0 else 0.0,
				"total_length_in": length_in,
				"max_od_in": od_in,
				"max_id_in": 0.0,
			},
			"feature_counts": {
				"external_cylinders": 1 if od_in > 0 else 0,
				"internal_bores": 0,
				"planar_faces": 2 if od_in > 0 else 0,
				"total_faces": 3 if od_in > 0 else 0,
			},
			"inference_metadata": {
				"mode": "auto_detect",
				"overall_confidence": 0.7,
				"source": "uploaded_step_bbox_fallback",
				"input_unit": unit_label,
				"file_name": metadata.get("file_name"),
			},
		}

	def _extract_step_metadata(self, step_path: Path) -> Dict[str, Any]:
		text = step_path.read_text(encoding="utf-8", errors="ignore")
		stem = step_path.stem
		file_name_match = re.search(r"FILE_NAME\('([^']*)'", text, re.IGNORECASE)
		product_match = re.search(r"PRODUCT\('([^']*)'\s*,\s*'([^']*)'", text, re.IGNORECASE)
		schema_match = re.search(r"FILE_SCHEMA\s*\(\('\s*([^']+?)\s*'\)\)", text, re.IGNORECASE)
		representation_match = re.search(r"REPRESENTATION_CONTEXT\('([^']*)'\s*,\s*'([^']*)'\)", text, re.IGNORECASE)
		manifold_count = len(re.findall(r"\bMANIFOLD_SOLID_BREP\b", text, re.IGNORECASE))
		return {
			"file_name": file_name_match.group(1).strip() if file_name_match else step_path.name,
			"file_stem": stem,
			"product_name": product_match.group(1).strip() if product_match else None,
			"product_id": product_match.group(2).strip() if product_match else None,
			"schema": schema_match.group(1).strip() if schema_match else None,
			"representation_name": representation_match.group(1).strip() if representation_match else None,
			"representation_context": representation_match.group(2).strip() if representation_match else None,
			"body_count": manifold_count,
		}

	def _detect_length_unit(self, step_path: Path) -> Tuple[str, float, str]:
		text = step_path.read_text(encoding="utf-8", errors="ignore").upper()
		if "CONVERSION_BASED_UNIT('INCH'" in text or "CONVERSION_BASED_UNIT('INCH " in text or "INCH" in text:
			return "in", 1.0, "Detected STEP length unit: inch"
		if ".MILLI." in text or "MILLIMETRE" in text or "MILLIMETER" in text:
			return "mm", 1.0 / 25.4, "Detected STEP length unit: millimetre"
		if ".METRE." in text or "METRE" in text or "METER" in text:
			return "m", 39.37007874015748, "Detected STEP length unit: metre"
		return "unknown", 1.0, "STEP length unit not found in header; assumed inches"

	def _build_deterministic_result(self, step_path: Path, part_summary: Dict[str, Any]) -> Dict[str, Any]:
		metadata = self._extract_step_metadata(step_path)
		totals = part_summary.get("totals") or {}
		segments = part_summary.get("segments") or []

		ods = [float(seg.get("od_diameter") or 0) for seg in segments if float(seg.get("od_diameter") or 0) > 0]
		ids = sorted(float(seg.get("id_diameter") or 0) for seg in segments if float(seg.get("id_diameter") or 0) > 0)
		od_in = float(totals.get("max_od_in") or (max(ods) if ods else 0.0)) or None
		length_in = float(totals.get("total_length_in") or 0.0) or None
		id_in = ids[0] if ids else None
		max_id_in = ids[-1] if ids else None

		file_stem = metadata.get("file_stem")
		product_id = metadata.get("product_id")
		product_name = metadata.get("product_name")
		body_count = int(metadata.get("body_count") or 0)
		representation_context = str(metadata.get("representation_context") or "")
		part_number = file_stem if _looks_generic_step_name(product_id) else (product_id or file_stem)
		part_name = file_stem if _looks_generic_step_name(product_name) else (product_name or file_stem)

		extracted = {
			"part_number": part_number,
			"part_name": part_name,
			"material": None,
			"quantity": None,
			"od_in": od_in,
			"max_od_in": od_in,
			"id_in": id_in,
			"max_id_in": max_id_in,
			"length_in": length_in,
			"max_length_in": length_in,
			"tolerance_od": None,
			"tolerance_id": None,
			"tolerance_length": None,
			"finish": None,
			"revision": None,
		}

		cross_checks = [
			"Analyzed from uploaded STEP geometry rather than PDF OCR.",
			"Raw material dimensions are not present in STEP; max_od_in and max_length_in default to finish dimensions.",
		]
		if _looks_generic_step_name(product_id) or _looks_generic_step_name(product_name):
			cross_checks.append(
				"STEP product metadata looked autogenerated or low-trust; used the file stem as the preferred human-readable identifier."
			)
		if body_count > 1:
			cross_checks.append(
				f"STEP file contains {body_count} manifold solid bodies; this may be a multi-body part or assembly-style export."
			)
		if representation_context:
			cross_checks.append(f"STEP representation context: {representation_context}.")
		if ids:
			cross_checks.append(
				f"Detected {len(ids)} positive bore diameter candidate(s) from STEP geometry; id_in uses the smallest and max_id_in uses the largest."
			)
		else:
			cross_checks.append("No positive internal bore diameters were detected in the STEP geometry.")

		fields = {
			"od_in": {"value": od_in, "confidence": 0.98 if od_in else 0.0, "issue": None if od_in else "OD not resolved from STEP geometry"},
			"id_in": {"value": id_in, "confidence": 0.94 if id_in else 0.6, "issue": None if id_in else "No finished bore detected in STEP geometry"},
			"length_in": {"value": length_in, "confidence": 0.98 if length_in else 0.0, "issue": None if length_in else "Length not resolved from STEP geometry"},
			"max_od_in": {"value": od_in, "confidence": 0.7 if od_in else 0.0, "issue": "Raw-stock OD unavailable in STEP; using finish OD" if od_in else "OD unavailable"},
			"max_id_in": {"value": max_id_in, "confidence": 0.9 if max_id_in else 0.6, "issue": None if max_id_in else "No stepped bore detected"},
			"max_length_in": {"value": length_in, "confidence": 0.7 if length_in else 0.0, "issue": "Raw-stock length unavailable in STEP; using finish length" if length_in else "Length unavailable"},
		}

		code_issues = self._build_code_issues(extracted)
		assembly_like = body_count > 1 or "ASSEMBLY" in representation_context.upper()
		recommendation = "ACCEPT" if not assembly_like and not code_issues and od_in and length_in else "REVIEW"

		return {
			"pdf_text_length": 0,
			"vision_mode": False,
			"analysis_source": "step",
			"extracted": extracted,
			"validation": {
				"recommendation": recommendation,
				"overall_confidence": 0.95 if recommendation == "ACCEPT" else (0.6 if assembly_like else 0.7),
				"fields": fields,
				"cross_checks": cross_checks,
			},
			"code_issues": code_issues,
			"valid": recommendation == "ACCEPT" and not code_issues,
		}

	def _merge_prompt_result(self, parsed: Dict[str, Any], fallback: Dict[str, Any]) -> Dict[str, Any]:
		result = dict(fallback)
		extracted = dict(fallback.get("extracted") or {})
		extracted.update(parsed.get("extracted") or {})

		for key in ("od_in", "max_od_in", "id_in", "max_id_in", "length_in", "max_length_in"):
			try:
				if extracted.get(key) is not None:
					extracted[key] = float(extracted[key])
			except Exception:
				extracted[key] = (fallback.get("extracted") or {}).get(key)

		validation = dict((fallback.get("validation") or {}))
		validation.update(parsed.get("validation") or {})
		fields = dict(((fallback.get("validation") or {}).get("fields") or {}))
		fields.update((parsed.get("validation") or {}).get("fields") or {})
		validation["fields"] = fields
		if not validation.get("cross_checks"):
			validation["cross_checks"] = (fallback.get("validation") or {}).get("cross_checks", [])

		result["extracted"] = extracted
		result["validation"] = validation
		result["code_issues"] = self._build_code_issues(extracted)
		result["valid"] = (
			validation.get("recommendation", "REVIEW") == "ACCEPT"
			and not result["code_issues"]
		)
		result["analysis_source"] = "step"
		return result

	def _build_code_issues(self, extracted: Dict[str, Any]) -> list[str]:
		issues: list[str] = []
		try:
			od = float(extracted.get("od_in") or 0)
			id_ = float(extracted.get("id_in") or 0)
			length = float(extracted.get("length_in") or 0)
			if od <= 0:
				issues.append("od_in must be positive")
			if length <= 0:
				issues.append("length_in must be positive")
			if id_ > 0 and od > 0 and id_ >= od:
				issues.append("id_in must be smaller than od_in")
		except Exception:
			issues.append("Failed to validate STEP-derived dimensions")
		return issues