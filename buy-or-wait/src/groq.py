"""HTTP transport for Groq. TEXT ONLY -- Groq has no vision model.

The sibling of `src/gemini.py`, not a layer above it. Groq's API is
OpenAI-compatible, so this is one client function with a different request
shape; `observe.py` picks between the two on a config value.

The prompt, the response schema and the validator are the SAME ones the Gemini
path uses. A different model has a different failure profile, so the
deterministic validator in `observe.py` is doing more work here, not less.

The API key is read from the environment at call time and never logged, cached,
traced, or written to the usage report.

Retries are BOUNDED: a fixed attempt count, linear backoff, every sleep capped,
and `Retry-After` honoured only up to that cap.
"""

from __future__ import annotations

import json
import time
from typing import Any

import requests

from src.config import GroqConfig
from src.gemini import ModelUnavailable
from src.usage import UsageRecorder

#: Rate limit and transient server faults. Same policy as the Gemini client.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def _sleep_seconds(response: requests.Response | None, attempt: int, config: GroqConfig) -> float:
    """How long to wait before the next attempt, always capped."""
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


def _as_strict(schema: dict[str, Any]) -> dict[str, Any]:
    """A strict-mode spelling of the SAME schema. Semantics unchanged.

    Groq validates `json_schema` strictly and rejects the whole call with
    `json_validate_failed` when an object allows unlisted properties or leaves
    properties optional. This rewrites the shape the API needs -- every property
    required, no additional properties -- without touching the schema that
    `observe.py` owns. Optional fields stay optional in MEANING: the model may
    send them empty, and `IncomeAmendment` already coerces empty to None.
    """
    if not isinstance(schema, dict):
        return schema

    out = dict(schema)
    if out.get("type") == "object":
        properties = {k: _as_strict(v) for k, v in (out.get("properties") or {}).items()}
        out["properties"] = properties
        out["required"] = list(properties)
        out["additionalProperties"] = False
    elif out.get("type") == "array" and "items" in out:
        out["items"] = _as_strict(out["items"])
    return out


#: OpenAI-compatible `json_object` mode REFUSES any request whose messages do
#: not contain the literal token "json". This is an API precondition, not a
#: change to the instructions -- it is appended only on the fallback path.
JSON_MODE_REQUIREMENT = (
    "\n\nReturn your answer as a single json object with an "
    '"amendments" array.'
)


def generate(
    *,
    config: GroqConfig,
    model: str,
    system_instruction: str,
    prompt: str,
    response_schema: dict[str, Any] | None,
    recorder: UsageRecorder,
    stage: str,
    units: int = 1,
) -> dict[str, Any]:
    """One chat completion, returning the parsed JSON body.

    Raises `ModelUnavailable` when there is no key or the retry budget is spent.
    Callers treat that as "no amendment", never as a failure of the run.
    """
    key = config.api_key()
    if not key:
        raise ModelUnavailable(
            f"{config.api_key_env_var} is not set; no model call was attempted"
        )

    # The same schema the Gemini path passes as `responseSchema`, handed over
    # through this API's equivalent field. Not a different contract -- but this
    # API validates it strictly, so it has to be spelled strictly.
    if response_schema is not None:
        response_format: dict[str, Any] = {
            "type": "json_schema",
            "json_schema": {
                "name": "observation_batch",
                "schema": _as_strict(response_schema),
            },
        }
    else:
        response_format = {"type": "json_object"}

    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": prompt},
        ],
        "temperature": config.temperature,
        "max_tokens": config.max_output_tokens,
        "response_format": response_format,
    }

    url = f"{config.base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    if config.inter_call_sleep_seconds > 0:
        time.sleep(config.inter_call_sleep_seconds)

    last_error = "no attempt made"
    for attempt in range(1, config.max_retries + 1):
        started = time.monotonic()
        response: requests.Response | None = None
        try:
            response = requests.post(
                url, json=body, headers=headers, timeout=config.request_timeout_seconds
            )
            elapsed = time.monotonic() - started

            if response.status_code in RETRYABLE_STATUS:
                last_error = f"HTTP {response.status_code}: {response.text[:900]}"
                recorder.record(
                    provider=config.provider, model=model, stage=stage,
                    wall_seconds=elapsed, units=units, key_label=config.api_key_env_var,
                    ok=False, error=last_error,
                )
                if attempt < config.max_retries:
                    time.sleep(_sleep_seconds(response, attempt, config))
                continue

            if response.status_code >= 400:
                # Groq rejects the whole call with `json_validate_failed` when
                # the model's output does not conform to the strict schema.
                # That is a conformance miss, not a bad request, so it is worth
                # one retry in permissive JSON mode -- the SAME schema still
                # governs, via the system prompt, and the deterministic
                # validator gates the result either way.
                last_error = f"HTTP {response.status_code}: {response.text[:400]}"
                recorder.record(
                    provider=config.provider, model=model, stage=stage,
                    wall_seconds=elapsed, units=units, key_label=config.api_key_env_var,
                    ok=False, error=last_error,
                )
                schema_rejected = (
                    "json_validate_failed" in response.text
                    or "response_format" in response.text
                )
                if (
                    body["response_format"]["type"] == "json_schema"
                    and schema_rejected
                    and attempt < config.max_retries
                ):
                    body["response_format"] = {"type": "json_object"}
                    # json_object mode is refused outright unless the messages
                    # mention json. Append it to the SYSTEM message only.
                    body["messages"][0]["content"] = (
                        system_instruction + JSON_MODE_REQUIREMENT
                    )
                    continue
                raise ModelUnavailable(last_error)

            payload = response.json()
            usage = payload.get("usage") or {}
            recorder.record(
                provider=config.provider,
                model=model,
                stage=stage,
                input_tokens=int(usage.get("prompt_tokens", 0)),
                output_tokens=int(usage.get("completion_tokens", 0)),
                wall_seconds=elapsed,
                units=units,
                key_label=config.api_key_env_var,
                ok=True,
            )

            choices = payload.get("choices") or []
            text = (choices[0].get("message") or {}).get("content", "") if choices else ""
            if not text.strip():
                last_error = "empty response body"
                if attempt < config.max_retries:
                    time.sleep(_sleep_seconds(None, attempt, config))
                    continue
                raise ModelUnavailable(last_error)

            try:
                return json.loads(text)
            except json.JSONDecodeError as exc:
                # Malformed JSON means no amendment for this batch. Never a guess.
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
                wall_seconds=elapsed, units=units, key_label=config.api_key_env_var,
                ok=False, error=last_error,
            )
            if attempt < config.max_retries:
                time.sleep(_sleep_seconds(None, attempt, config))
                continue

    raise ModelUnavailable(f"exhausted {config.max_retries} attempts: {last_error}")
