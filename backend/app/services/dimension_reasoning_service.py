"""LLM-based dimension reasoning service.

Provides a single entrypoint `run_llm_dimension_reasoning(candidate_json: dict) -> dict`
which calls an LLM (OpenAI by default) with a constrained prompt and returns
validated JSON according to the schema required by the project.

Safe fallbacks are provided when the LLM call or parsing fails.
"""

import os
import json
import logging
import time
from typing import Any, Dict

import requests
from pathlib import Path
try:
    # google-auth is optional; only required when using service account flow
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request as GoogleRequest
    _HAS_GOOGLE_AUTH = True
except Exception:
    _HAS_GOOGLE_AUTH = False

try:
    # Optional official Google GenAI client (py package: google-genai)
    from google import genai
    _HAS_GOOGLE_GENAI = True
except Exception:
    _HAS_GOOGLE_GENAI = False

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


DEFAULT_PROMPT = (
    "You are a senior manufacturing drawing analyst.\n\n"
    "You are given dimension candidates already extracted from a 2D engineering drawing.\n"
    "Your task is to identify the quote-driving dimensions for manufacturing.\n\n"
    "You must return these fields:\n"
    "- max_od\n- finish_od\n- max_id\n- finish_id\n- overall_length\n\n"
    "Important definitions:\n"
    "- max_od = largest valid external diameter anywhere on the part\n"
    "- finish_od = main finished outer diameter of the part body, not a short flange or local feature\n"
    "- max_id = largest valid internal diameter anywhere on the part\n"
    "- finish_id = main functional bore diameter, not a short counterbore or local relief\n"
    "- overall_length = total axial end-to-end length of the part\n\n"
    "Important rules:\n"
    "1. Do not simply choose the largest number.\n"
    "2. Do not confuse local features, chamfers, radii, GD&T, notes, or metric bracket values with quote-driving dimensions.\n"
    "3. finish_od is not always max_od.\n"
    "4. finish_id is not always max_id.\n"
    "5. overall_length must represent the total part length, not a local step length.\n"
    "6. If a dimension is given as a range, use:\n"
    "   - upper bound for max_od/max_id when appropriate\n"
    "   - nominal midpoint for finish_od/finish_id/overall_length unless there is a strong reason not to\n"
    "7. If uncertain, return alternates and set needs_review=true.\n\n"
    "You must reason like a machining estimator, not like an OCR parser.\n\n"
    "Input candidate JSON:\n\n"
)


def _build_prompt(candidate_json: Dict[str, Any]) -> str:
    return DEFAULT_PROMPT + json.dumps(candidate_json, indent=2) + "\n\nReturn JSON only."


def _llm_call_openai(prompt: str, max_tokens: int = 512, timeout: int = 15) -> str:
    """Call OpenAI Completion (or Chat Completions) via REST API.

    This small wrapper expects environment variable `OPENAI_API_KEY` to be set.
    If not present, raises RuntimeError.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")

    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": os.environ.get("LLM_MODEL", "gpt-4o-mini"),
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.0,
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    # Navigate response to find assistant content
    try:
        return data["choices"][0]["message"]["content"]
    except Exception:
        # Fallback for different OpenAI shapes
        return data.get("choices", [])[0].get("text", "")


def _llm_call_google(prompt: str, max_tokens: int = 512, timeout: int = 15) -> str:
    """Call Google Generative Language (PaLM) REST API using API key.

    Expects `GOOGLE_API_KEY` to be set in env. Uses model from `LLM_GOOGLE_MODEL`
    (default `text-bison-001`). Returns the generated text content.
    """
    # Prefer service-account-based auth (Gemini / Generative API) when possible
    sa_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    # Allow user to pass a full Gemini model id (e.g. 'gemini-3-flash-preview')
    model = os.environ.get("LLM_GOOGLE_MODEL", "gemini-3-flash-preview")

    # If the official GenAI client is installed, prefer it (handles auth for us)
    if _HAS_GOOGLE_GENAI:
        try:
            client = genai.Client()
            # The client uses model ids like 'gemini-3-flash-preview' or 'models/...'
            resp = client.models.generate_content(model=model, contents=prompt)
            # Many client responses expose `.text` or `.output` — try common attributes
            if hasattr(resp, "text") and resp.text:
                return resp.text
            if hasattr(resp, "output") and resp.output:
                return resp.output
            # Fallback: str()
            return str(resp)
        except Exception as e:
            logger.warning(f"google-genai client failed: {e}")
            # Fall through to service-account / API-key flows

    if sa_path:
        if not _HAS_GOOGLE_AUTH:
            raise RuntimeError("google-auth is required for service account authentication. Install google-auth package.")
        sa_file = Path(sa_path)
        if not sa_file.exists():
            raise RuntimeError(f"Service account file not found: {sa_path}")

        # Obtain short-lived access token using service account
        scopes = ["https://www.googleapis.com/auth/cloud-platform"]
        creds = service_account.Credentials.from_service_account_file(str(sa_file), scopes=scopes)
        creds.refresh(GoogleRequest())
        token = creds.token

        url = f"https://generativelanguage.googleapis.com/v1/{model}:generate"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        payload = {
            "prompt": {"text": prompt},
            "temperature": 0.0,
            "maxOutputTokens": int(max_tokens),
        }

        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()

        # Parse google response shapes
        if isinstance(data, dict):
            if "candidates" in data and isinstance(data["candidates"], list) and data["candidates"]:
                cand = data["candidates"][0]
                if isinstance(cand, dict):
                    return cand.get("content") or cand.get("output") or json.dumps(cand)
                return str(cand)
            if "output" in data and isinstance(data["output"], list) and data["output"]:
                first = data["output"][0]
                if isinstance(first, dict):
                    return first.get("content") or json.dumps(first)
                return str(first)
        return resp.text

    # Fallback: try API key endpoints (older projects may have API key only)
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("Neither GOOGLE_APPLICATION_CREDENTIALS nor GOOGLE_API_KEY is set")

    endpoints = [
        f"https://generativelanguage.googleapis.com/v1/{model}:generate?key={api_key}",
        f"https://generativelanguage.googleapis.com/v1beta2/{model}:generate?key={api_key}",
        f"https://generativelanguage.googleapis.com/v1/{model}:generateText?key={api_key}",
    ]

    payload = {"prompt": {"text": prompt}, "temperature": 0.0, "maxOutputTokens": int(max_tokens)}
    last_exc = None
    for url in endpoints:
        try:
            logger.info(f"Attempting Google LLM request to: {url}")
            resp = requests.post(url, json=payload, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict):
                if "candidates" in data and data["candidates"]:
                    cand = data["candidates"][0]
                    if isinstance(cand, dict):
                        return cand.get("content") or cand.get("output") or json.dumps(cand)
                    return str(cand)
                if "output" in data and data["output"]:
                    first = data["output"][0]
                    if isinstance(first, dict):
                        return first.get("content") or json.dumps(first)
                    return str(first)
            return resp.text
        except Exception as e:
            logger.warning(f"Google LLM endpoint failed ({url}): {e}")
            last_exc = e
            continue

    if last_exc:
        raise last_exc
    raise RuntimeError("Google LLM call failed")


def _validate_schema(out: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and sanitize the returned JSON according to the required schema.

    Performs basic numeric coercion and simple cross-field checks.
    """
    # Ensure keys exist
    schema = {
        "max_od": {"value": None, "confidence": 0.0, "candidate_id": "", "source_text": "", "reason": ""},
        "finish_od": {"value": None, "confidence": 0.0, "candidate_id": "", "source_text": "", "reason": ""},
        "max_id": {"value": None, "confidence": 0.0, "candidate_id": "", "source_text": "", "reason": ""},
        "finish_id": {"value": None, "confidence": 0.0, "candidate_id": "", "source_text": "", "reason": ""},
        "overall_length": {"value": None, "confidence": 0.0, "candidate_id": "", "source_text": "", "reason": ""},
        "alternates": {"finish_od": [], "finish_id": [], "overall_length": []},
        "needs_review": False,
        "review_reasons": [],
    }

    # If out is not dict, return fallback
    if not isinstance(out, dict):
        return {**schema, "needs_review": True, "review_reasons": ["LLM returned non-dict"]}

    # Copy over if present and coerce
    for key in ["max_od", "finish_od", "max_id", "finish_id", "overall_length"]:
        val = out.get(key)
        if isinstance(val, dict):
            # try to coerce value and confidence
            v = val.get("value")
            conf = val.get("confidence", 0.0)
            try:
                v_num = float(v) if v is not None else None
            except Exception:
                v_num = None
            try:
                conf_num = float(conf)
            except Exception:
                conf_num = 0.0
            schema[key]["value"] = v_num
            schema[key]["confidence"] = max(0.0, min(1.0, conf_num))
            schema[key]["candidate_id"] = str(val.get("candidate_id") or "")
            schema[key]["source_text"] = str(val.get("source_text") or "")
            schema[key]["reason"] = str(val.get("reason") or "")
        else:
            # try if straight numeric
            try:
                v_num = float(val) if val is not None else None
            except Exception:
                v_num = None
            schema[key]["value"] = v_num
            schema[key]["confidence"] = float(out.get(f"{key}_confidence") or 0.0)
            schema[key]["candidate_id"] = str(out.get(f"{key}_candidate_id") or "")
            schema[key]["source_text"] = str(out.get(f"{key}_source_text") or "")
            schema[key]["reason"] = str(out.get(f"{key}_reason") or "")

    # Alternates
    alternates = out.get("alternates") or {}
    if isinstance(alternates, dict):
        for k in ["finish_od", "finish_id", "overall_length"]:
            schema["alternates"][k] = alternates.get(k, []) if isinstance(alternates.get(k, []), list) else []

    # needs_review
    schema["needs_review"] = bool(out.get("needs_review", False))
    schema["review_reasons"] = out.get("review_reasons") or []

    # Basic cross-field validations
    try:
        max_od_v = schema["max_od"]["value"]
        finish_od_v = schema["finish_od"]["value"]
        max_id_v = schema["max_id"]["value"]
        finish_id_v = schema["finish_id"]["value"]
        overall_len_v = schema["overall_length"]["value"]
    except Exception:
        max_od_v = finish_od_v = max_id_v = finish_id_v = overall_len_v = None

    # Validation rules: if violated, set needs_review and append reason
    if finish_od_v is not None and max_od_v is not None and finish_od_v > max_od_v + 1e-6:
        schema["needs_review"] = True
        schema["review_reasons"].append("finish_od > max_od")

    if finish_id_v is not None and max_id_v is not None and finish_id_v > max_id_v + 1e-6:
        schema["needs_review"] = True
        schema["review_reasons"].append("finish_id > max_id")

    if finish_id_v is not None and finish_od_v is not None and finish_id_v >= finish_od_v - 1e-6:
        schema["needs_review"] = True
        schema["review_reasons"].append("finish_id >= finish_od")

    if overall_len_v is None or (isinstance(overall_len_v, (int, float)) and overall_len_v <= 0):
        schema["needs_review"] = True
        schema["review_reasons"].append("overall_length missing or <= 0")

    # Deduplicate review reasons
    schema["review_reasons"] = list(dict.fromkeys(schema["review_reasons"]))

    return schema


def run_llm_dimension_reasoning(candidate_json: Dict[str, Any]) -> Dict[str, Any]:
    """Main entrypoint.

    Accepts structured candidate JSON and returns the validated schema.
    """
    prompt = _build_prompt(candidate_json)

    # Default fallback structure
    fallback = {
        "max_od": {"value": None, "confidence": 0.0, "candidate_id": "", "source_text": "", "reason": "LLM fallback"},
        "finish_od": {"value": None, "confidence": 0.0, "candidate_id": "", "source_text": "", "reason": "LLM fallback"},
        "max_id": {"value": None, "confidence": 0.0, "candidate_id": "", "source_text": "", "reason": "LLM fallback"},
        "finish_id": {"value": None, "confidence": 0.0, "candidate_id": "", "source_text": "", "reason": "LLM fallback"},
        "overall_length": {"value": None, "confidence": 0.0, "candidate_id": "", "source_text": "", "reason": "LLM fallback"},
        "alternates": {"finish_od": [], "finish_id": [], "overall_length": []},
        "needs_review": True,
        "review_reasons": ["LLM not run or failed"],
    }

    try:
        # Call LLM using selected provider (env: LLM_PROVIDER)
        provider = os.environ.get("LLM_PROVIDER", "openai").lower()
        start = time.time()
        if provider == "google" or os.environ.get("GOOGLE_API_KEY"):
            logger.info("Using Google LLM provider for dimension reasoning")
            text = _llm_call_google(prompt)
        else:
            logger.info("Using OpenAI LLM provider for dimension reasoning")
            text = _llm_call_openai(prompt)

        elapsed = time.time() - start
        logger.info(f"LLM call completed in {elapsed:.2f}s")

        # Try parse JSON from model output. Model is instructed to return JSON only.
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            # Try to extract JSON-like substring
            first = text.find("{")
            last = text.rfind("}")
            if first != -1 and last != -1 and last > first:
                try:
                    parsed = json.loads(text[first:last+1])
                except Exception:
                    logger.exception("Failed to parse JSON from LLM output")
                    return fallback
            else:
                logger.exception("LLM output not JSON")
                return fallback

        # Validate and return structured schema
        validated = _validate_schema(parsed)
        return validated

    except Exception as e:
        logger.exception(f"LLM dimension reasoning failed: {e}")
        return fallback
