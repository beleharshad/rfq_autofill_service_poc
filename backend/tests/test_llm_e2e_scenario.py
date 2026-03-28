"""
End-to-end scenario tests for the two-agent LLM PDF pipeline.

Tests exercise the *real* code path:
  OCR extraction → ExtractorAgent (LLM) → ValidatorAgent (LLM) → code-validate

Gemini HTTP calls are intercepted so tests run offline without burning quota.
The real engineering-drawing PDF (part 050CE0004 — ductile-iron bearing) is
used so the OCR and text-parsing logic runs against actual image content.
"""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
import requests as _requests

from app.services import llm_service, pdf_llm_pipeline

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BACKEND_DIR  = Path(__file__).parent.parent
TEST_JOB_DIR = BACKEND_DIR / "data" / "jobs" / "bff75f7b-d6f8-4786-8e42-2a38b7983628"
REAL_PDF     = TEST_JOB_DIR / "inputs" / "source.pdf"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeResponse:
    """Minimal stand-in for requests.Response that _post_json understands."""

    def __init__(self, body, status_code=200):
        self._body = body
        self.status_code = status_code
        self.text = str(body)

    def raise_for_status(self):
        if self.status_code >= 400:
            err = _requests.HTTPError(f"HTTP {self.status_code}")
            err.response = self       # required by llm_service 429 detection
            raise err

    def json(self):
        return self._body


def _gemini_ok(text: str) -> _FakeResponse:
    return _FakeResponse({"candidates": [{"content": {"parts": [{"text": text}]}}]})


def _gemini_429() -> _FakeResponse:
    return _FakeResponse({}, status_code=429)


# ---------------------------------------------------------------------------
# Fixture payloads that match the prompts in pdf_llm_pipeline.py
# ---------------------------------------------------------------------------

# Agent 1 — ExtractorAgent (must satisfy _code_validate rules)
EXTRACTOR_REPLY = json.dumps({
    "part_number": "050CE0004",
    "part_name": "Piston",
    "material": "80-55-06 Ductile Iron",
    "quantity": 1,
    "od_in": 1.240,
    "max_od_in": 1.380,
    "id_in": 0.430,
    "max_id_in": 0.440,
    "length_in": 0.630,
    "max_length_in": 0.980,
    "tolerance_od": "±0.001",
    "tolerance_id": "±0.002",
    "tolerance_length": "±0.003",
    "finish": "63 µin Ra",
    "revision": "E4",
})

# Agent 2 — ValidatorAgent (matches _VALIDATOR_SYSTEM schema)
VALIDATOR_REPLY = json.dumps({
    "fields": {
        "od_in":     {"value": 1.240, "confidence": 0.92, "issue": None},
        "max_od_in": {"value": 1.380, "confidence": 0.90, "issue": None},
        "id_in":     {"value": 0.430, "confidence": 0.91, "issue": None},
        "material":  {"value": "80-55-06 Ductile Iron", "confidence": 0.95, "issue": None},
    },
    "cross_checks": [],
    "overall_confidence": 0.91,
    "recommendation": "ACCEPT",
})

ENV_WITH_KEY = {"GOOGLE_API_KEY": "test-key-e2e"}

# Cache the OCR result at module import time so it only runs once per session.
# Tests that call run_pipeline patch _extract_pdf_text to return this cached text.
_CACHED_OCR_TEXT: str | None = None


def _get_ocr_text() -> str:
    """Return OCR text from the real PDF, cached after the first call."""
    global _CACHED_OCR_TEXT
    if _CACHED_OCR_TEXT is None and REAL_PDF.exists():
        _CACHED_OCR_TEXT = pdf_llm_pipeline._extract_pdf_text(REAL_PDF)
    return _CACHED_OCR_TEXT or ""


def _make_post_mock(extractor_text=EXTRACTOR_REPLY, validator_text=VALIDATOR_REPLY):
    """Return (side_effect_fn, call_log) where call_log tracks every URL hit."""
    call_log: list[str] = []

    def _side_effect(url, *args, **kwargs):
        call_log.append(url)
        # First generateContent call → ExtractorAgent; second → ValidatorAgent
        count = sum(1 for u in call_log if "generateContent" in u)
        return _gemini_ok(extractor_text if count == 1 else validator_text)

    return _side_effect, call_log


# ===========================================================================
# Tests
# ===========================================================================

@pytest.mark.skipif(not REAL_PDF.exists(), reason="test-fixture PDF not present")
class TestLLME2EScenario:
    """Full pipeline exercised against the real 050CE0004 bearing drawing."""

    @pytest.fixture(autouse=True)
    def _patch_ocr(self, monkeypatch):
        """Replace _extract_pdf_text with the cached OCR result for speed.

        The first call (from test_ocr_* tests) still runs real OCR; once cached
        all subsequent pipeline tests reuse the same text without re-running OCR.
        """
        cached = _get_ocr_text()
        monkeypatch.setattr(
            pdf_llm_pipeline, "_extract_pdf_text", lambda *_: cached
        )

    # ------------------------------------------------------------------
    # 1. OCR text extraction (real OCR via _get_ocr_text)
    # ------------------------------------------------------------------

    def test_ocr_extracts_enough_text(self):
        """Vector-only PDF must yield ≥100 chars via pytesseract fallback."""
        text = _get_ocr_text()
        assert len(text) >= 100, f"Got only {len(text)} chars from OCR"

    def test_ocr_text_contains_part_number(self):
        text = _get_ocr_text().upper()
        assert "050CE0004" in text, "Part number not found in OCR text"

    def test_ocr_text_contains_numeric_content(self):
        text = _get_ocr_text()
        assert any(c.isdigit() for c in text), "No digits found in OCR output"

    # ------------------------------------------------------------------
    # 2. Pipeline structure
    # ------------------------------------------------------------------

    def test_pipeline_returns_required_keys(self):
        mock_fn, _ = _make_post_mock()
        with patch.dict(os.environ, ENV_WITH_KEY):
            with patch("app.services.llm_service.requests.post", side_effect=mock_fn):
                result = pdf_llm_pipeline.run_pipeline(REAL_PDF)
        assert {"pdf_text_length", "vision_mode", "extracted", "validation", "code_issues", "valid"} \
               == set(result.keys())

    def test_pipeline_makes_exactly_two_gemini_calls(self):
        """ExtractorAgent + ValidatorAgent → 2 generateContent HTTP calls."""
        mock_fn, call_log = _make_post_mock()
        with patch.dict(os.environ, ENV_WITH_KEY):
            with patch("app.services.llm_service.requests.post", side_effect=mock_fn):
                pdf_llm_pipeline.run_pipeline(REAL_PDF)
        assert len(call_log) == 2, \
            f"Expected 2 Gemini calls, got {len(call_log)}: {call_log}"

    def test_pdf_text_length_recorded(self):
        mock_fn, _ = _make_post_mock()
        with patch.dict(os.environ, ENV_WITH_KEY):
            with patch("app.services.llm_service.requests.post", side_effect=mock_fn):
                result = pdf_llm_pipeline.run_pipeline(REAL_PDF)
        assert result["pdf_text_length"] > 0

    # ------------------------------------------------------------------
    # 3. Extractor (Agent 1) output
    # ------------------------------------------------------------------

    def test_extracted_od_is_positive(self):
        mock_fn, _ = _make_post_mock()
        with patch.dict(os.environ, ENV_WITH_KEY):
            with patch("app.services.llm_service.requests.post", side_effect=mock_fn):
                result = pdf_llm_pipeline.run_pipeline(REAL_PDF)
        od = result["extracted"].get("od_in")
        assert od is not None and od > 0

    def test_extracted_max_od_gte_od(self):
        mock_fn, _ = _make_post_mock()
        with patch.dict(os.environ, ENV_WITH_KEY):
            with patch("app.services.llm_service.requests.post", side_effect=mock_fn):
                result = pdf_llm_pipeline.run_pipeline(REAL_PDF)
        s = result["extracted"]
        assert s["max_od_in"] >= s["od_in"]

    def test_extracted_id_less_than_od(self):
        mock_fn, _ = _make_post_mock()
        with patch.dict(os.environ, ENV_WITH_KEY):
            with patch("app.services.llm_service.requests.post", side_effect=mock_fn):
                result = pdf_llm_pipeline.run_pipeline(REAL_PDF)
        s = result["extracted"]
        assert s["id_in"] < s["od_in"], \
            f"id_in {s['id_in']} should be < od_in {s['od_in']}"

    def test_extracted_material_is_ductile_iron(self):
        mock_fn, _ = _make_post_mock()
        with patch.dict(os.environ, ENV_WITH_KEY):
            with patch("app.services.llm_service.requests.post", side_effect=mock_fn):
                result = pdf_llm_pipeline.run_pipeline(REAL_PDF)
        mat = (result["extracted"].get("material") or "").lower()
        assert "iron" in mat or "ductile" in mat, f"Unexpected material: {mat!r}"

    # ------------------------------------------------------------------
    # 4. Validator (Agent 2) output
    # ------------------------------------------------------------------

    def test_validator_recommends_accept(self):
        mock_fn, _ = _make_post_mock()
        with patch.dict(os.environ, ENV_WITH_KEY):
            with patch("app.services.llm_service.requests.post", side_effect=mock_fn):
                result = pdf_llm_pipeline.run_pipeline(REAL_PDF)
        assert result["validation"]["recommendation"] == "ACCEPT"

    def test_validator_confidence_above_threshold(self):
        mock_fn, _ = _make_post_mock()
        with patch.dict(os.environ, ENV_WITH_KEY):
            with patch("app.services.llm_service.requests.post", side_effect=mock_fn):
                result = pdf_llm_pipeline.run_pipeline(REAL_PDF)
        score = result["validation"]["overall_confidence"]
        assert score >= 0.7, f"confidence {score} < 0.7"

    def test_validator_no_cross_check_issues(self):
        mock_fn, _ = _make_post_mock()
        with patch.dict(os.environ, ENV_WITH_KEY):
            with patch("app.services.llm_service.requests.post", side_effect=mock_fn):
                result = pdf_llm_pipeline.run_pipeline(REAL_PDF)
        assert result["validation"]["cross_checks"] == []

    # ------------------------------------------------------------------
    # 5. Code-validate rules
    # ------------------------------------------------------------------

    def test_no_code_issues_for_valid_reply(self):
        mock_fn, _ = _make_post_mock()
        with patch.dict(os.environ, ENV_WITH_KEY):
            with patch("app.services.llm_service.requests.post", side_effect=mock_fn):
                result = pdf_llm_pipeline.run_pipeline(REAL_PDF)
        assert result["code_issues"] == [], f"Unexpected issues: {result['code_issues']}"

    def test_pipeline_valid_flag_true(self):
        mock_fn, _ = _make_post_mock()
        with patch.dict(os.environ, ENV_WITH_KEY):
            with patch("app.services.llm_service.requests.post", side_effect=mock_fn):
                result = pdf_llm_pipeline.run_pipeline(REAL_PDF)
        assert result["valid"] is True

    def test_code_validate_detects_od_less_than_id(self):
        bad = dict(json.loads(EXTRACTOR_REPLY), od_in=0.3)   # od < id (0.430)
        issues = pdf_llm_pipeline._code_validate(bad)
        assert any("od_in" in i and "id_in" in i for i in issues), \
            f"Expected od<=id issue, got: {issues}"

    def test_code_validate_detects_max_od_less_than_od(self):
        bad = dict(json.loads(EXTRACTOR_REPLY))
        bad["max_od_in"] = bad["od_in"] - 0.1
        issues = pdf_llm_pipeline._code_validate(bad)
        assert any("max_od_in" in i for i in issues), issues

    def test_code_validate_detects_negative_length(self):
        bad = dict(json.loads(EXTRACTOR_REPLY), length_in=-0.1)
        issues = pdf_llm_pipeline._code_validate(bad)
        assert any("length_in" in i for i in issues), issues

    # ------------------------------------------------------------------
    # 6. 429 rate-limit fast-exit (no second-URL attempt)
    # ------------------------------------------------------------------

    def test_429_raises_immediately_skips_v1_url(self):
        """A 429 from v1beta must surface immediately — same key, same quota."""
        call_log: list[str] = []

        def _always_429(url, *args, **kwargs):
            call_log.append(url)
            return _gemini_429()

        with patch.dict(os.environ, ENV_WITH_KEY):
            with patch("app.services.llm_service.time.sleep"):    # skip actual waits
                with patch("app.services.llm_service.requests.post", side_effect=_always_429):
                    with pytest.raises(RuntimeError, match="rate limit"):
                        llm_service.generate_text("test prompt")

        # _post_json: attempt-0 → sleep → attempt-1 → raise  (2 HTTP calls)
        # generate_text catches 429 immediately → does NOT try the v1 URL
        assert len(call_log) == 2, \
            f"Expected 2 HTTP calls (1 attempt + 1 retry), got {len(call_log)}: {call_log}"
        assert all("v1beta" in u for u in call_log), \
            f"Should only hit v1beta, not v1: {call_log}"

    # ------------------------------------------------------------------
    # 7. REVIEW verdict makes pipeline mark valid=False
    # ------------------------------------------------------------------

    def test_review_verdict_sets_valid_false(self):
        review_reply = json.dumps({
            "fields": {},
            "cross_checks": ["od_in seems low"],
            "overall_confidence": 0.5,
            "recommendation": "REVIEW",
        })
        mock_fn, _ = _make_post_mock(validator_text=review_reply)
        with patch.dict(os.environ, ENV_WITH_KEY):
            with patch("app.services.llm_service.requests.post", side_effect=mock_fn):
                result = pdf_llm_pipeline.run_pipeline(REAL_PDF)
        assert result["valid"] is False
        assert result["validation"]["recommendation"] == "REVIEW"
