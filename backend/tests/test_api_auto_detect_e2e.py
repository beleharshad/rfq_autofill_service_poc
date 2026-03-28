"""
End-to-end API test for POST /jobs/{job_id}/pdf/auto_detect_turned_view
with a mock LLM (Gemini calls intercepted).

What this test verifies:
  1. The endpoint exists and returns 200.
  2. The response contains the standard CV-detection keys PLUS `llm_analysis`.
  3. `llm_analysis` carries the expected extracted dimensions (od_in, id_in,
     length_in, max_od_in, material, part_number).
  4. `llm_analysis.valid` is True when both agents agree.
  5. `llm_analysis.json` is written to disk (outputs folder).
  6. The pipeline gracefully degrades when the PDF is absent (llm_analysis.error).
  7. 429 from Gemini does NOT crash the endpoint â€” error surfaced in llm_analysis.
  8. Vision-mode flag is propagated into the response.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests as _requests
from fastapi.testclient import TestClient

from app.main import app
import app.api.pdf as pdf_api_mod
from app.services import llm_service, pdf_llm_pipeline

# ---------------------------------------------------------------------------
# Test fixture paths
# ---------------------------------------------------------------------------

BACKEND_DIR = Path(__file__).parent.parent
REAL_PDF = (
    BACKEND_DIR
    / "data" / "jobs" / "bff75f7b-d6f8-4786-8e42-2a38b7983628"
    / "inputs" / "source.pdf"
)

TEST_JOB_ID = "mock-e2e-job-0001"
_ENDPOINT = f"/api/v1/jobs/{TEST_JOB_ID}/pdf/auto_detect_turned_view"

# ---------------------------------------------------------------------------
# Mock Gemini responses (match the exact fixture values from test_llm_e2e_scenario)
# ---------------------------------------------------------------------------

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
    "tolerance_od": "Â±0.001",
    "tolerance_id": "Â±0.002",
    "tolerance_length": "Â±0.003",
    "finish": "63 Âµin Ra",
    "revision": "E4",
})

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

# Minimal CV result returned by the mocked auto_detect_service
FAKE_CV_RESULT: dict = {
    "job_id": TEST_JOB_ID,
    "ranked_views": [],
    "best_view": None,
    "status": "no_views_detected",
    "message": "mocked â€” no real OpenCV run",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeGeminiResponse:
    """Minimal stand-in for requests.Response understood by llm_service._post_json."""

    def __init__(self, body, status_code: int = 200):
        self._body = body
        self.status_code = status_code
        self.text = str(body)

    def raise_for_status(self):
        if self.status_code >= 400:
            err = _requests.HTTPError(f"HTTP {self.status_code}")
            err.response = self
            raise err

    def json(self):
        return self._body


def _gemini_ok(text: str) -> _FakeGeminiResponse:
    return _FakeGeminiResponse(
        {"candidates": [{"content": {"parts": [{"text": text}]}}]}
    )


def _gemini_429() -> _FakeGeminiResponse:
    return _FakeGeminiResponse({}, status_code=429)


def _make_gemini_mock(
    extractor_text: str = EXTRACTOR_REPLY,
    validator_text: str = VALIDATOR_REPLY,
):
    """Return a requests.post side_effect function that tracks call count."""
    call_count: list[int] = [0]

    def _side_effect(url, *args, **kwargs):
        call_count[0] += 1
        # 1st generateContent â†’ ExtractorAgent, 2nd â†’ ValidatorAgent
        if call_count[0] == 1:
            return _gemini_ok(extractor_text)
        return _gemini_ok(validator_text)

    return _side_effect, call_count


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_job_dir(tmp_path: Path):
    """Create a temp job directory with source.pdf + outputs/ ready."""
    job_root = tmp_path / TEST_JOB_ID
    inputs_dir = job_root / "inputs"
    outputs_dir = job_root / "outputs"
    inputs_dir.mkdir(parents=True)
    outputs_dir.mkdir(parents=True)

    # Copy the real PDF if available; otherwise write a tiny stub
    if REAL_PDF.exists():
        shutil.copy2(REAL_PDF, inputs_dir / "source.pdf")
    else:
        (inputs_dir / "source.pdf").write_bytes(b"%PDF-1.4 stub")

    return job_root


@pytest.fixture()
def patched_services(tmp_job_dir: Path, monkeypatch):
    """
    Patch all external service calls so the test runs fully offline:
      - job_service.get_job  â†’ returns a fake Job object (no DB)
      - job_service.file_storage.get_inputs_path  â†’ points to tmp_job_dir/inputs
      - job_service.file_storage.get_outputs_path â†’ points to tmp_job_dir/outputs
      - auto_detect_service.auto_detect_turned_view â†’ returns FAKE_CV_RESULT
      - pdf_llm_pipeline._extract_pdf_text â†’ returns pre-baked OCR stub
    """
    # Fake job model (only needs job_id attribute for the endpoint)
    fake_job = MagicMock()
    fake_job.job_id = TEST_JOB_ID

    # Patch job existence check
    monkeypatch.setattr(
        pdf_api_mod.job_service, "get_job", lambda jid: fake_job
    )

    # Patch file_storage paths
    inputs_dir = tmp_job_dir / "inputs"
    outputs_dir = tmp_job_dir / "outputs"
    monkeypatch.setattr(
        pdf_api_mod.job_service.file_storage,
        "get_inputs_path",
        lambda jid: inputs_dir,
    )
    monkeypatch.setattr(
        pdf_api_mod.job_service.file_storage,
        "get_outputs_path",
        lambda jid: outputs_dir,
    )

    # Patch CV auto-detect (avoid OpenCV + EasyOCR dependency)
    monkeypatch.setattr(
        pdf_api_mod.auto_detect_service,
        "auto_detect_turned_view",
        lambda jid: dict(FAKE_CV_RESULT),
    )

    # Speed-up: bypass real OCR inside the pipeline
    monkeypatch.setattr(
        pdf_llm_pipeline,
        "_extract_pdf_text",
        lambda *_: (
            "Part: 050CE0004  OD: 1.240  MAX OD: 1.380  ID: .430  "
            "LENGTH: .630  Material: 80-55-06 Ductile Iron  Rev: E4"
        ),
    )

    return {"inputs_dir": inputs_dir, "outputs_dir": outputs_dir}


# ---------------------------------------------------------------------------
# ===========================================================================
# Test Suite
# ===========================================================================
# ---------------------------------------------------------------------------

class TestAutoDetectE2E:
    """Full endpoint test: CV detection + LLM pipeline together, mock Gemini."""

    ENV = {"GOOGLE_API_KEY": "test-key-e2e"}

    # ------------------------------------------------------------------
    # 1. HTTP layer
    # ------------------------------------------------------------------

    def test_endpoint_returns_200(self, patched_services):
        """Must return HTTP 200 with mocked services."""
        mock_fn, _ = _make_gemini_mock()
        client = TestClient(app)
        with patch.dict(os.environ, self.ENV):
            with patch("app.services.llm_service.requests.post", side_effect=mock_fn):
                resp = client.post(_ENDPOINT)
        assert resp.status_code == 200, resp.text

    def test_response_is_valid_json(self, patched_services):
        mock_fn, _ = _make_gemini_mock()
        client = TestClient(app)
        with patch.dict(os.environ, self.ENV):
            with patch("app.services.llm_service.requests.post", side_effect=mock_fn):
                resp = client.post(_ENDPOINT)
        data = resp.json()
        assert isinstance(data, dict)

    # ------------------------------------------------------------------
    # 2. CV detection part preserved
    # ------------------------------------------------------------------

    def test_cv_result_present_in_response(self, patched_services):
        mock_fn, _ = _make_gemini_mock()
        client = TestClient(app)
        with patch.dict(os.environ, self.ENV):
            with patch("app.services.llm_service.requests.post", side_effect=mock_fn):
                resp = client.post(_ENDPOINT)
        data = resp.json()
        assert data.get("job_id") == TEST_JOB_ID
        assert "ranked_views" in data

    # ------------------------------------------------------------------
    # 3. llm_analysis present and error-free
    # ------------------------------------------------------------------

    def test_llm_analysis_key_present(self, patched_services):
        mock_fn, _ = _make_gemini_mock()
        client = TestClient(app)
        with patch.dict(os.environ, self.ENV):
            with patch("app.services.llm_service.requests.post", side_effect=mock_fn):
                resp = client.post(_ENDPOINT)
        assert "llm_analysis" in resp.json()

    def test_llm_analysis_has_no_error(self, patched_services):
        mock_fn, _ = _make_gemini_mock()
        client = TestClient(app)
        with patch.dict(os.environ, self.ENV):
            with patch("app.services.llm_service.requests.post", side_effect=mock_fn):
                resp = client.post(_ENDPOINT)
        llm = resp.json()["llm_analysis"]
        assert "error" not in llm, f"Unexpected error: {llm.get('error')}"

    # ------------------------------------------------------------------
    # 4. Extracted dimensions
    # ------------------------------------------------------------------

    def test_finish_od_matches_fixture(self, patched_services):
        mock_fn, _ = _make_gemini_mock()
        client = TestClient(app)
        with patch.dict(os.environ, self.ENV):
            with patch("app.services.llm_service.requests.post", side_effect=mock_fn):
                resp = client.post(_ENDPOINT)
        extracted = resp.json()["llm_analysis"]["extracted"]
        assert extracted["od_in"] == pytest.approx(1.240, abs=1e-6)

    def test_max_od_matches_fixture(self, patched_services):
        mock_fn, _ = _make_gemini_mock()
        client = TestClient(app)
        with patch.dict(os.environ, self.ENV):
            with patch("app.services.llm_service.requests.post", side_effect=mock_fn):
                resp = client.post(_ENDPOINT)
        extracted = resp.json()["llm_analysis"]["extracted"]
        assert extracted["max_od_in"] == pytest.approx(1.380, abs=1e-6)

    def test_finish_id_matches_fixture(self, patched_services):
        mock_fn, _ = _make_gemini_mock()
        client = TestClient(app)
        with patch.dict(os.environ, self.ENV):
            with patch("app.services.llm_service.requests.post", side_effect=mock_fn):
                resp = client.post(_ENDPOINT)
        extracted = resp.json()["llm_analysis"]["extracted"]
        assert extracted["id_in"] == pytest.approx(0.430, abs=1e-6)

    def test_finish_length_matches_fixture(self, patched_services):
        mock_fn, _ = _make_gemini_mock()
        client = TestClient(app)
        with patch.dict(os.environ, self.ENV):
            with patch("app.services.llm_service.requests.post", side_effect=mock_fn):
                resp = client.post(_ENDPOINT)
        extracted = resp.json()["llm_analysis"]["extracted"]
        assert extracted["length_in"] == pytest.approx(0.630, abs=1e-6)

    def test_part_number_extracted(self, patched_services):
        mock_fn, _ = _make_gemini_mock()
        client = TestClient(app)
        with patch.dict(os.environ, self.ENV):
            with patch("app.services.llm_service.requests.post", side_effect=mock_fn):
                resp = client.post(_ENDPOINT)
        extracted = resp.json()["llm_analysis"]["extracted"]
        assert extracted.get("part_number") == "050CE0004"

    def test_material_extracted(self, patched_services):
        mock_fn, _ = _make_gemini_mock()
        client = TestClient(app)
        with patch.dict(os.environ, self.ENV):
            with patch("app.services.llm_service.requests.post", side_effect=mock_fn):
                resp = client.post(_ENDPOINT)
        mat = (resp.json()["llm_analysis"]["extracted"].get("material") or "").lower()
        assert "iron" in mat or "ductile" in mat

    def test_od_gt_id(self, patched_services):
        """Physical sanity: finish OD must exceed finish ID."""
        mock_fn, _ = _make_gemini_mock()
        client = TestClient(app)
        with patch.dict(os.environ, self.ENV):
            with patch("app.services.llm_service.requests.post", side_effect=mock_fn):
                resp = client.post(_ENDPOINT)
        ext = resp.json()["llm_analysis"]["extracted"]
        assert ext["od_in"] > ext["id_in"]

    def test_max_od_gte_od(self, patched_services):
        """MAX OD â‰¥ Finish OD â€” raw stock is never smaller than finish."""
        mock_fn, _ = _make_gemini_mock()
        client = TestClient(app)
        with patch.dict(os.environ, self.ENV):
            with patch("app.services.llm_service.requests.post", side_effect=mock_fn):
                resp = client.post(_ENDPOINT)
        ext = resp.json()["llm_analysis"]["extracted"]
        assert ext["max_od_in"] >= ext["od_in"]

    # ------------------------------------------------------------------
    # 5. Validation block
    # ------------------------------------------------------------------

    def test_validation_recommendation_accept(self, patched_services):
        mock_fn, _ = _make_gemini_mock()
        client = TestClient(app)
        with patch.dict(os.environ, self.ENV):
            with patch("app.services.llm_service.requests.post", side_effect=mock_fn):
                resp = client.post(_ENDPOINT)
        val = resp.json()["llm_analysis"]["validation"]
        assert val["recommendation"] == "ACCEPT"

    def test_validation_confidence_high(self, patched_services):
        mock_fn, _ = _make_gemini_mock()
        client = TestClient(app)
        with patch.dict(os.environ, self.ENV):
            with patch("app.services.llm_service.requests.post", side_effect=mock_fn):
                resp = client.post(_ENDPOINT)
        score = resp.json()["llm_analysis"]["validation"]["overall_confidence"]
        assert score >= 0.85, f"confidence {score} < 0.85"

    def test_pipeline_valid_flag_true(self, patched_services):
        mock_fn, _ = _make_gemini_mock()
        client = TestClient(app)
        with patch.dict(os.environ, self.ENV):
            with patch("app.services.llm_service.requests.post", side_effect=mock_fn):
                resp = client.post(_ENDPOINT)
        assert resp.json()["llm_analysis"]["valid"] is True

    def test_no_code_issues(self, patched_services):
        mock_fn, _ = _make_gemini_mock()
        client = TestClient(app)
        with patch.dict(os.environ, self.ENV):
            with patch("app.services.llm_service.requests.post", side_effect=mock_fn):
                resp = client.post(_ENDPOINT)
        assert resp.json()["llm_analysis"]["code_issues"] == []

    # ------------------------------------------------------------------
    # 6. Disk artifact: llm_analysis.json written to outputs/
    # ------------------------------------------------------------------

    def test_llm_analysis_json_written_to_disk(self, patched_services):
        outputs_dir: Path = patched_services["outputs_dir"]
        mock_fn, _ = _make_gemini_mock()
        client = TestClient(app)
        with patch.dict(os.environ, self.ENV):
            with patch("app.services.llm_service.requests.post", side_effect=mock_fn):
                client.post(_ENDPOINT)
        artifact = outputs_dir / "llm_analysis.json"
        assert artifact.exists(), "llm_analysis.json not written to disk"
        with artifact.open(encoding="utf-8") as f:
            cached = json.load(f)
        assert cached["extracted"]["od_in"] == pytest.approx(1.240, abs=1e-6)

    def test_llm_analysis_json_is_valid_json(self, patched_services):
        outputs_dir: Path = patched_services["outputs_dir"]
        mock_fn, _ = _make_gemini_mock()
        client = TestClient(app)
        with patch.dict(os.environ, self.ENV):
            with patch("app.services.llm_service.requests.post", side_effect=mock_fn):
                client.post(_ENDPOINT)
        artifact = outputs_dir / "llm_analysis.json"
        # json.load raises if content is not valid JSON
        with artifact.open(encoding="utf-8") as f:
            json.load(f)  # must not raise

    # ------------------------------------------------------------------
    # 7. Graceful degradation: PDF absent â†’ error surfaced, not 500
    # ------------------------------------------------------------------

    def test_missing_pdf_returns_llm_error_not_500(self, patched_services):
        """If source.pdf is absent, llm_analysis.error must be set â€” not HTTP 500."""
        inputs_dir: Path = patched_services["inputs_dir"]
        pdf = inputs_dir / "source.pdf"
        if pdf.exists():
            pdf.unlink()

        mock_fn, _ = _make_gemini_mock()
        client = TestClient(app)
        with patch.dict(os.environ, self.ENV):
            with patch("app.services.llm_service.requests.post", side_effect=mock_fn):
                resp = client.post(_ENDPOINT)

        assert resp.status_code == 200, "Endpoint must not crash on missing PDF"
        llm = resp.json()["llm_analysis"]
        assert "error" in llm
        assert "source.pdf" in llm["error"].lower() or "not found" in llm["error"].lower()

    # ------------------------------------------------------------------
    # 8. Graceful degradation: Gemini 429 â†’ error surfaced, not 500
    # ------------------------------------------------------------------

    def test_gemini_429_returns_llm_error_not_500(self, patched_services):
        """A 429 (quota exhausted) must surface in llm_analysis.error, not crash."""
        call_count: list[int] = [0]

        def _always_429(url, *args, **kwargs):
            call_count[0] += 1
            return _gemini_429()

        client = TestClient(app)
        with patch.dict(os.environ, self.ENV):
            with patch("app.services.llm_service.time.sleep"):  # skip retry waits
                with patch("app.services.llm_service.requests.post", side_effect=_always_429):
                    resp = client.post(_ENDPOINT)

        assert resp.status_code == 200, "Endpoint must not return 5xx on quota error"
        llm = resp.json()["llm_analysis"]
        assert "error" in llm, f"Expected error key, got: {list(llm.keys())}"

    # ------------------------------------------------------------------
    # 9. Exactly 2 Gemini calls (Extractor + Validator)
    # ------------------------------------------------------------------

    def test_exactly_two_gemini_calls_made(self, patched_services):
        """Two generateContent requests: one per agent, no extra retries."""
        mock_fn, call_count = _make_gemini_mock()
        client = TestClient(app)
        with patch.dict(os.environ, self.ENV):
            with patch("app.services.llm_service.requests.post", side_effect=mock_fn):
                client.post(_ENDPOINT)
        assert call_count[0] == 2, f"Expected 2 Gemini calls, got {call_count[0]}"

    # ------------------------------------------------------------------
    # 10. vision_mode flag present in llm_analysis
    # ------------------------------------------------------------------

    def test_vision_mode_flag_present(self, patched_services):
        """run_pipeline always returns a vision_mode key so frontend can show it."""
        mock_fn, _ = _make_gemini_mock()
        client = TestClient(app)
        with patch.dict(os.environ, self.ENV):
            with patch("app.services.llm_service.requests.post", side_effect=mock_fn):
                resp = client.post(_ENDPOINT)
        assert "vision_mode" in resp.json()["llm_analysis"]

    # ------------------------------------------------------------------
    # 11. REVIEW verdict â†’ valid=False propagated through endpoint
    # ------------------------------------------------------------------

    def test_review_verdict_propagates_valid_false(self, patched_services):
        review_reply = json.dumps({
            "fields": {},
            "cross_checks": ["od_in seems suspiciously small"],
            "overall_confidence": 0.55,
            "recommendation": "REVIEW",
        })
        mock_fn, _ = _make_gemini_mock(validator_text=review_reply)
        client = TestClient(app)
        with patch.dict(os.environ, self.ENV):
            with patch("app.services.llm_service.requests.post", side_effect=mock_fn):
                resp = client.post(_ENDPOINT)
        llm = resp.json()["llm_analysis"]
        assert llm["valid"] is False
        assert llm["validation"]["recommendation"] == "REVIEW"

