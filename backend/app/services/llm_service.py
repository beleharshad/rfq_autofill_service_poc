"""Google Gemini REST wrapper with endpoint fallbacks.

This module supports both newer Gemini `generateContent` responses and older
Generative Language `generateText` responses.

Environment variables:
- `GOOGLE_API_KEY` (required)
- `GOOGLE_GEMINI_MODEL` (optional, default `gemini-2.0-flash`)
- `GOOGLE_GENERATIVE_URL` (optional base URL, default v1beta)
"""

import base64
import datetime
import json
import logging
import os
import time

import requests

logger = logging.getLogger(__name__)


def _get_api_key() -> str | None:
    return os.getenv("GOOGLE_API_KEY")


def _normalize_model_name(model: str | None) -> str:
    raw_model = model or os.getenv("GOOGLE_GEMINI_MODEL", "gemini-2.0-flash")
    return raw_model if raw_model.startswith("models/") else f"models/{raw_model}"


# ---------------------------------------------------------------------------
# Model cascade: automatic fallback when daily quota is exhausted
# ---------------------------------------------------------------------------

# Primary model cascade. Build at call-time so changes to the
# `GOOGLE_GEMINI_MODEL` env var are picked up without restarting the process.
def _get_model_cascade() -> list[str]:
    # Prioritize a known-working model first to avoid cascade blocking.
    # Keep the env-configured model in the list too and de-duplicate below.
    raw = [
        "gemini-2.5-flash",
        os.getenv("GOOGLE_GEMINI_MODEL", "gemini-2.0-flash"),
        # Prefer Gemini 3.1 variants and older models next; try Gemini 4 last
        "gemini-3.1-pro",
        "gemini-3.1-flash-lite",
        "gemini-2.0-flash-lite",
        "gemini-4-26b",
    ]
    seen: set[str] = set()
    return [m for m in raw if not (m in seen or seen.add(m))]

# Epoch-second timestamp after which each model's daily quota resets (0 = not exhausted).
_model_quota_reset: dict[str, float] = {}


def _next_pacific_midnight() -> float:
    """Return UTC epoch seconds of the next US Pacific midnight (PST = UTC-8)."""
    now_utc = datetime.datetime.utcnow()
    # Pacific midnight = 08:00 UTC (conservative: uses PST offset all year)
    candidate = now_utc.replace(hour=8, minute=0, second=0, microsecond=0)
    if now_utc >= candidate:
        candidate += datetime.timedelta(days=1)
    return candidate.timestamp()


def _mark_daily_quota_exhausted(model_raw: str) -> None:
    """Mark *model_raw* as daily-quota-exhausted until next Pacific midnight."""
    reset_at = _next_pacific_midnight()
    _model_quota_reset[model_raw] = reset_at
    reset_str = datetime.datetime.utcfromtimestamp(reset_at).strftime("%Y-%m-%d %H:%M UTC")
    logger.warning(
        "[LLM] Daily quota exhausted for %s — blocked until %s (Pacific midnight)",
        model_raw, reset_str,
    )


def _mark_rate_limited(model_raw: str, seconds: float = 120) -> None:
    """Mark *model_raw* as temporarily rate-limited (per-minute quota). Recovers after *seconds*."""
    _model_quota_reset[model_raw] = time.time() + seconds
    logger.warning(
        "[LLM] Per-minute rate limit on %s — skipping for %ds",
        model_raw, int(seconds),
    )


def _get_active_model() -> str:
    """Return the first model in the cascade whose daily quota is not exhausted."""
    now = time.time()
    cascade = _get_model_cascade()
    for m in cascade:
        if now >= _model_quota_reset.get(m, 0):
            return m
    # All models exhausted — return last and let it fail with a clear error
    return cascade[-1]


def _get_base_url() -> str:
    return os.getenv("GOOGLE_GENERATIVE_URL", "https://generativelanguage.googleapis.com")


def _extract_text(data: object) -> str:
    if isinstance(data, dict):
        candidates = data.get("candidates")
        if isinstance(candidates, list) and candidates:
            first = candidates[0]
            if isinstance(first, dict):
                content = first.get("content")
                if isinstance(content, dict):
                    parts = content.get("parts")
                    if isinstance(parts, list):
                        texts = [part.get("text", "") for part in parts if isinstance(part, dict)]
                        merged = "".join(texts).strip()
                        if merged:
                            return merged
                    text = content.get("text")
                    if isinstance(text, str) and text.strip():
                        return text.strip()
                output = first.get("output")
                if isinstance(output, str) and output.strip():
                    return output.strip()
                return json.dumps(first)

        output = data.get("output")
        if isinstance(output, str) and output.strip():
            return output.strip()
        if isinstance(output, list) and output:
            first = output[0]
            if isinstance(first, dict):
                content = first.get("content")
                if isinstance(content, str) and content.strip():
                    return content.strip()
                return json.dumps(first)
            return str(first)

    return str(data)


# How long to wait after a 429 before retrying (seconds).
# Override via env var LLM_RATE_LIMIT_RETRY_WAIT_S (e.g. "65").
_RATE_LIMIT_RETRY_WAIT_S: int = int(os.getenv("LLM_RATE_LIMIT_RETRY_WAIT_S", "65"))
_RATE_LIMIT_MAX_RETRIES: int = int(os.getenv("LLM_RATE_LIMIT_MAX_RETRIES", "2"))


class RateLimitError(RuntimeError):
    """Raised when Gemini returns HTTP 429.  Carries quota header info."""

    def __init__(self, message: str, rate_limit_info: dict):
        super().__init__(message)
        self.rate_limit_info = rate_limit_info  # parsed from response headers


def _parse_rate_limit_headers(response: "requests.Response") -> dict:
    """Extract quota / rate-limit headers from a Gemini 429 response.

    Gemini (and most Google APIs) return a subset of these headers:
      retry-after                  – seconds until the request can be retried
      x-ratelimit-limit-requests   – max requests per minute
      x-ratelimit-remaining-requests – requests left in this window
      x-ratelimit-reset-requests   – seconds until the window resets
      x-ratelimit-limit-tokens     – token limit per minute
      x-ratelimit-remaining-tokens – tokens left in this window
      x-ratelimit-reset-tokens     – seconds until the token window resets

    Any absent header is recorded as null so the JSON stub is always complete.
    """
    h = response.headers

    def _int(key: str) -> int | None:
        val = h.get(key) or h.get(key.title())
        try:
            return int(val) if val is not None else None
        except (TypeError, ValueError):
            return None

    retry_after = _int("retry-after") or _int("Retry-After")

    info = {
        "retry_after_s":              retry_after,
        "limit_requests_per_min":     _int("x-ratelimit-limit-requests"),
        "remaining_requests":         _int("x-ratelimit-remaining-requests"),
        "reset_requests_in_s":        _int("x-ratelimit-reset-requests"),
        "limit_tokens_per_min":       _int("x-ratelimit-limit-tokens"),
        "remaining_tokens":           _int("x-ratelimit-remaining-tokens"),
        "reset_tokens_in_s":          _int("x-ratelimit-reset-tokens"),
        # Raw response body (may contain quota details from Google)
        "response_body_preview":      response.text[:500] if response.text else None,
    }
    logger.info("[RateLimit] 429 headers: %s", info)
    return info


def _is_daily_quota_exhausted(response: "requests.Response") -> bool:
    """Return True when Gemini's daily (billing) quota is exhausted.

    Gemini signals a fully-exhausted daily quota by including ``"limit: 0"``
    in the 429 response body.  Per-minute rate limits have limit > 0 and
    can benefit from a brief retry; daily exhaustion cannot be resolved by
    waiting 65 seconds, so we fail fast.
    """
    try:
        body = response.text
        return "limit: 0" in body or '"limit":0' in body or '"limit": 0' in body
    except Exception:
        return False


def _is_daily_rate_limit(exc: "RateLimitError") -> bool:
    """Return True if a RateLimitError represents a daily quota exhaustion."""
    info = getattr(exc, "rate_limit_info", {}) or {}
    preview = info.get("response_body_preview") or ""
    return "limit: 0" in preview or '"limit":0' in preview or '"limit": 0' in preview


def _post_json(url: str, payload: dict, timeout: int, *, fast_fail: bool = False) -> object:
    """POST JSON to *url*, retrying on 429 with exponential back-off.

    When *fast_fail* is True (used in cascade mode) any 429 raises immediately
    so the cascade can try the next model without blocking.

    Default: up to 2 retries, waiting 65 s each time (covers the 1 RPM limit).
    Tune via env vars LLM_RATE_LIMIT_RETRY_WAIT_S / LLM_RATE_LIMIT_MAX_RETRIES.
    """
    max_retries = 0 if fast_fail else _RATE_LIMIT_MAX_RETRIES
    for attempt in range(max_retries + 1):
        response = requests.post(
            url,
            json=payload,
            timeout=timeout,
            headers={"Content-Type": "application/json"},
        )
        if response.status_code == 429:
            # Daily quota exhausted — no point retrying
            if _is_daily_quota_exhausted(response):
                logger.warning("Gemini daily quota exhausted (limit: 0) — failing fast")
                response.raise_for_status()
                return {}  # unreachable but satisfies type checker
            # fast_fail: caller (cascade) handles retry by trying next model
            if fast_fail:
                response.raise_for_status()
                return {}  # unreachable
            if attempt < max_retries:
                wait = _RATE_LIMIT_RETRY_WAIT_S * (attempt + 1)  # 65s, 130s, …
                logger.warning(
                    "Gemini 429 rate-limit (attempt %d/%d); retrying in %ds",
                    attempt + 1, max_retries, wait,
                )
                time.sleep(wait)
                continue
        response.raise_for_status()
        return response.json()
    # Should never reach here, but satisfy type checker
    response.raise_for_status()  # type: ignore[possibly-undefined]
    return response.json()  # type: ignore[possibly-undefined]


def _generate_text_with_model(
    prompt: str,
    model_raw: str,
    temperature: float,
    max_output_tokens: int,
    timeout: int,
    *,
    fast_fail: bool = False,
) -> str:
    """Try a single model for text generation; raises RateLimitError / RuntimeError on failure."""
    api_key = _get_api_key()
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY environment variable is not set")
    model_name = _normalize_model_name(model_raw)
    base_url = _get_base_url().rstrip("/")
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature, "maxOutputTokens": int(max_output_tokens)},
    }
    for endpoint in ("v1beta", "v1"):
        url = f"{base_url}/{endpoint}/{model_name}:generateContent?key={api_key}"
        try:
            logger.info("Attempting Gemini request: %s", url)
            data = _post_json(url, payload, timeout, fast_fail=fast_fail)
            text = _extract_text(data).strip()
            if text:
                return text
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 429:
                info = _parse_rate_limit_headers(exc.response)
                raise RateLimitError(
                    f"Gemini 429 on {model_raw}. "
                    f"retry_after={info['retry_after_s']}s  "
                    f"remaining_requests={info['remaining_requests']}  "
                    f"remaining_tokens={info['remaining_tokens']}",
                    rate_limit_info=info,
                ) from exc
            logger.warning("Gemini request failed for %s: %s", url, exc)
        except Exception as exc:
            logger.warning("Gemini request failed for %s: %s", url, exc)
    raise RuntimeError(f"Gemini text request failed for all endpoints with model {model_raw}")


def generate_text(
    prompt: str,
    temperature: float = 0.2,
    max_output_tokens: int = 512,
    timeout: int = 30,
    model: str | None = None,
    max_attempts: int = 1,
) -> str:
    """Generate text from Gemini.

    *max_attempts* limits how many models from the cascade are tried.
    Default is 1 (one Gemini request only) to stay within free-tier daily limits.
    The total across generate_with_image + generate_text per pipeline run is
    therefore at most 2 requests.
    """
    if not prompt or not prompt.strip():
        raise ValueError("prompt must not be empty")

    # If caller specified a model explicitly, use only that (no cascade).
    if model is not None:
        return _generate_text_with_model(prompt, model, temperature, max_output_tokens, timeout)

    last_error: Exception | None = None
    attempts = 0  # counts only non-rate-limited calls (quota-consuming attempts)
    cascade = _get_model_cascade()
    for candidate in cascade:
        if attempts >= max_attempts:
            break
        if time.time() < _model_quota_reset.get(candidate, 0):
            logger.info("[LLM] Skipping %s (rate-limited — will retry later)", candidate)
            continue
        try:
            result = _generate_text_with_model(
                prompt, candidate, temperature, max_output_tokens, timeout, fast_fail=True
            )
            attempts += 1  # only count after a real (non-429) request
            if candidate != cascade[0]:
                logger.info("[LLM] Successfully used fallback model: %s", candidate)
            return result
        except RateLimitError as exc:
            # 429 — quota not consumed, so don't count against max_attempts
            last_error = exc
            if _is_daily_rate_limit(exc):
                _mark_daily_quota_exhausted(candidate)
            else:
                _mark_rate_limited(candidate, seconds=120)
            logger.info("[LLM] Falling back from %s to next model in cascade", candidate)
            continue
        except Exception as exc:
            # Non-429 errors (network/503/etc) — log and try next candidate.
            last_error = exc
            logger.warning("[LLM] Model %s failed with error: %s — falling back", candidate, exc)
            continue

    if last_error:
        raise last_error
    raise RuntimeError("All models in cascade exhausted (rate-limited or quota)")


def generate_with_image(
    prompt: str,
    image_bytes_list: list[bytes],
    *,
    mime_type: str = "image/png",
    temperature: float = 0.0,
    max_output_tokens: int = 2048,
    timeout: int = 60,
    model: str | None = None,
    max_attempts: int = 1,
) -> str:
    """Send one or more images + a text prompt to Gemini Vision (multimodal).

    *max_attempts* limits how many models from the cascade are tried (default 1).
    Together with generate_text(max_attempts=1) this caps total Gemini requests
    to 2 per pipeline run (1 vision + 1 text fallback).
    """
    if not prompt or not prompt.strip():
        raise ValueError("prompt must not be empty")
    if not image_bytes_list:
        raise ValueError("image_bytes_list must contain at least one image")

    api_key = _get_api_key()
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY environment variable is not set")

    model_name = _normalize_model_name(model)
    base_url = _get_base_url().rstrip("/")

    # Build the multimodal parts list: images first, then the text instruction.
    parts: list[dict] = []
    for img_bytes in image_bytes_list:
        b64 = base64.b64encode(img_bytes).decode("ascii")
        parts.append({"inlineData": {"mimeType": mime_type, "data": b64}})
    parts.append({"text": prompt})

    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": int(max_output_tokens),
        },
    }

    attempts = [
        f"{base_url}/v1beta/{model_name}:generateContent?key={api_key}",
        f"{base_url}/v1/{model_name}:generateContent?key={api_key}",
    ]

    if model is not None:
        # Explicit model — no cascade, single attempt
        last_error: Exception | None = None
        for url in attempts:
            try:
                logger.info("Attempting Gemini Vision request: %s", url)
                data = _post_json(url, payload, timeout)
                text = _extract_text(data).strip()
                if text:
                    return text
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code == 429:
                    info = _parse_rate_limit_headers(exc.response)
                    raise RateLimitError(
                        f"Gemini Vision 429 on {model}. "
                        f"retry_after={info['retry_after_s']}s  "
                        f"remaining_requests={info['remaining_requests']}  "
                        f"remaining_tokens={info['remaining_tokens']}",
                        rate_limit_info=info,
                    ) from exc
                logger.warning("Gemini Vision request failed for %s: %s", url, exc)
                last_error = exc
            except Exception as exc:
                logger.warning("Gemini Vision request failed for %s: %s", url, exc)
                last_error = exc
        if last_error:
            raise RuntimeError(f"Gemini Vision request failed: {last_error}") from last_error
        raise RuntimeError("Gemini Vision request failed with no response")

    # No explicit model — cascade, capped at max_attempts models
    cascade_last_err: Exception | None = None
    cascade_attempts = 0  # counts only non-rate-limited calls (quota-consuming attempts)
    cascade = _get_model_cascade()
    for candidate in cascade:
        if cascade_attempts >= max_attempts:
            break
        if time.time() < _model_quota_reset.get(candidate, 0):
            logger.info("[LLM] Vision: skipping %s (rate-limited — will retry later)", candidate)
            continue
        candidate_model_name = _normalize_model_name(candidate)
        base_url_c = _get_base_url().rstrip("/")
        api_key_c = _get_api_key()
        cascade_urls = [
            f"{base_url_c}/v1beta/{candidate_model_name}:generateContent?key={api_key_c}",
            f"{base_url_c}/v1/{candidate_model_name}:generateContent?key={api_key_c}",
        ]
        try:
            for url in cascade_urls:
                try:
                    logger.info("Attempting Gemini Vision request: %s", url)
                    data = _post_json(url, payload, timeout, fast_fail=True)
                    text = _extract_text(data).strip()
                    if text:
                        if candidate != cascade[0]:
                            logger.info("[LLM] Vision: successfully used fallback model: %s", candidate)
                        cascade_attempts += 1  # only count after a real (non-429) request
                        return text
                except requests.HTTPError as exc:
                    if exc.response is not None and exc.response.status_code == 429:
                        info = _parse_rate_limit_headers(exc.response)
                        raise RateLimitError(
                            f"Gemini Vision 429 on {candidate}. "
                            f"retry_after={info['retry_after_s']}s  "
                            f"remaining_requests={info['remaining_requests']}  "
                            f"remaining_tokens={info['remaining_tokens']}",
                            rate_limit_info=info,
                        ) from exc
                    logger.warning("Gemini Vision request failed for %s: %s", url, exc)
        except RateLimitError as exc:
            cascade_last_err = exc
            if _is_daily_rate_limit(exc):
                _mark_daily_quota_exhausted(candidate)
            else:
                _mark_rate_limited(candidate, seconds=120)
            logger.info("[LLM] Vision: falling back from %s to next model", candidate)
            continue

    if cascade_last_err:
        raise cascade_last_err
    raise RuntimeError("All models in cascade exhausted (rate-limited or quota)")
