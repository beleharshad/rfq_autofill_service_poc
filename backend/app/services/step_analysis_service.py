"""STEP upload processing and analysis helpers."""

from __future__ import annotations

import copy
import json
import math
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.geometry.feature_extractor import FeatureExtractor, OCC_AVAILABLE
from app.services import llm_service
from app.services.pdf_llm_pipeline import _parse_json_response
from app.services.step_to_glb_converter import StepToGlbConverter
from app.storage.file_storage import FileStorage

try:
	from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
	from OCC.Core.BRepBndLib import brepbndlib_Add
	from OCC.Core.Bnd import Bnd_Box
	from OCC.Core.IFSelect import IFSelect_RetDone
	from OCC.Core.STEPControl import STEPControl_AsIs, STEPControl_Reader, STEPControl_Writer
	from OCC.Core.TopAbs import TopAbs_FACE, TopAbs_SOLID
	from OCC.Core.TopExp import TopExp_Explorer
	from OCC.Core.gp import gp_Pnt, gp_Trsf

	_STEP_READER_AVAILABLE = True
except ImportError:
	_STEP_READER_AVAILABLE = False


_STEP_ANALYSIS_PROMPT = """\
You are analyzing a STEP CAD model summary, not a PDF drawing and not OCR text.

Your job is to extract manufacturing-relevant dimensions from CAD geometry only.
You are given ONE selected body summary plus a short list of alternative bodies.
Use ONLY the provided STEP metadata, selected-body geometry summary, alternative-body shortlist, and deterministic candidate pack.

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
1. Start with the selected body, but you MAY override it and use ONE alternative body when the selected body is weak.
2. Treat the alternative-body shortlist as comparison context only unless one alternative is clearly stronger. Never blend dimensions across multiple bodies.
3. Prefer the highest-scoring feature-extracted turned body when it is materially more trustworthy than a bbox-fallback body.
4. If the selected body uses bbox fallback, has null key dimensions, or conflicts with a clearly stronger feature-extracted alternative, prefer the stronger feature-extracted alternative.
5. `od_in`
	 - Use the largest finished outer diameter visible in the selected-body geometry summary.
	 - Prefer segment OD values / totals over file-name heuristics.
	 - Ignore tiny sliver segments, zero-length transitions, detached helper solids, and local protrusions that are not the main finished turned envelope.
6. `length_in`
	 - Use the full finished axial span from the selected-body geometry summary.
	 - Do not confuse local feature lengths with overall part length.
	 - Ignore tiny end slivers, short construction fragments, and detached secondary bodies.
7. `id_in` and `max_id_in`
	 - Consider only positive internal bore diameters from the selected body.
	 - Prefer bore diameters that persist over meaningful axial span or recur across multiple segments.
	 - Ignore incidental micro-bores, spot features, local chamfer transitions, and very short counterbores unless they are clearly the finished bore.
	 - If one positive bore exists: `id_in = max_id_in = that bore`.
	 - If multiple positive bores exist: `id_in = smallest`, `max_id_in = largest`.
	 - If no positive bore exists, both should be null.
8. `max_od_in` and `max_length_in`
	 - STEP files usually describe finished geometry, not raw stock.
	 - If no explicit raw-stock information exists, set `max_od_in = od_in` and `max_length_in = length_in`.
	 - Mention this fallback in `cross_checks` and the affected field `issue`.
9. Metadata fields
	 - `part_number` may come from a trustworthy STEP product id or file stem.
	 - `part_name` may come from a trustworthy STEP product name or file stem.
	 - Keep unsupported metadata null.

Metadata quality rules:
- Treat blank names, `None`, `-None`, single spaces, and auto-generated CAD export tokens as low-trust metadata.
- If STEP `product_name` or `product_id` looks auto-generated but `file_stem` is human-readable, prefer `file_stem`.
- Do not blindly trust internal CAD-export identifiers as customer-facing part names.

Multi-body / assembly rules:
- If multiple solids exist, prefer one authoritative body only.
- If an alternative body has higher score, feature extraction success, and more plausible turned dimensions than the selected body, use that alternative body instead.
- Mention ambiguity when alternate candidates are plausible.
- If the selection score is weak or alternatives are close, prefer `REVIEW`.

Validation rules:
- Reject impossible geometry such as `id_in >= od_in` when both exist.
- Review when only fallback / bbox-style data is available.
- Review when the STEP appears to be a multi-body or assembly-like export rather than one clean turned part.
- Accept when geometry-derived values are internally consistent and the selected body is clearly dominant.
- Prefer deterministic candidates when they already satisfy the rules.

STEP METADATA:
{metadata_json}

SELECTED BODY + ALTERNATIVES:
{geometry_json}

DETERMINISTIC CANDIDATE PACK:
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

	def export_selected_body_preview_step(
		self,
		job_id: str,
		source_step_path: Optional[Path] = None,
		output_step_path: Optional[Path] = None,
	) -> Dict[str, Any]:
		"""Export the selected STEP body as an inch-scaled preview STEP.

		This keeps STEP-backed 3D previews aligned with the selected-body summary that
		the frontend uses for camera framing and feature overlays.
		"""
		if not _STEP_READER_AVAILABLE or not OCC_AVAILABLE:
			raise RuntimeError("Selected-body STEP preview requires pythonocc-core on the backend")

		outputs_path = self.file_storage.get_outputs_path(job_id)
		source_step = source_step_path or (outputs_path / "model.step")
		preview_step = output_step_path or (outputs_path / "selected_body_preview.step")
		part_summary_path = outputs_path / "part_summary.json"

		if not source_step.exists():
			raise FileNotFoundError(f"Source STEP not found for preview: {source_step}")
		if not part_summary_path.exists():
			raise FileNotFoundError(f"part_summary.json not found for preview: {part_summary_path}")

		part_summary = json.loads(part_summary_path.read_text(encoding="utf-8-sig"))
		selected_body = part_summary.get("selected_body") or {}
		selected_body_index = int(
			selected_body.get("body_index")
			or (part_summary.get("inference_metadata") or {}).get("selected_body_index")
			or 0
		)

		shape = self._read_step_shape(source_step)
		solids = self._extract_solids(shape)
		if not solids:
			raise RuntimeError(f"No solid bodies found in STEP file: {source_step.name}")
		if selected_body_index < 0 or selected_body_index >= len(solids):
			raise RuntimeError(
				f"Selected body index {selected_body_index} is out of range for {len(solids)} solid(s)"
			)

		preview_shape = solids[selected_body_index]
		unit_label, scale_to_in, unit_note = self._detect_length_unit(source_step)
		scale_applied = 1.0
		if abs(scale_to_in - 1.0) > 1e-9:
			trsf = gp_Trsf()
			trsf.SetScale(gp_Pnt(0.0, 0.0, 0.0), scale_to_in)
			transform = BRepBuilderAPI_Transform(preview_shape, trsf, True)
			transform.Build()
			preview_shape = transform.Shape()
			scale_applied = scale_to_in

		preview_step.parent.mkdir(parents=True, exist_ok=True)
		writer = STEPControl_Writer()
		transfer_result = writer.Transfer(preview_shape, STEPControl_AsIs)
		if transfer_result != IFSelect_RetDone:
			raise RuntimeError(f"Failed to transfer selected STEP body {selected_body_index} for preview export")
		write_result = writer.Write(str(preview_step))
		if write_result != IFSelect_RetDone:
			raise RuntimeError(f"Failed to write selected-body preview STEP: {preview_step}")

		return {
			"preview_step_path": str(preview_step),
			"selected_body_index": selected_body_index,
			"input_unit": unit_label,
			"scale_applied": scale_applied,
			"unit_note": unit_note,
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
			geometry_json=json.dumps(self._build_llm_geometry_payload(part_summary), indent=2),
			candidate_json=json.dumps(self._build_llm_candidate_payload(part_summary, deterministic), indent=2),
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

		shape = self._read_step_shape(step_path)
		solids = self._extract_solids(shape)
		if not solids:
			raise RuntimeError(f"No solid bodies found in STEP file: {step_path.name}")

		body_candidates = self._analyze_body_candidates(
			solids,
			scale_to_in,
			unit_label,
			unit_note,
			metadata,
		)
		selected = self._select_dominant_body(body_candidates)
		return self._build_selected_body_summary(metadata, body_candidates, selected)

	def _read_step_shape(self, step_path: Path):
		reader = STEPControl_Reader()
		status = reader.ReadFile(str(step_path))
		if status != IFSelect_RetDone:
			raise RuntimeError(f"Failed to read STEP file: {step_path.name}")
		reader.TransferRoots()
		shape = reader.OneShape()
		if shape is None or shape.IsNull():
			raise RuntimeError(f"STEP file produced an empty shape: {step_path.name}")
		return shape

	def _extract_solids(self, shape) -> List[Any]:
		solids: List[Any] = []
		explorer = TopExp_Explorer(shape, TopAbs_SOLID)
		while explorer.More():
			solids.append(explorer.Current())
			explorer.Next()
		if not solids and hasattr(shape, "ShapeType") and shape.ShapeType() == TopAbs_SOLID:
			solids.append(shape)
		return solids

	def _analyze_body_candidates(
		self,
		solids: List[Any],
		scale_to_in: float,
		unit_label: str,
		unit_note: str,
		metadata: Dict[str, Any],
	) -> List[Dict[str, Any]]:
		candidates: List[Dict[str, Any]] = []
		for index, solid in enumerate(solids):
			extraction_method = "feature"
			summary_error = None
			try:
				summary = self._build_feature_summary(solid, scale_to_in, unit_label, unit_note, metadata)
			except Exception as exc:
				extraction_method = "bbox"
				summary_error = str(exc)
				summary = self._build_bbox_summary(solid, scale_to_in, unit_label, unit_note, metadata)

			summary = copy.deepcopy(summary)
			summary.setdefault("inference_metadata", {})
			summary["inference_metadata"]["body_index"] = index
			summary["inference_metadata"]["body_count"] = len(solids)
			summary["inference_metadata"]["source"] = (
				"uploaded_step_body" if extraction_method == "feature" else "uploaded_step_body_bbox_fallback"
			)

			score, reasons = self._score_body_candidate(summary, extraction_method)
			candidate = self._build_compact_candidate(index, summary, extraction_method, score, reasons)
			if summary_error:
				candidate["analysis_warning"] = summary_error
			candidates.append(
				{
					"body_index": index,
					"score": score,
					"selection_reasons": reasons,
					"extraction_method": extraction_method,
					"summary": summary,
					"compact": candidate,
				}
			)
		return candidates

	def _select_dominant_body(self, candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
		if not candidates:
			raise RuntimeError("No STEP body candidates were analyzed")
		return max(candidates, key=lambda candidate: float(candidate.get("score") or float("-inf")))

	def _build_selected_body_summary(
		self,
		metadata: Dict[str, Any],
		candidates: List[Dict[str, Any]],
		selected: Dict[str, Any],
	) -> Dict[str, Any]:
		selected_summary = copy.deepcopy(selected["summary"])
		selected_compact = copy.deepcopy(selected["compact"])
		body_candidates = [copy.deepcopy(candidate["compact"]) for candidate in candidates]
		selected_body_index = int(selected_compact.get("body_index") or 0)

		selected_summary.setdefault("inference_metadata", {})
		selected_summary["inference_metadata"]["source"] = (
			"uploaded_step_selected_body"
			if selected.get("extraction_method") == "feature"
			else "uploaded_step_selected_body_bbox_fallback"
		)
		selected_summary["inference_metadata"]["selected_body_index"] = selected_body_index
		selected_summary["inference_metadata"]["body_count"] = len(body_candidates)

		warnings: List[str] = []
		if len(body_candidates) > 1:
			warnings.append(
				f"Detected {len(body_candidates)} solid bodies in the STEP file; selected body {selected_body_index} as the dominant turned candidate."
			)

		selected_summary["selected_body"] = selected_compact
		selected_summary["body_candidates"] = body_candidates
		selected_summary["warnings"] = warnings
		selected_summary["step_metadata"] = {
			"file_name": metadata.get("file_name"),
			"representation_context": metadata.get("representation_context"),
			"body_count": metadata.get("body_count"),
		}
		return selected_summary

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
				"source": "uploaded_step_body",
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
				"source": "uploaded_step_body_bbox_fallback",
				"input_unit": unit_label,
				"file_name": metadata.get("file_name"),
			},
		}

	def _score_body_candidate(
		self,
		summary: Dict[str, Any],
		extraction_method: str,
	) -> Tuple[float, List[str]]:
		totals = summary.get("totals") or {}
		feature_counts = summary.get("feature_counts") or {}
		segments = summary.get("segments") or []

		length_in = float(totals.get("total_length_in") or 0.0)
		max_od_in = float(totals.get("max_od_in") or 0.0)
		volume_in3 = float(totals.get("volume_in3") or 0.0)
		external_cylinders = int(feature_counts.get("external_cylinders") or 0)
		internal_bores = int(feature_counts.get("internal_bores") or 0)
		planar_faces = int(feature_counts.get("planar_faces") or 0)
		segment_count = len(segments)

		score = 0.0
		reasons: List[str] = []

		if extraction_method == "feature":
			score += 18.0
			reasons.append("Feature extraction succeeded for this body.")
		else:
			score -= 10.0
			reasons.append("Only bounding-box fallback was available for this body.")

		if length_in > 0:
			score += min(length_in, 40.0) * 0.6
			reasons.append(f"Positive axial span detected ({length_in:.3f} in).")
		else:
			score -= 40.0

		if max_od_in > 0:
			score += min(max_od_in, 20.0) * 1.5
			reasons.append(f"Positive OD detected ({max_od_in:.3f} in).")

		if volume_in3 > 0:
			score += min(math.log1p(volume_in3) * 6.0, 20.0)

		if external_cylinders > 0:
			score += min(external_cylinders, 6) * 5.0
			reasons.append(f"Contains {external_cylinders} external cylindrical feature(s).")
		else:
			score -= 12.0

		if internal_bores > 0:
			score += min(internal_bores, 4) * 3.0
			reasons.append(f"Contains {internal_bores} internal bore feature(s).")

		if segment_count > 0:
			score += min(segment_count, 12) * 3.0
			reasons.append(f"Built {segment_count} turned stack segment(s).")

		if planar_faces >= 2:
			score += 2.0

		return round(score, 4), reasons[:4]

	def _build_compact_candidate(
		self,
		body_index: int,
		summary: Dict[str, Any],
		extraction_method: str,
		score: float,
		reasons: List[str],
	) -> Dict[str, Any]:
		totals = summary.get("totals") or {}
		segments = summary.get("segments") or []
		ids = [float(seg.get("id_diameter") or 0.0) for seg in segments if float(seg.get("id_diameter") or 0.0) > 0]
		return {
			"body_index": body_index,
			"score": score,
			"extraction_method": extraction_method,
			"segment_count": len(segments),
			"selection_reasons": reasons,
			"dimensions": {
				"od_in": float(totals.get("max_od_in") or 0.0) or None,
				"id_in": min(ids) if ids else None,
				"max_id_in": max(ids) if ids else None,
				"length_in": float(totals.get("total_length_in") or 0.0) or None,
			},
			"z_range": summary.get("z_range"),
			"totals": {
				"volume_in3": totals.get("volume_in3"),
				"max_od_in": totals.get("max_od_in"),
				"max_id_in": totals.get("max_id_in"),
				"total_length_in": totals.get("total_length_in"),
			},
			"feature_counts": copy.deepcopy(summary.get("feature_counts") or {}),
			"inference_metadata": {
				"source": (summary.get("inference_metadata") or {}).get("source"),
				"overall_confidence": (summary.get("inference_metadata") or {}).get("overall_confidence"),
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

	def _build_llm_geometry_payload(self, part_summary: Dict[str, Any]) -> Dict[str, Any]:
		selected_body = copy.deepcopy(part_summary.get("selected_body") or {})
		selected_summary = {
			"z_range": part_summary.get("z_range"),
			"segments": part_summary.get("segments"),
			"totals": part_summary.get("totals"),
			"feature_counts": part_summary.get("feature_counts"),
			"inference_metadata": part_summary.get("inference_metadata"),
		}
		alternatives = [
			candidate
			for candidate in (part_summary.get("body_candidates") or [])
			if int(candidate.get("body_index") or -1) != int(selected_body.get("body_index") or -1)
		][:3]
		return {
			"selected_body": selected_body,
			"selected_body_summary": selected_summary,
			"alternative_bodies": alternatives,
			"warnings": part_summary.get("warnings") or [],
		}

	def _build_llm_candidate_payload(
		self,
		part_summary: Dict[str, Any],
		deterministic: Dict[str, Any],
	) -> Dict[str, Any]:
		body_candidates = copy.deepcopy(part_summary.get("body_candidates") or [])
		selected_body = copy.deepcopy(part_summary.get("selected_body") or {})
		feature_candidates = [
			candidate
			for candidate in body_candidates
			if str(candidate.get("extraction_method") or "") == "feature"
		]
		feature_candidates.sort(key=lambda candidate: float(candidate.get("score") or 0.0), reverse=True)
		best_feature = feature_candidates[0] if feature_candidates else None

		return {
			"deterministic_extracted": copy.deepcopy(deterministic.get("extracted") or {}),
			"selected_body_index": selected_body.get("body_index"),
			"selected_body_score": selected_body.get("score"),
			"selected_body_method": selected_body.get("extraction_method"),
			"best_feature_body_index": best_feature.get("body_index") if best_feature else selected_body.get("body_index"),
			"best_feature_body_score": best_feature.get("score") if best_feature else selected_body.get("score"),
			"body_dimension_candidates": [
				{
					"body_index": candidate.get("body_index"),
					"score": candidate.get("score"),
					"extraction_method": candidate.get("extraction_method"),
					"segment_count": candidate.get("segment_count"),
					"dimensions": copy.deepcopy(candidate.get("dimensions") or {}),
					"feature_counts": copy.deepcopy(candidate.get("feature_counts") or {}),
				}
				for candidate in body_candidates[:5]
			],
			"od_diameter_candidates": self._summarize_segment_diameters(part_summary, "od_diameter"),
			"id_diameter_candidates": self._summarize_segment_diameters(part_summary, "id_diameter"),
		}

	def _summarize_segment_diameters(
		self,
		part_summary: Dict[str, Any],
		field_name: str,
	) -> List[Dict[str, Any]]:
		groups: Dict[float, Dict[str, Any]] = {}
		for segment in (part_summary.get("segments") or []):
			try:
				diameter = float(segment.get(field_name) or 0.0)
			except Exception:
				continue
			if diameter <= 0:
				continue
			try:
				z_start = float(segment.get("z_start") or 0.0)
				z_end = float(segment.get("z_end") or 0.0)
			except Exception:
				z_start = 0.0
				z_end = 0.0
			span = abs(z_end - z_start)
			key = round(diameter, 4)
			entry = groups.setdefault(
				key,
				{
					"diameter_in": diameter,
					"segment_count": 0,
					"total_axial_span_in": 0.0,
					"max_single_span_in": 0.0,
				},
			)
			entry["segment_count"] += 1
			entry["total_axial_span_in"] += span
			entry["max_single_span_in"] = max(float(entry["max_single_span_in"]), span)

		return sorted(
			groups.values(),
			key=lambda entry: (
				float(entry.get("total_axial_span_in") or 0.0),
				float(entry.get("segment_count") or 0.0),
				-float(entry.get("diameter_in") or 0.0),
			),
			reverse=True,
		)[:8]

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
		selected_body = part_summary.get("selected_body") or {}
		selected_body_index = selected_body.get("body_index")
		candidate_count = len(part_summary.get("body_candidates") or [])
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
		if selected_body_index is not None:
			cross_checks.append(
				f"Selected STEP body {selected_body_index} as the dominant turned/revolved candidate for dimension extraction."
			)
			if selected_body.get("selection_reasons"):
				cross_checks.extend(str(reason) for reason in selected_body.get("selection_reasons")[:3])
		if candidate_count > 1:
			cross_checks.append(
				f"Compared {candidate_count} body candidates and used only the selected body dimensions for OD / ID / length."
			)
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
		assembly_like = body_count > 1 or "ASSEMBLY" in representation_context.upper() or candidate_count > 1
		selected_score = float(selected_body.get("score") or 0.0)
		close_alternatives = False
		body_candidates = part_summary.get("body_candidates") or []
		if len(body_candidates) > 1:
			sorted_scores = sorted((float(candidate.get("score") or 0.0) for candidate in body_candidates), reverse=True)
			if len(sorted_scores) > 1 and (sorted_scores[0] - sorted_scores[1]) < 8.0:
				close_alternatives = True
		recommendation = "ACCEPT" if not assembly_like and not close_alternatives and not code_issues and od_in and length_in and selected_score >= 20.0 else "REVIEW"

		return {
			"pdf_text_length": 0,
			"vision_mode": False,
			"analysis_source": "step",
			"extracted": extracted,
			"validation": {
				"recommendation": recommendation,
				"overall_confidence": 0.95 if recommendation == "ACCEPT" else (0.58 if close_alternatives else (0.6 if assembly_like else 0.72)),
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
		validation["fields"] = self._sync_validation_fields(extracted, fields, fallback)
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

	def _sync_validation_fields(
		self,
		extracted: Dict[str, Any],
		fields: Dict[str, Any],
		fallback: Dict[str, Any],
	) -> Dict[str, Any]:
		fallback_fields = ((fallback.get("validation") or {}).get("fields") or {})
		synced: Dict[str, Any] = dict(fields)
		for key in ("od_in", "max_od_in", "id_in", "max_id_in", "length_in", "max_length_in"):
			entry = dict(fallback_fields.get(key) or {})
			entry.update(synced.get(key) or {})
			entry["value"] = extracted.get(key)
			if entry.get("confidence") is None:
				entry["confidence"] = float((fallback_fields.get(key) or {}).get("confidence") or 0.0)
			entry["issue"] = entry.get("issue")
			synced[key] = entry
		return synced

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