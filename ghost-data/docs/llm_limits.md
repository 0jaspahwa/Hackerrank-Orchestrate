# LLM providers, measured limits and model choice (2026-10-25)

Source: `python code/llm/smoke_test.py` (raw results in `.cache/smoke_test.json`, gitignored). Keys are read from `.env` (gitignored, never committed) and never printed or logged. Gemini receives the key in the `x-goog-api-key` header, never in the URL.

## Available models
* **Groq (11):** `openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `openai/gpt-oss-safeguard-20b`, `qwen/qwen3.8-27b`, `allam-2-7b`, plus guard, TTS and whisper models. There are no Llama 3.3 / Llama 4 chat models.
* **Gemini (46 with generateContent):** `gemini-3.5-flash`, `gemini-3.5-flash-lite`, `gemini-3.8-flash`, 3.x previews, gemma-4 and others. `gemini-2.5-flash` and `-flash-lite` are listed but return HTTP 404 "no longer available to new users".

## Text request (tiny JSON intent prompt)

| Model | Latency | Tokens in/out | JSON | Result |
|---|---|---|---|---|
| groq `openai/gpt-oss-120b` | 1.3–1.5 s | 120 / 101 | ok | correct |
| groq `openai/gpt-oss-20b` | 1.2–1.5 s | 120 / 173 | ok | correct |
| groq `qwen/qwen3.8-27b` | 0.8 s | 37 / 7 | ok | correct; one 429 on a rerun burst |
| gemini `gemini-3.5-flash-lite` | 5.3 s | 25 / 9 | ok | correct |
| gemini `gemini-3.5-flash` | 3.2–3.9 s | 25 / 89–207 (incl. thinking) | ok | correct |
| gemini `gemini-3.8-flash` | timed out (90 s) | — | — | not usable today |

## Image request (`backup_restore_console_20260429.png`)
Target strings: `backup_9287`, `areed1433@gmail.com`, `Retention end`, `26 Jul 2026`.

| Model | Latency | Tokens in/out | JSON | Exact reads |
|---|---|---|---|---|
| gemini `gemini-3.5-flash` | 15.7–17.7 s | 1132 / ~3850 (incl. thinking) | ok | 4/4 |
| gemini `gemini-3.5-flash-lite` | 7.5 s | 1132 / 1455 | ok | 4/4 |
| groq `qwen/qwen3.8-27b` | 4.2–4.8 s | 1866 / 1457 | ok | 4/4 |
| gemini `gemini-3.8-flash` | timed out | — | — | — |

## Observed limits
* **Groq** headers on every response: `x-ratelimit-limit-requests: 1000` (reset ~1–3 min, a rolling daily window), `x-ratelimit-limit-tokens: 8000` per minute, per model. Remaining tokens dropped about 2.1k after one image call.
* **Gemini** sends no rate-limit headers. A 429 carries `retryDelay`, which the client honours.

## Observed errors, retries and quotas (live runs 2026-10-25, `runs/llm_events.jsonl`)

| Model | Attempts ok | 429 | other | Notes |
|---|---|---|---|---|
| gemini `gemini-3.5-flash` | 9 (smoke + 7 vision) | 3 retried 429s, then 8 fail-fast quota 429s | — | **Daily quota exhausted after 20 requests (incl. failed ones):** `GenerateRequestsPerDayPerProjectPerModel-FreeTier`, `quotaValue: 20`, dimension model `gemini-3.6-flash` (3.5-flash is served by it), retryDelay ≈ 38 400 s (~10.7 h). The client now fails fast on a per-day quota instead of retrying 4 × 60 s. |
| groq `qwen/qwen3.8-27b` | 22 | 8 (all recovered on attempt 2) | 1 × 503 (recovered) | Headers report 1000 requests/day and 8000 TPM. The 429s arrive with ~5k tokens still "remaining"; Groq reserves `max_tokens` (8192 > 8000) against TPM, so a call only fits an otherwise empty minute window. Harmless with the retry; lowering max_tokens to 4096 would change the cache key (a re-extraction), so it is left for the next prompt revision. |
| groq `openai/gpt-oss-120b` | 16 intent batches | 0 | 0 | ~1 batch/min under the limiter |

A limiter bug was found and fixed during these runs: a single request's estimated tokens (image + max_tokens 8192) exceeded the 6000 TPM self-limit, so `wait()` could never return. Estimates are now capped at 90 % of TPM and use typical output, not max_tokens.

## Vision model scoring against the 11 hand-written fixtures (`code/evaluation/vision_eval.py`)

Tokens = resource ids, emails, user ids and phones per image, after corroboration (OCR token + **same OCR row as the record's id** + dataset). "State" = the state the pipeline derives for the catalog-linked resource.

| Model / prompt | Images read | Token P | Token R | Linked state | Notes |
|---|---|---|---|---|---|
| gemini-3.5-flash, prompt v1 | 7/11 (quota) | 1.000 | 0.750 (36/48) | 5/11 | On its 7 images it missed 1 token (analytics_7438, shown only in a subtitle) |
| qwen3.8-27b, prompt v1 | 11/11 | 0.949 | 0.771 | 8/11 | Before boundary and row checks, truncated misreads (`…@company.co`) passed corroboration |
| **qwen3.8-27b, prompt v2 (pinned)** | 11/11 | **1.000** | **0.896 (43/48)** | **11/11** | All 5 misses are misreads that corroboration dropped (kagyeman8947 → 8847, bluepeakco → bluepeako, two `company.co` truncations, g.silva76.jr → g.silva76 on the decoy row). None touches a linked resource. |

Prompt v2 adds `page_subtitle`, `page_resource_id` and "ids shown ANYWHERE". Gemini could not be re-scored on v2 today (quota).

**Pin:** vision = groq `qwen/qwen3.8-27b`. On the 7 images both models read, Gemini reads raw strings slightly better (1 miss vs 5). But every qwen miss is caught by corroboration (0 false tokens), the derived states are 11/11, Groq's limits are published, and Gemini's free tier (20 requests/day/model) cannot absorb a re-extraction plus retries. Gemini remains an availability-only fallback. A fallback extraction is flagged per resource in the trace (`image_extraction.fallback_used`) and counted in `evaluation/usage_report.md` (0 in the current run). Optional: re-score Gemini on prompt v2 after its quota resets (11 calls).

## Does a full live run fit?

| Call group | Model | Calls | Tokens (measured) | Limits | Wall time under our limiter |
|---|---|---|---|---|---|
| Screenshot extraction | groq qwen3.8-27b | 11 | ≈ 2.9k each, ≈ 32k total | 1000 req/day, 8000 TPM | ≈ 11 min (1/min; 429s self-heal) |
| Intent confirmation | groq gpt-oss-120b | 16 batches × 25 texts | ≈ 2.2k each, ≈ 35k total | 1000 req/day, 8000 TPM | ≈ 16 min (separate per-model budget, can overlap) |

27 requests is **under 3 %** of each model's daily request limit, and the per-minute token budget is respected by the limiter. With the cache warm, a rerun makes 0 calls.

## Choice and limiter (`code/llm/models.py`)

| Use | Primary | Fallback | Limiter |
|---|---|---|---|
| Intent confirmation (text) | groq `openai/gpt-oss-120b` | gemini `gemini-3.5-flash-lite` | 20 RPM / 6000 TPM (75 % of 8000) |
| Screenshot extraction (vision) | **groq `qwen/qwen3.8-27b` (pinned)** | gemini `gemini-3.5-flash` (availability only; 20 req/day) | 10 RPM / 6000 TPM (qwen), 5 RPM (Gemini) |

All calls run at temperature 0 with JSON output and go through a disk cache (`.cache/llm/`), so reruns are free and deterministic. OCR corroboration: `rapidocr-onnxruntime` in the repo venv (`.venv`, on F:), which also reads all 4 target strings.
