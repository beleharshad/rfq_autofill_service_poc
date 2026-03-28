import os
import json
import types
import pytest

from app.services.dimension_reasoning_service import run_llm_dimension_reasoning


class MockResponse:
    def __init__(self, content_str):
        self._content = content_str

    def raise_for_status(self):
        return None

    def json(self):
        # Return a structure similar to OpenAI chat completions
        return {"choices": [{"message": {"content": self._content}}]}


def test_llm_success(monkeypatch, tmp_path):
    # Ensure dummy API key
    monkeypatch.setenv("OPENAI_API_KEY", "testkey")

    assistant_content = json.dumps({
        "max_od": {"value": 1.63, "confidence": 0.95, "candidate_id": "seg_od_0", "source_text": "segment_0", "reason": "largest external"},
        "finish_od": {"value": 1.63, "confidence": 0.9, "candidate_id": "seg_od_0", "source_text": "segment_0", "reason": "main body"},
        "max_id": {"value": 1.13, "confidence": 0.9, "candidate_id": "seg_id_0", "source_text": "segment_0", "reason": ""},
        "finish_id": {"value": 1.13, "confidence": 0.9, "candidate_id": "seg_id_0", "source_text": "segment_0", "reason": ""},
        "overall_length": {"value": 4.0, "confidence": 0.9, "candidate_id": "seg_len_0", "source_text": "segment_0", "reason": ""},
        "alternates": {"finish_od": [], "finish_id": [], "overall_length": []},
        "needs_review": False,
        "review_reasons": []
    })

    # Monkeypatch requests.post
    import requests
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: MockResponse(assistant_content))

    out = run_llm_dimension_reasoning({})

    assert out["max_od"]["value"] == pytest.approx(1.63)
    assert out["finish_od"]["value"] == pytest.approx(1.63)
    assert out["max_id"]["value"] == pytest.approx(1.13)
    assert out["overall_length"]["value"] == pytest.approx(4.0)
    assert out["needs_review"] is False


def test_llm_invalid_json_fallback(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "testkey")

    bad_output = "This is not JSON"
    import requests
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: MockResponse(bad_output))

    out = run_llm_dimension_reasoning({})
    assert out["needs_review"] is True
    assert "LLM not run or failed" in out["review_reasons"]


def test_llm_validation_triggers_review(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "testkey")

    # finish_od > max_od and overall_length <= 0 to trigger review reasons
    assistant_content = json.dumps({
        "max_od": {"value": 1.0, "confidence": 0.9},
        "finish_od": {"value": 2.0, "confidence": 0.9},
        "max_id": {"value": 0.5, "confidence": 0.9},
        "finish_id": {"value": 0.4, "confidence": 0.9},
        "overall_length": {"value": 0.0, "confidence": 0.2},
        "alternates": {},
        "needs_review": False,
        "review_reasons": []
    })

    import requests
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: MockResponse(assistant_content))

    out = run_llm_dimension_reasoning({})
    assert out["needs_review"] is True
    assert "finish_od > max_od" in out["review_reasons"]
    assert "overall_length missing or <= 0" in out["review_reasons"]
