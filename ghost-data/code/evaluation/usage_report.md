# Usage report: final full-dataset run

## Was the final run live or cached?

**Cached.** `output.csv` was produced on 2026-10-25 by `python code/main.py --strict` (no fallback allowed), which read every LLM result from the disk cache (`.cache/llm/`, keyed by provider + model + prompt + image hash + parameters). That run made **0 live calls** (vision, intent and investigator responses all served from cache); a second run reproduced it byte-for-byte.

The cache entries it used were created by real live calls on 2026-10-24/25 UTC: the 11 vision calls during the prompt-v2 / max_tokens=1500 run, and the 16 intent batches during the intent re-run. Both appear in `runs/llm_events.jsonl` with outcome `ok`.

**Investigator calls (2026-10-25, 11:19–11:45 UTC):** 119 live calls to Groq `openai/gpt-oss-120b` (plus 8 in a 2-case smoke test), ≈ 66.5k tokens, 0 failures or 429s. They are cached and committed in the snapshot, and the final run replays them.

**Complete live run (all three call groups, fresh cache): not possible before about 2026-10-25T21:46Z.** gpt-oss's rolling daily budget (60.1k left) is below 1.2 × the need of 104.7k (D51).

**Final output.csv was served from the cache of real live calls.** These calls produced it (event log `runs/llm_events.jsonl`; the responses are committed in `code/llm/cache_snapshot/`):

* 2026-10-24 21:20–21:31 UTC: 11 vision calls, Groq `qwen/qwen3.8-27b` (prompt v2, max_tokens 1500), with 3 × 429 and 1 × 503 retried.
* 2026-10-24 21:34–21:49 UTC: 16 intent batches, Groq `openai/gpt-oss-120b`, 0 retries.

**Strict live re-runs with an empty cache: both aborted, output.csv untouched.**

| Attempt | Headroom check | Outcome |
|---|---|---|
| 1 (2026-10-24 ~22:45 UTC) | qwen ≈ 98k of 200K daily tokens left vs a 2× need of ~90k: passed | qwen returned per-minute 429s on evidence_42 (retries then backed off only 3–24 s), and the Gemini fallback truncated → strict abort (D45) |
| 2 (2026-10-24 23:11–23:18 UTC) | 1.2× rule: qwen ≈ 74k left vs 1.2 × 33.5k ≈ 40k; gpt-oss ≈ 127k left vs 1.2 × 38k ≈ 46k: passed | Paced at 70 % TPM (5600 tokens/min). 1 vision call ok, then **7 consecutive HTTP 429s** on the second screenshot, honouring `retry-after` (about 7 min) → strict abort. qwen answered normally minutes later, so this was a sticky burst, not the daily budget |

The 429 response body was not logged then; the client now records it in the event log for the next attempt. Groq's daily window is rolling; most of this session's qwen usage frees up around 2026-10-25T20:00Z.

## Calls behind output.csv (per model)

| Provider | Model | Purpose | Calls | Cache hits in final run | Input tokens | Output tokens | Total tokens |
|---|---|---|---|---|---|---|---|
| Groq | qwen/qwen3.8-27b | screenshot extraction (11 PNGs) | 11 | 11 | 24,519 | 9,043 | 33,562 |
| Groq | openai/gpt-oss-120b | intent confirmation (550 request texts, deduplicated, 25 per batch) | 16 | 16 | 22,719 | 15,521 | 38,240 |
| Groq | openai/gpt-oss-120b | bounded investigator (30 uncertain cases, ≤ 4 actions each) | 119 | 119 | 54,250 | 8,267 | 62,517 |
| **Overall** | | | **146** | **146** | **101,488** | **32,831** | **134,319** |

* **Per request (518 eval requests):** 0.28 calls, 195.9 input + 63.4 output = **259.3 tokens**. The intent batches also cover the 32 sample requests (550 texts).
* **Fallback extractions in this run:** **0**. All 11 screenshots were read by the pinned model; the trace field `image_extraction.fallback_used` is false for every screenshot-cited resource. Rules-only intent: 0. OCR-only vision: 0.
* **Rate-limit retries while producing these cache entries:** vision 3 × HTTP 429 and 1 × HTTP 503, all recovered on retry; intent 0.

## Cost

* **Actual cost: $0.** Both models were used on Groq's free tier.
* **Estimate at current paid prices.** Source: https://console.groq.com/docs/models, read 2026-10-25. `openai/gpt-oss-120b` costs $0.15 / M input and $0.60 / M output; `qwen/qwen3.8-27b` costs $0.80 / M input and $4.00 / M output.

| Model | Input cost | Output cost | Total |
|---|---|---|---|
| qwen/qwen3.8-27b | 24,519 × $0.80/M = $0.0196 | 9,043 × $4.00/M = $0.0362 | $0.0558 |
| openai/gpt-oss-120b (intent) | 22,719 × $0.15/M = $0.0034 | 15,521 × $0.60/M = $0.0093 | $0.0127 |
| openai/gpt-oss-120b (investigator) | 54,250 × $0.15/M = $0.0081 | 8,267 × $0.60/M = $0.0050 | $0.0131 |
| **Overall** | | | **$0.0816** (≈ **$0.00016 per request**) |

## All live calls made during development (not used for predictions unless listed above)

From `runs/llm_events.jsonl` (event logging began 2026-10-24T20:17Z) and the cache files. Smoke tests before that are counted from the cache only.

| Provider / model | Successful calls | Input tokens | Output tokens | Errors seen |
|---|---|---|---|---|
| Groq qwen/qwen3.8-27b | 44 | 92,376 | 33,963 | 18 × 429 and 2 × 503, all retried; 1 aborted strict run |
| Groq openai/gpt-oss-120b | 34 | 44,988 | 28,398 | 1 × 400 (headroom probe with too-small max_tokens) |
| Groq openai/gpt-oss-20b | 1 | 120 | 173 | — |
| Gemini gemini-3.5-flash | 9 | 11,254 | 23,376 | 11 × 429 (free-tier quota: 20 requests/day/model), 1 truncation |
| Gemini gemini-3.5-flash-lite | 2 | 1,157 | 1,464 | — |

At the paid prices above, the Groq development calls total about $0.23 (qwen ≈ $0.21, gpt-oss ≈ $0.024). Gemini calls were free tier only.
