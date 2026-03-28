"""
Test scenarios for llm_service.generate_text()

Coverage:
  - Missing API key
  - Empty / whitespace-only prompt
  - Modern generateContent multi-part response
  - Multi-part concatenation
  - content.text fallback (no parts key)
  - Legacy output-string response
  - Legacy output-list response
  - All endpoints fail → RuntimeError
  - Network timeout propagation
  - Custom model override (with/without models/ prefix)
  - Model sourced from env var
  - Custom base URL from env var
  - Temperature and max_output_tokens forwarded in payload
  - _normalize_model_name unit tests
  - _extract_text direct unit tests
"""

import json

import pytest
import requests as _requests

from app.services import llm_service


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class MockResponse:
    def __init__(self, payload=None, status_code=200, text=""):
        self._payload = payload if payload is not None else {}
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            error = _requests.HTTPError(f"HTTP {self.status_code}: {self.text}")
            error.response = self
            raise error

    def json(self):
        return self._payload


def _always_return(payload):
    """Return a monkeypatch mock that always responds with *payload*."""
    return lambda *args, **kwargs: MockResponse(payload)


# ---------------------------------------------------------------------------
# 1. API key guard
# ---------------------------------------------------------------------------

def test_generate_text_raises_when_key_missing(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GOOGLE_API_KEY"):
        llm_service.generate_text("hello")


# ---------------------------------------------------------------------------
# 2. Prompt validation
# ---------------------------------------------------------------------------

def test_generate_text_rejects_empty_prompt(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    with pytest.raises(ValueError, match="prompt"):
        llm_service.generate_text("   ")


def test_generate_text_rejects_blank_string(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    with pytest.raises(ValueError, match="prompt"):
        llm_service.generate_text("")


# ---------------------------------------------------------------------------
# 3. Modern generateContent response — single part
# ---------------------------------------------------------------------------

def test_generate_text_parses_generate_content_response(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setattr(
        llm_service.requests, "post",
        _always_return({
            "candidates": [
                {"content": {"parts": [{"text": "Hello from Gemini"}]}}
            ]
        }),
    )
    assert llm_service.generate_text("Say hello") == "Hello from Gemini"


# ---------------------------------------------------------------------------
# 4. Multi-part text concatenation
# ---------------------------------------------------------------------------

def test_generate_text_concatenates_multiple_parts(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setattr(
        llm_service.requests, "post",
        _always_return({
            "candidates": [
                {"content": {"parts": [{"text": "Part one. "}, {"text": "Part two."}]}}
            ]
        }),
    )
    assert llm_service.generate_text("Multi part") == "Part one. Part two."


# ---------------------------------------------------------------------------
# 5. content.text fallback (no parts key)
# ---------------------------------------------------------------------------

def test_generate_text_parses_content_text_fallback(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setattr(
        llm_service.requests, "post",
        _always_return({
            "candidates": [
                {"content": {"text": "Text via content.text"}}
            ]
        }),
    )
    assert llm_service.generate_text("content.text") == "Text via content.text"


# ---------------------------------------------------------------------------
# 6. Legacy candidate.output string
# ---------------------------------------------------------------------------

def test_generate_text_parses_legacy_output_string(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setattr(
        llm_service.requests, "post",
        _always_return({"candidates": [{"output": "Legacy output"}]}),
    )
    assert llm_service.generate_text("Legacy") == "Legacy output"


# ---------------------------------------------------------------------------
# 7. Endpoint fallback chain
# ---------------------------------------------------------------------------

def test_generate_text_falls_back_to_v1_endpoint(monkeypatch):
    """v1beta fails with 404 → v1 generateContent succeeds."""
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    calls = {"count": 0}

    def mock_post(url, *args, **kwargs):
        calls["count"] += 1
        if calls["count"] < 2:
            return MockResponse(status_code=404, text="not found")
        return MockResponse({"candidates": [{"content": {"parts": [{"text": "v1 response"}]}}]})

    monkeypatch.setattr(llm_service.requests, "post", mock_post)
    assert llm_service.generate_text("Fallback please") == "v1 response"
    assert calls["count"] == 2


# ---------------------------------------------------------------------------
# 8. All endpoints fail
# ---------------------------------------------------------------------------

def test_generate_text_raises_when_all_endpoints_fail(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setattr(
        llm_service.requests, "post",
        lambda *a, **k: MockResponse(status_code=500, text="Server error"),
    )
    with pytest.raises(RuntimeError, match="failed after fallbacks"):
        llm_service.generate_text("Will fail")


# ---------------------------------------------------------------------------
# 8b. 429 rate-limit exits immediately without trying second URL
# ---------------------------------------------------------------------------

def test_generate_text_raises_immediately_on_429(monkeypatch):
    """429 from v1beta should NOT trigger the v1 fallback — same key, same quota."""
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setattr(llm_service.time, "sleep", lambda _: None)  # skip waits
    call_count = {"n": 0}

    def mock_post(url, *args, **kwargs):
        call_count["n"] += 1
        return MockResponse(status_code=429, text="Too Many Requests")

    monkeypatch.setattr(llm_service.requests, "post", mock_post)
    with pytest.raises(RuntimeError, match="rate limit"):
        llm_service.generate_text("rate limited")
    # Only 2 HTTP calls (1 attempt + 1 retry in _post_json), never tries v1
    assert call_count["n"] == 2


# ---------------------------------------------------------------------------
# 9. Network timeout propagation
# ---------------------------------------------------------------------------

def test_generate_text_propagates_timeout_error(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")

    def raise_timeout(*args, **kwargs):
        raise _requests.Timeout("connection timed out")

    monkeypatch.setattr(llm_service.requests, "post", raise_timeout)
    with pytest.raises(RuntimeError, match="failed after fallbacks"):
        llm_service.generate_text("timeout test")


# ---------------------------------------------------------------------------
# 10. Custom model override
# ---------------------------------------------------------------------------

def test_generate_text_uses_custom_model_without_prefix(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    captured = {}

    def mock_post(url, *args, **kwargs):
        captured["url"] = url
        return MockResponse({"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})

    monkeypatch.setattr(llm_service.requests, "post", mock_post)
    llm_service.generate_text("test", model="gemini-2.0-flash")
    assert "models/gemini-2.0-flash" in captured["url"]


def test_generate_text_uses_custom_model_with_prefix(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    captured = {}

    def mock_post(url, *args, **kwargs):
        captured["url"] = url
        return MockResponse({"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})

    monkeypatch.setattr(llm_service.requests, "post", mock_post)
    llm_service.generate_text("test", model="models/gemini-2.0-flash")
    assert "models/gemini-2.0-flash" in captured["url"]
    assert "models/models/" not in captured["url"]


# ---------------------------------------------------------------------------
# 11. Model from env var
# ---------------------------------------------------------------------------

def test_generate_text_uses_model_from_env_var(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setenv("GOOGLE_GEMINI_MODEL", "gemini-env-model")
    captured = {}

    def mock_post(url, *args, **kwargs):
        captured["url"] = url
        return MockResponse({"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})

    monkeypatch.setattr(llm_service.requests, "post", mock_post)
    llm_service.generate_text("env model test")
    assert "models/gemini-env-model" in captured["url"]


# ---------------------------------------------------------------------------
# 12. Custom base URL from env var
# ---------------------------------------------------------------------------

def test_generate_text_uses_custom_base_url(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setenv("GOOGLE_GENERATIVE_URL", "https://my-proxy.example.com")
    captured = {}

    def mock_post(url, *args, **kwargs):
        captured["url"] = url
        return MockResponse({"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})

    monkeypatch.setattr(llm_service.requests, "post", mock_post)
    llm_service.generate_text("proxy test")
    assert captured["url"].startswith("https://my-proxy.example.com")


# ---------------------------------------------------------------------------
# 13. Temperature and max_output_tokens forwarded in payload
# ---------------------------------------------------------------------------

def test_generate_text_forwards_generation_params(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    captured = {}

    def mock_post(url, *args, **kwargs):
        captured["payload"] = kwargs.get("json", {})
        return MockResponse({"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})

    monkeypatch.setattr(llm_service.requests, "post", mock_post)
    llm_service.generate_text("params test", temperature=0.9, max_output_tokens=256)

    cfg = captured["payload"].get("generationConfig", {})
    assert cfg.get("temperature") == pytest.approx(0.9)
    assert cfg.get("maxOutputTokens") == 256


# ---------------------------------------------------------------------------
# 14. _normalize_model_name unit tests
# ---------------------------------------------------------------------------

def test_normalize_model_name_adds_prefix(monkeypatch):
    monkeypatch.delenv("GOOGLE_GEMINI_MODEL", raising=False)
    assert llm_service._normalize_model_name("gemini-1.5-flash") == "models/gemini-1.5-flash"


def test_normalize_model_name_keeps_existing_prefix():
    assert llm_service._normalize_model_name("models/gemini-1.5-pro") == "models/gemini-1.5-pro"


def test_normalize_model_name_defaults_to_flash(monkeypatch):
    monkeypatch.delenv("GOOGLE_GEMINI_MODEL", raising=False)
    assert llm_service._normalize_model_name(None) == "models/gemini-2.0-flash"


# ---------------------------------------------------------------------------
# 15. _extract_text direct unit tests
# ---------------------------------------------------------------------------

def test_extract_text_from_non_dict_returns_str():
    assert llm_service._extract_text(42) == "42"
    assert llm_service._extract_text(None) == "None"


def test_extract_text_from_empty_candidates_falls_through():
    result = llm_service._extract_text({"candidates": []})
    assert result == str({"candidates": []})


def test_extract_text_from_output_list(monkeypatch):
    data = {"output": [{"content": "from list"}]}
    assert llm_service._extract_text(data) == "from list"