"""HTTP transport for Google Gemini. The only module that opens a socket.

`ocr.py` and `observe.py` describe WHAT they want; this module knows how to ask
for it, how to retry, and how to count what it cost. Base URL, model names and
the key's environment variable all come from `ModelConfig`, so swapping provider
is a config edit.

The API key is read from the environment at call time, sent in a header, and
never logged, cached, traced, or written to the usage report.

Retries are BOUNDED: a fixed attempt count, linear backoff, every sleep capped,
and `Retry-After` honoured only up to that cap. There is no unbounded loop.
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Any

import requests
from pydantic import BaseModel, ConfigDict

from src.config import ModelConfig
from src.usage import UsageRecorder


class ModelUnavailable(RuntimeError):
    """No API key, or the provider could not be reached within the retry budget.

    Every caller treats this as "no model output", never as a failure of the run.
    """


class ModelResponse(BaseModel):
    """A parsed JSON response plus what the call cost."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    payload: dict[str, Any]
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    wall_seconds: float = 0.0


#: Status codes worth trying again: rate limit and transient server faults.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def _sleep_seconds(response: requests.Response | None, attempt: int, config: ModelConfig) -> float:
    """How long to wait before the next attempt, always capped.

    Honours the provider's own `Retry-After` when it sends one, because that is
    the quota talking, but never sleeps longer than `max_retry_sleep_seconds`.
    """
    hinted: float | None = None
    if response is not None:
        raw = response.headers.get("Retry-After") or response.headers.get("retry-after")
        if raw:
            try:
                hinted = float(raw)
            except ValueError:
                hinted = None
    wait = hinted if hinted is not None else config.retry_backoff_seconds * attempt
    return max(0.0, min(wait, config.max_retry_sleep_seconds))


def _extract_text(payload: dict[str, Any]) -> str:
    """Pull the single text part out of a Gemini candidate."""
    candidates = payload.get("candidates") or []
    if not candidates:
        return ""
    parts = (candidates[0].get("content") or {}).get("parts") or []
    return "".join(part.get("text", "") for part in parts)


def image_part(path: Path) -> dict[str, Any]:
    """Inline a PNG as base64, the shape Gemini expects."""
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return {"inline_data": {"mime_type": "image/png", "data": encoded}}


def text_part(text: str) -> dict[str, Any]:
    return {"text": text}


def generate(
    *,
    config: ModelConfig,
    model: str,
    system_instruction: str,
    parts: list[dict[str, Any]],
    response_schema: dict[str, Any] | None,
    recorder: UsageRecorder,
    stage: str,
    units: int = 1,
) -> dict[str, Any]:
    """One `generateContent` call, returning the parsed JSON body.

    Raises `ModelUnavailable` when there is no key or the retry budget is spent.
    Every attempt is recorded, so the usage report reflects failed calls too.
    """
    credentials = config.credentials()
    if not credentials:
        raise ModelUnavailable(
            f"{config.api_key_env_var} is not set; no model call was attempted"
        )

    url = f"{config.base_url}/models/{model}:generateContent"
    body: dict[str, Any] = {
        "contents": [{"role": "user", "parts": parts}],
        "systemInstruction": {"parts": [{"text": system_instruction}]},
        "generationConfig": {
            "temperature": config.temperature,
            "maxOutputTokens": config.max_output_tokens,
            "responseMimeType": "application/json",
        },
    }
    if response_schema is not None:
        body["generationConfig"]["responseSchema"] = response_schema

    # Pace ourselves rather than discovering the quota by tripping over it.
    if config.inter_call_sleep_seconds > 0:
        time.sleep(config.inter_call_sleep_seconds)

    last_error = "no attempt made"
    # Try each credential in priority order. Same provider, same model, same
    # request -- only the key differs -- so a quota-exhausted primary falls
    # through to the secondary instead of stranding the run.
    for key_label, key in credentials:
        for attempt in range(1, config.max_retries + 1):
            started = time.monotonic()
            response: requests.Response | None = None
            try:
                response = requests.post(
                    url,
                    params={"key": key},
                    json=body,
                    timeout=config.request_timeout_seconds,
                    headers={"Content-Type": "application/json"},
                )
                elapsed = time.monotonic() - started

                if response.status_code in RETRYABLE_STATUS:
                    # Keep the body: a 429 says whether the limit is per-minute
                    # or per-day, and which quota was hit.
                    last_error = f"HTTP {response.status_code}: {response.text[:900]}"
                    recorder.record(
                        provider=config.provider, model=model, stage=stage,
                        wall_seconds=elapsed, units=units, key_label=key_label,
                        ok=False, error=last_error,
                    )
                    if attempt < config.max_retries:
                        time.sleep(_sleep_seconds(response, attempt, config))
                    continue

                if response.status_code >= 400:
                    # Not retryable, and not the credential's fault: a bad
                    # request stays bad on every key, so do not burn the others.
                    last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                    recorder.record(
                        provider=config.provider, model=model, stage=stage,
                        wall_seconds=elapsed, units=units, key_label=key_label,
                        ok=False, error=last_error,
                    )
                    raise ModelUnavailable(last_error)

                payload = response.json()
                usage = payload.get("usageMetadata") or {}
                recorder.record(
                    provider=config.provider,
                    model=model,
                    stage=stage,
                    input_tokens=int(usage.get("promptTokenCount", 0)),
                    output_tokens=int(usage.get("candidatesTokenCount", 0)),
                    cached_tokens=int(usage.get("cachedContentTokenCount", 0)),
                    wall_seconds=elapsed,
                    units=units,
                    key_label=key_label,
                    ok=True,
                )

                text = _extract_text(payload)
                if not text.strip():
                    last_error = "empty response body"
                    if attempt < config.max_retries:
                        time.sleep(_sleep_seconds(None, attempt, config))
                        continue
                    raise ModelUnavailable(last_error)

                try:
                    return json.loads(text)
                except json.JSONDecodeError as exc:
                    # Malformed JSON: the caller makes no amendment and the
                    # image stays unresolved. Never a guess, never a zero.
                    last_error = f"malformed JSON: {exc}"
                    if attempt < config.max_retries:
                        time.sleep(_sleep_seconds(None, attempt, config))
                        continue
                    raise ModelUnavailable(last_error) from exc

            except requests.RequestException as exc:
                elapsed = time.monotonic() - started
                last_error = f"{type(exc).__name__}: {exc}"
                recorder.record(
                    provider=config.provider, model=model, stage=stage,
                    wall_seconds=elapsed, units=units, key_label=key_label,
                    ok=False, error=last_error,
                )
                if attempt < config.max_retries:
                    time.sleep(_sleep_seconds(None, attempt, config))
                    continue

    raise ModelUnavailable(
        f"exhausted {config.max_retries} attempts across "
        f"{len(credentials)} credential(s) ({', '.join(n for n, _ in credentials)}): "
        f"{last_error}"
    )
