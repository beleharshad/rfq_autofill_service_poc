"""Unit tests for dimension_intent_labeler module."""

import pytest

from app.services.dimension_intent_labeler import (
    _extract_ocr_candidates,
    _classify_intent,
    _classify_unknown_by_geometry,
    _norm,
    _pick_best,
    _build_geometry_pool,
    _match_od_to_geometry,
    _validate_length,
    label_and_validate_dimensions,
)


# ── Tolerance range parsing ───────────────────────────────────────────────

class TestToleranceParsing:
    def test_range_parsed_and_max_used_for_od(self):
        ps = _make_ps(raw_dims=[
            {"text": "Ø 0.723-0.727", "value_in": 0.725, "confidence": 0.9},
        ])
        cands = _extract_ocr_candidates(ps)
        assert len(cands) == 1
        assert cands[0]["is_tolerance"] is True
        assert cands[0]["tol_hi"] == 0.727
        assert cands[0]["tol_lo"] == 0.723

        best = _pick_best(cands, "OD", use_max=True)
        assert best is not None
        assert best["value_in"] == 0.727

    def test_range_parsed_and_avg_used_for_len(self):
        ps = _make_ps(raw_dims=[
            {"text": "LENGTH 1.20-1.24", "value_in": 1.22, "confidence": 0.9},
        ])
        cands = _extract_ocr_candidates(ps)
        best = _pick_best(cands, "LEN", use_max=False)
        assert best is not None
        assert abs(best["value_in"] - 1.22) < 0.001


# ── Bracketed metric / skip filtering ──────────────────────────────────────

class TestBracketedMetricSkip:
    def test_bracketed_metric_skipped(self):
        ps = _make_ps(raw_dims=[
            {"text": "Ø 0.500 [12.70]", "value_in": 0.5, "confidence": 0.9},
            {"text": "Ø 1.000", "value_in": 1.0, "confidence": 0.9},
        ])
        cands = _extract_ocr_candidates(ps)
        assert len(cands) == 1
        assert cands[0]["value_in"] == 1.0

    def test_scale_line_skipped(self):
        ps = _make_ps(raw_dims=[
            {"text": "SCALE 2:1", "value_in": 2.0, "confidence": 0.9},
            {"text": "Ø 1.500", "value_in": 1.5, "confidence": 0.9},
        ])
        cands = _extract_ocr_candidates(ps)
        assert len(cands) == 1
        assert cands[0]["value_in"] == 1.5

    def test_thread_line_skipped(self):
        ps = _make_ps(raw_dims=[
            {"text": "1/4-20 UNC THREAD", "value_in": 0.25, "confidence": 0.8},
        ])
        cands = _extract_ocr_candidates(ps)
        assert len(cands) == 0

    def test_angle_skipped(self):
        ps = _make_ps(raw_dims=[
            {"text": "45°", "value_in": 45.0, "confidence": 0.7},
        ])
        cands = _extract_ocr_candidates(ps)
        assert len(cands) == 0

    def test_surface_finish_annotation_skipped(self):
        ps = _make_ps(raw_dims=[
            {"text": "\\/@.34 X 82", "value_in": 0.34, "confidence": 0.9},
            {"text": "Ø 1.495", "value_in": 1.495, "confidence": 0.9},
        ])
        cands = _extract_ocr_candidates(ps)
        assert len(cands) == 1
        assert cands[0]["value_in"] == 1.495

    def test_ra_roughness_skipped(self):
        ps = _make_ps(raw_dims=[
            {"text": "Ra 0.8", "value_in": 0.8, "confidence": 0.8},
        ])
        cands = _extract_ocr_candidates(ps)
        assert len(cands) == 0

    def test_rms_roughness_skipped(self):
        ps = _make_ps(raw_dims=[
            {"text": "32 RMS", "value_in": 32.0, "confidence": 0.7},
        ])
        cands = _extract_ocr_candidates(ps)
        assert len(cands) == 0


# ── Geometry-guided UNKNOWN reclassification ──────────────────────────────

class TestGeometryGuidedClassification:
    def test_unknown_reclassified_as_od_when_matches_segment(self):
        """OCR value 1.73 with garbled text should be classified as OD
        when it matches a geometry segment OD within 15%."""
        ps = _make_ps(
            raw_dims=[
                {"text": "1.73 garbled", "value_in": 1.73, "confidence": 0.8},
            ],
            segments=[
                {"z_start": 0.0, "z_end": 1.0, "od_diameter": 1.80, "id_diameter": 0.0, "confidence": 0.9},
            ],
            z_range=[0.0, 1.0],
        )
        cands = _extract_ocr_candidates(ps)
        assert cands[0]["intent"] == "UNKNOWN"
        _classify_unknown_by_geometry(cands, ps)
        assert cands[0]["intent"] == "OD"

    def test_unknown_reclassified_as_id_when_matches_bore(self):
        ps = _make_ps(
            raw_dims=[
                {"text": "0.82 noise", "value_in": 0.82, "confidence": 0.8},
            ],
            segments=[
                {"z_start": 0.0, "z_end": 1.0, "od_diameter": 2.0, "id_diameter": 0.80, "confidence": 0.9},
            ],
            z_range=[0.0, 1.0],
        )
        cands = _extract_ocr_candidates(ps)
        _classify_unknown_by_geometry(cands, ps)
        assert cands[0]["intent"] == "ID"

    def test_unknown_reclassified_as_len_when_exceeds_max_od(self):
        ps = _make_ps(
            raw_dims=[
                {"text": "3.38 noise", "value_in": 3.38, "confidence": 0.8},
            ],
            segments=[
                {"z_start": 0.0, "z_end": 1.0, "od_diameter": 1.92, "id_diameter": 0.0, "confidence": 0.9},
            ],
            z_range=[0.0, 1.0],
        )
        cands = _extract_ocr_candidates(ps)
        _classify_unknown_by_geometry(cands, ps)
        assert cands[0]["intent"] == "LEN"

    def test_token_classified_not_overridden(self):
        """Values with clear tokens should NOT be reclassified."""
        ps = _make_ps(
            raw_dims=[
                {"text": "Ø 1.50", "value_in": 1.50, "confidence": 0.9},
            ],
            segments=[
                {"z_start": 0.0, "z_end": 1.0, "od_diameter": 2.0, "id_diameter": 0.0, "confidence": 0.9},
            ],
            z_range=[0.0, 1.0],
        )
        cands = _extract_ocr_candidates(ps)
        assert cands[0]["intent"] == "OD"  # from Ø token
        _classify_unknown_by_geometry(cands, ps)
        assert cands[0]["intent"] == "OD"  # unchanged

    def test_optimal_scoring_best_match_wins(self):
        """When multiple UNKNOWN values could match geometry ODs, the
        tightest match should win the OD slot regardless of list order."""
        ps = _make_ps(
            raw_dims=[
                {"text": "1.495 noise", "value_in": 1.495, "confidence": 0.6},
                {"text": "1.730 noise", "value_in": 1.730, "confidence": 0.6},
            ],
            segments=[
                {"z_start": 0.0, "z_end": 0.5, "od_diameter": 1.5541, "id_diameter": 0.0, "confidence": 0.9},
                {"z_start": 0.5, "z_end": 1.0, "od_diameter": 1.7304, "id_diameter": 0.0, "confidence": 0.9},
            ],
            z_range=[0.0, 1.0],
        )
        cands = _extract_ocr_candidates(ps)
        _classify_unknown_by_geometry(cands, ps)
        # 1.730 matches seg 1 (od=1.7304) at 0.02% error — much tighter than
        # 1.495 matching seg 0 (od=1.5541) at 3.8%
        od_cand = [c for c in cands if c["intent"] == "OD"]
        assert len(od_cand) == 1
        assert od_cand[0]["value_in"] == 1.730

    def test_050da0028_scenario_od_found(self):
        """Simulates the 050da0028 problem: garbled OCR with correct values
        that should be classified via geometry proximity."""
        ps = _make_ps(
            raw_dims=[
                {"text": "1.73 garbled", "value_in": 1.73, "confidence": 0.8},
                {"text": "0.80 garbled", "value_in": 0.80, "confidence": 0.8},
                {"text": "3.38 garbled", "value_in": 3.38, "confidence": 0.7},
            ],
            segments=[
                {"z_start": 0.0, "z_end": 0.5, "od_diameter": 1.80, "id_diameter": 0.78, "confidence": 0.9},
                {"z_start": 0.5, "z_end": 1.0, "od_diameter": 1.92, "id_diameter": 0.0, "confidence": 0.9},
            ],
            z_range=[0.0, 1.0],
        )
        result = label_and_validate_dimensions(ps)
        assert result["status"] == "OK"
        assert result["labeled"]["finish_od_in"]["value"] == 1.73
        assert result["labeled"]["finish_id_in"]["value"] == 0.80

    def test_050da0028_scenario_len_accepted_with_close_geometry(self):
        """When geometry total length is close to OCR length, all three
        dimensions should be accepted."""
        ps = _make_ps(
            raw_dims=[
                {"text": "1.73 garbled", "value_in": 1.73, "confidence": 0.8},
                {"text": "0.80 garbled", "value_in": 0.80, "confidence": 0.8},
                {"text": "3.38 garbled", "value_in": 3.38, "confidence": 0.8},
            ],
            segments=[
                {"z_start": 0.0, "z_end": 1.5, "od_diameter": 1.80, "id_diameter": 0.78, "confidence": 0.9},
                {"z_start": 1.5, "z_end": 3.20, "od_diameter": 1.92, "id_diameter": 0.0, "confidence": 0.9},
            ],
            z_range=[0.0, 3.20],
        )
        result = label_and_validate_dimensions(ps)
        assert result["status"] == "OK"
        assert result["labeled"]["finish_od_in"]["value"] == 1.73
        assert result["labeled"]["finish_id_in"]["value"] == 0.80
        assert result["labeled"]["finish_len_in"]["value"] == 3.38


# ── OD matching accept / reject ───────────────────────────────────────────

class TestODMatching:
    def test_direct_match_within_10pct(self):
        pool = [
            {"idx": 0, "od_in": 1.0, "seg_len": 0.5, "confidence": 0.9, "z_start": 0, "z_end": 0.5},
            {"idx": 1, "od_in": 0.5, "seg_len": 0.5, "confidence": 0.9, "z_start": 0.5, "z_end": 1.0},
        ]
        seg, xy, reasons = _match_od_to_geometry(1.05, pool, [1.05])
        assert seg is not None
        assert seg["idx"] == 0
        assert xy is None
        assert len(reasons) == 0

    def test_mismatch_beyond_10pct_no_scale_support_rejected(self):
        pool = [
            {"idx": 0, "od_in": 1.0, "seg_len": 0.05, "confidence": 0.5, "z_start": 0, "z_end": 0.05},
        ]
        seg, xy, reasons = _match_od_to_geometry(2.0, pool, [2.0])
        assert seg is None
        assert "OCR_GEOM_OD_MISMATCH" in reasons

    def test_mismatch_accepted_via_xy_scale_when_confident(self):
        pool = [
            {"idx": 0, "od_in": 1.0, "seg_len": 0.5, "confidence": 0.9, "z_start": 0, "z_end": 0.5},
        ]
        seg, xy, reasons = _match_od_to_geometry(2.0, pool, [2.0])
        assert seg is not None
        assert xy is not None
        assert abs(xy - 2.0) < 0.001

    def test_empty_pool_gives_insufficient_context(self):
        seg, xy, reasons = _match_od_to_geometry(1.0, [], [1.0])
        assert seg is None
        assert "OCR_INSUFFICIENT_CONTEXT" in reasons


# ── Fallback reasons included ─────────────────────────────────────────────

class TestFallbackReasons:
    def test_no_ocr_candidates_gives_no_ocr(self):
        ps = _make_ps(raw_dims=[])
        result = label_and_validate_dimensions(ps)
        assert result["status"] == "NO_OCR"

    def test_no_od_candidate_gives_fallback(self):
        ps = _make_ps(raw_dims=[
            {"text": "LENGTH 2.0", "value_in": 2.0, "confidence": 0.9},
        ])
        result = label_and_validate_dimensions(ps)
        assert result["status"] == "FALLBACK_GEOMETRY"
        assert "OCR_INSUFFICIENT_CONTEXT" in result["validation"]["reasons"]

    def test_od_mismatch_gives_fallback_with_reason(self):
        ps = _make_ps(
            raw_dims=[{"text": "Ø 5.0", "value_in": 5.0, "confidence": 0.9}],
            segments=[
                {"z_start": 0.0, "z_end": 0.08, "od_diameter": 1.0, "id_diameter": 0.0, "confidence": 0.7},
            ],
            z_range=[0.0, 0.08],
        )
        result = label_and_validate_dimensions(ps)
        assert result["status"] == "FALLBACK_GEOMETRY"
        assert "OCR_GEOM_OD_MISMATCH" in result["validation"]["reasons"]

    def test_ok_status_when_od_matches_geometry(self):
        ps = _make_ps(
            raw_dims=[{"text": "Ø 1.000", "value_in": 1.0, "confidence": 0.9}],
            segments=[
                {"z_start": 0.0, "z_end": 2.0, "od_diameter": 1.0, "id_diameter": 0.0, "confidence": 0.9},
            ],
            z_range=[0.0, 2.0],
        )
        result = label_and_validate_dimensions(ps)
        assert result["status"] == "OK"
        assert result["labeled"]["finish_od_in"]["value"] == 1.0

    def test_len_implausible_reason(self):
        ps = _make_ps(
            raw_dims=[
                {"text": "Ø 1.000", "value_in": 1.0, "confidence": 0.9},
                {"text": "LENGTH 10.0", "value_in": 10.0, "confidence": 0.3},
            ],
            segments=[
                {"z_start": 0.0, "z_end": 2.0, "od_diameter": 1.0, "id_diameter": 0.0, "confidence": 0.9},
            ],
            z_range=[0.0, 2.0],
        )
        result = label_and_validate_dimensions(ps)
        assert result["status"] == "OK"
        assert "OCR_LEN_IMPLAUSIBLE" in result["validation"]["reasons"]
        assert result["labeled"]["finish_len_in"] is None

    def test_id_tiny_feature_rejected(self):
        ps = _make_ps(
            raw_dims=[
                {"text": "Ø 2.000", "value_in": 2.0, "confidence": 0.9},
                {"text": "ID 0.05", "value_in": 0.05, "confidence": 0.8},
            ],
            segments=[
                {"z_start": 0.0, "z_end": 1.0, "od_diameter": 2.0, "id_diameter": 0.0, "confidence": 0.9},
            ],
            z_range=[0.0, 1.0],
        )
        result = label_and_validate_dimensions(ps)
        assert result["status"] == "OK"
        assert "OCR_ID_TINY_FEATURE" in result["validation"]["reasons"]
        assert result["labeled"]["finish_id_in"] is None


# ── Length validation ──────────────────────────────────────────────────────

class TestLengthValidation:
    def test_within_15pct_accepted(self):
        val, z_scale, reasons = _validate_length(2.1, 2.0, None)
        assert val == 2.1
        assert z_scale is None
        assert len(reasons) == 0

    def test_beyond_15pct_low_conf_rejected(self):
        cand = {"confidence": 0.3, "intent": "LEN"}
        val, z_scale, reasons = _validate_length(5.0, 2.0, cand)
        assert val is None
        assert "OCR_LEN_IMPLAUSIBLE" in reasons

    def test_beyond_15pct_high_conf_len_token_scaled(self):
        cand = {"confidence": 0.9, "intent": "LEN"}
        val, z_scale, reasons = _validate_length(5.0, 2.0, cand)
        assert val == 5.0
        assert z_scale is not None
        assert abs(z_scale - 2.5) < 0.001


# ── Integration: full pipeline returning debug fields ─────────────────────

class TestIntegration:
    def test_autofill_populates_intent_labeler_debug(self):
        from app.services.rfq_autofill_service import RFQAutofillService

        svc = RFQAutofillService()
        resp = svc.autofill(
            part_no="TEST001",
            part_summary_dict={
                "units": {"length": "in"},
                "z_range": [0.0, 1.5],
                "segments": [
                    {"z_start": 0.0, "z_end": 1.5, "od_diameter": 1.0, "id_diameter": 0.0, "confidence": 0.9},
                ],
                "scale_report": {"method": "anchor_dimension", "validation_passed": True},
                "inference_metadata": {
                    "overall_confidence": 0.9,
                    "raw_dimensions": [
                        {"text": "Ø 1.000", "value_in": 1.0, "confidence": 0.9},
                    ],
                },
            },
            tolerances={"rm_od_allowance_in": 0.1, "rm_len_allowance_in": 0.35},
            step_metrics=None,
            mode="GEOMETRY",
        )
        assert resp.debug.intent_labeler_used is True
        assert resp.debug.intent_labeler_status in ("OK", "FALLBACK_GEOMETRY", "NO_OCR")


# ── helpers ───────────────────────────────────────────────────────────────

def _make_ps(
    raw_dims=None,
    segments=None,
    z_range=None,
    totals=None,
):
    ps = {
        "units": {"length": "in"},
        "inference_metadata": {},
    }
    if raw_dims is not None:
        ps["inference_metadata"]["raw_dimensions"] = raw_dims
    if segments is not None:
        ps["segments"] = segments
    if z_range is not None:
        ps["z_range"] = z_range
    if totals is not None:
        ps["totals"] = totals
    return ps
