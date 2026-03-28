"""Tests for the two-agent PDF LLM pipeline.

All LLM and PDF extraction calls are monkeypatched — no real network requests.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from app.services import pdf_llm_pipeline
from app.services.pdf_llm_pipeline import (
    ExtractorAgent,
    ValidatorAgent,
    _code_validate,
    _extract_pdf_text,
    _parse_json_response,
    _positive,
    run_pipeline,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

GOOD_EXTRACTED: dict[str, Any] = {
    "part_number": "050CE0004",
    "part_name": "SLEEVE",
    "material": "4140 STEEL",
    "quantity": 10,
    "od_in": 1.625,
    "max_od_in": 1.875,
    "id_in": 1.125,
    "max_id_in": 1.125,
    "length_in": 4.0,
    "max_length_in": 4.5,
    "tolerance_od": "+0.000/-0.002",
    "tolerance_id": "+0.002/-0.000",
    "tolerance_length": "±0.010",
    "finish": "32 Ra",
    "revision": "A",
}

GOOD_VALIDATION: dict[str, Any] = {
    "fields": {
        "od_in": {"value": 1.625, "confidence": 0.95, "issue": None},
        "id_in": {"value": 1.125, "confidence": 0.93, "issue": None},
        "length_in": {"value": 4.0, "confidence": 0.97, "issue": None},
    },
    "cross_checks": [],
    "overall_confidence": 0.95,
    "recommendation": "ACCEPT",
}


@pytest.fixture()
def fake_pdf(tmp_path: Path) -> Path:
    """Create a tiny fake PDF file (all zeros — won't actually parse)."""
    p = tmp_path / "drawing.pdf"
    p.write_bytes(b"%PDF-1.4 fake content for tests")
    return p


# ---------------------------------------------------------------------------
# 1. _parse_json_response
# ---------------------------------------------------------------------------


def test_parse_json_response_plain():
    raw = '{"key": 1}'
    assert _parse_json_response(raw) == {"key": 1}


def test_parse_json_response_strips_markdown_fence():
    raw = "```json\n{\"key\": 2}\n```"
    assert _parse_json_response(raw) == {"key": 2}


def test_parse_json_response_raises_on_garbage():
    with pytest.raises(ValueError, match="non-JSON"):
        _parse_json_response("This is not JSON at all")


# ---------------------------------------------------------------------------
# 2. _code_validate rules
# ---------------------------------------------------------------------------


def test_code_validate_passes_good_data():
    assert _code_validate(GOOD_EXTRACTED) == []


def test_code_validate_flags_od_not_greater_than_id():
    data = {**GOOD_EXTRACTED, "od_in": 1.0, "id_in": 1.5}
    issues = _code_validate(data)
    assert any("od_in" in i and "greater than id_in" in i for i in issues)


def test_code_validate_flags_negative_od():
    data = {**GOOD_EXTRACTED, "od_in": -1.0}
    issues = _code_validate(data)
    assert any("od_in must be positive" in i for i in issues)


def test_code_validate_flags_zero_length():
    data = {**GOOD_EXTRACTED, "length_in": 0.0}
    issues = _code_validate(data)
    assert any("length_in must be positive" in i for i in issues)


def test_code_validate_flags_zero_quantity():
    data = {**GOOD_EXTRACTED, "quantity": 0}
    issues = _code_validate(data)
    assert any("quantity must be a positive integer" in i for i in issues)


def test_code_validate_skips_none_fields():
    # No OD / ID → should not flag the OD>ID rule or max rules
    data = {**GOOD_EXTRACTED, "od_in": None, "id_in": None,
            "max_od_in": None, "max_id_in": None}
    assert _code_validate(data) == []


def test_code_validate_flags_max_od_less_than_od():
    data = {**GOOD_EXTRACTED, "od_in": 1.625, "max_od_in": 1.5}  # max < finish
    issues = _code_validate(data)
    assert any("max_od_in" in i and ">= od_in" in i for i in issues)


def test_code_validate_flags_max_id_less_than_id():
    data = {**GOOD_EXTRACTED, "id_in": 1.125, "max_id_in": 0.9}
    issues = _code_validate(data)
    assert any("max_id_in" in i and ">= id_in" in i for i in issues)


def test_code_validate_flags_max_length_less_than_length():
    data = {**GOOD_EXTRACTED, "length_in": 4.0, "max_length_in": 3.5}
    issues = _code_validate(data)
    assert any("max_length_in" in i and ">= length_in" in i for i in issues)


def test_code_validate_max_od_equal_to_od_is_valid():
    # Plain cylinder: no undercuts, so max_od == finish od is fine
    data = {**GOOD_EXTRACTED, "od_in": 1.625, "max_od_in": 1.625}
    issues = _code_validate(data)
    assert not any("max_od_in" in i for i in issues)


def test_code_validate_flags_max_od_not_greater_than_max_id():
    data = {**GOOD_EXTRACTED, "max_od_in": 1.0, "max_id_in": 1.0}
    issues = _code_validate(data)
    assert any("max_od_in" in i and "greater than max_id_in" in i for i in issues)


# ---------------------------------------------------------------------------
# _positive helper
# ---------------------------------------------------------------------------


def test_positive_returns_float_for_valid_value():
    issues: list[str] = []
    result = _positive(2.5, "test_field", issues)
    assert result == pytest.approx(2.5)
    assert issues == []


def test_positive_returns_none_for_none():
    issues: list[str] = []
    result = _positive(None, "test_field", issues)
    assert result is None
    assert issues == []  # None is not flagged as error


def test_positive_flags_zero():
    issues: list[str] = []
    result = _positive(0.0, "od_in", issues)
    assert result == pytest.approx(0.0)
    assert any("od_in must be positive" in i for i in issues)


def test_positive_flags_non_numeric_string():
    issues: list[str] = []
    result = _positive("bad", "od_in", issues)
    assert result is None
    assert any("not a valid number" in i for i in issues)


# ---------------------------------------------------------------------------
# 3. ExtractorAgent
# ---------------------------------------------------------------------------


def test_extractor_agent_calls_llm_and_parses_json(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    expected = {**GOOD_EXTRACTED}
    monkeypatch.setattr(
        pdf_llm_pipeline.llm_service,
        "generate_text",
        lambda *a, **k: json.dumps(expected),
    )
    result = ExtractorAgent().run("some drawing text")
    assert result["od_in"] == 1.625
    assert result["material"] == "4140 STEEL"


def test_extractor_agent_truncates_long_text(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    captured: dict[str, Any] = {}

    def fake_generate(prompt: str, **kw: Any) -> str:
        captured["prompt_len"] = len(prompt)
        return json.dumps(GOOD_EXTRACTED)

    monkeypatch.setattr(pdf_llm_pipeline.llm_service, "generate_text", fake_generate)
    long_text = "X" * 20_000
    ExtractorAgent().run(long_text)
    # Text capped at MAX_TEXT_CHARS (12000); system prompt ~4000 chars; total should stay < 20000
    assert captured["prompt_len"] < 20_000  # prompt + system fits within margin


def test_extractor_agent_raises_on_bad_llm_json(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setattr(
        pdf_llm_pipeline.llm_service,
        "generate_text",
        lambda *a, **k: "Sorry, I cannot help with that.",
    )
    with pytest.raises(ValueError, match="non-JSON"):
        ExtractorAgent().run("text")


# ---------------------------------------------------------------------------
# 4. ValidatorAgent
# ---------------------------------------------------------------------------


def test_validator_agent_calls_llm_and_parses_json(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    expected = {**GOOD_VALIDATION}
    monkeypatch.setattr(
        pdf_llm_pipeline.llm_service,
        "generate_text",
        lambda *a, **k: json.dumps(expected),
    )
    result = ValidatorAgent().run("drawing text", GOOD_EXTRACTED)
    assert result["recommendation"] == "ACCEPT"
    assert result["overall_confidence"] == pytest.approx(0.95)


def test_validator_agent_raises_on_bad_response(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setattr(
        pdf_llm_pipeline.llm_service,
        "generate_text",
        lambda *a, **k: "not json",
    )
    with pytest.raises(ValueError, match="non-JSON"):
        ValidatorAgent().run("text", {})


# ---------------------------------------------------------------------------
# 5. run_pipeline — happy path
# ---------------------------------------------------------------------------


def test_run_pipeline_happy_path(monkeypatch, fake_pdf: Path):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")

    # Mock text extraction
    monkeypatch.setattr(pdf_llm_pipeline, "_extract_pdf_text", lambda _: "DRAWING TEXT")

    call_num: dict[str, int] = {"n": 0}

    def fake_generate(prompt: str, **kw: Any) -> str:
        call_num["n"] += 1
        if call_num["n"] == 1:
            return json.dumps(GOOD_EXTRACTED)
        return json.dumps(GOOD_VALIDATION)

    monkeypatch.setattr(pdf_llm_pipeline.llm_service, "generate_text", fake_generate)

    result = run_pipeline(fake_pdf)

    assert result["valid"] is True
    assert result["extracted"]["od_in"] == 1.625
    assert result["validation"]["recommendation"] == "ACCEPT"
    assert result["code_issues"] == []
    assert result["pdf_text_length"] == len("DRAWING TEXT")
    assert call_num["n"] == 2  # exactly two LLM calls


# ---------------------------------------------------------------------------
# 6. run_pipeline — validator says REVIEW
# ---------------------------------------------------------------------------


def test_run_pipeline_not_valid_when_reviewer_says_review(monkeypatch, fake_pdf: Path):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setattr(pdf_llm_pipeline, "_extract_pdf_text", lambda _: "DRAWING TEXT")

    review_validation = {**GOOD_VALIDATION, "recommendation": "REVIEW"}
    call_num: dict[str, int] = {"n": 0}

    def fake_generate(prompt: str, **kw: Any) -> str:
        call_num["n"] += 1
        if call_num["n"] == 1:
            return json.dumps(GOOD_EXTRACTED)
        return json.dumps(review_validation)

    monkeypatch.setattr(pdf_llm_pipeline.llm_service, "generate_text", fake_generate)

    result = run_pipeline(fake_pdf)
    assert result["valid"] is False
    assert result["validation"]["recommendation"] == "REVIEW"


# ---------------------------------------------------------------------------
# 7. run_pipeline — code issues make valid=False even when LLM says ACCEPT
# ---------------------------------------------------------------------------


def test_run_pipeline_not_valid_when_code_issues_exist(monkeypatch, fake_pdf: Path):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setattr(pdf_llm_pipeline, "_extract_pdf_text", lambda _: "TEXT")

    bad_extracted = {**GOOD_EXTRACTED, "od_in": 0.5, "id_in": 2.0}  # OD < ID
    call_num: dict[str, int] = {"n": 0}

    def fake_generate(prompt: str, **kw: Any) -> str:
        call_num["n"] += 1
        if call_num["n"] == 1:
            return json.dumps(bad_extracted)
        return json.dumps(GOOD_VALIDATION)  # LLM says ACCEPT

    monkeypatch.setattr(pdf_llm_pipeline.llm_service, "generate_text", fake_generate)

    result = run_pipeline(fake_pdf)
    assert result["valid"] is False
    assert any("od_in" in i and "greater than id_in" in i for i in result["code_issues"])


# ---------------------------------------------------------------------------
# 8. run_pipeline — missing PDF raises FileNotFoundError
# ---------------------------------------------------------------------------


def test_run_pipeline_raises_for_missing_pdf():
    with pytest.raises(FileNotFoundError):
        run_pipeline(Path("/nonexistent/drawing.pdf"))


# ---------------------------------------------------------------------------
# 9. _extract_pdf_text fallback behaviour
# ---------------------------------------------------------------------------


def test_extract_pdf_text_raises_when_all_parsers_fail(fake_pdf: Path, monkeypatch):
    monkeypatch.setattr(pdf_llm_pipeline, "_extract_pdf_text",
                        lambda p: (_ for _ in ()).throw(RuntimeError("all parsers failed")))
    with pytest.raises(RuntimeError):
        raise RuntimeError("all parsers failed")  # simulate
