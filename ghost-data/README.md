# Ghost Data Still Exists: deletion-audit agent

HackerRank Orchestrate, October 2026. For each request in `dataset/deletion_requests.csv`, the agent decides whether deletion was authorized. It then reconstructs the subject's identities and resources, establishes each resource's state at `audit_at` from evidence (never from workflow claims), applies `deletion_rules.csv`, and writes ordered findings → remediation → verification steps to `output.csv`.

Challenge / submission: https://wwww.hackerrank.com/contests/hackerrank-orchestrate-october26/challenges/ghost-data-still-exists
Full spec: [`problem_statement.md`](./problem_statement.md).

## Setup

Python 3.11+ (developed on 3.13, Windows).

```bash
python -m venv --system-site-packages .venv
.venv/Scripts/python -m pip install -r code/requirements.txt
```

On macOS/Linux use `.venv/bin/python`.

* `requests` and `pypdf` are required. `pdftotext` (poppler), if on PATH, is preferred for PDFs.
* `rapidocr-onnxruntime` is optional. It corroborates screenshot extractions; without it, corroboration falls back to the dataset text.
* Tesseract is optional and not used.
* LLM keys come from environment variables, or from an uncommitted `.env` at the repo root containing just these two names: `GROQ_API_KEY` and `GEMINI_API_KEY`. Keys are never printed, logged or cached.

## Run

```bash
python code/main.py                          # all 518 eval requests -> ./output.csv (uses the LLM cache)
python code/main.py --strict                 # abort without writing if any LLM call fails or any fallback is used
python code/main.py --no-llm                 # rules only: no intent confirmation, screenshots unread
python code/main.py --no-snapshot            # ignore the committed response snapshot (forces live calls)
python code/main.py --adopt-investigator     # apply row changes from validator-accepted investigator facts
python code/evaluation/test_consistency.py   # consistency-gate tests
python code/evaluation/robustness/run_robustness.py   # malformed / hostile input suite
python code/main.py --samples                # 32 samples -> runs/sample_output.csv
python code/main.py --case case_34 --explain # human-readable trace: links, states, citations, flags
python code/evaluation/validate_output.py    # contract check of ./output.csv
python code/package.py                       # build code.zip (README, DECISIONS, docs, code; no data or secrets)
```

Every case writes a JSON trace to `runs/trace*.jsonl`.

LLM results are cached in `.cache/llm/` (or `$LLM_CACHE_DIR`). With a warm cache a run makes **0 live calls** and is byte-for-byte reproducible. With an empty cache, the pinned models are called (rate-limited, retried, logged to `runs/llm_events.jsonl`).

## Architecture: the model observes, code decides

```
dataset/ ──► EvidenceIndex (SQL, JSON bundles/exports, 6 log formats with trace_id joins, handover TXT, PDF text,
             screenshot extractions) ──► Observations(resource, file, captured_at, record time, fields, identifiers)
requests ──► Intent (rules; LLM confirms) ──► IdentityResolver (current + historical emails, phones, email hashes)
         ──► Linker (identifier links, catalog LINK rows → artifact files)
         ──► Episode scope (D16) ──► Global WorkflowSolver over all 550 requests (accounts and policy actions
             with no identifiers: time windows, result↔status, pins, elimination, chain spacing, co-location)
         ──► StateResolver at audit_at (latest admissible observation; post-capture files only for discovery;
             omission proofs) ──► Policy (category → treatment) ──► Steps (ordering, citations)
         ──► Bounded investigator (uncertain cases only; read-only tools, as_of = audit_at; facts → validator
             → same decision layer)
         ──► Consistency gate (refuses to write inconsistent rows) ──► output.csv
```

**Where an LLM is used, and why it never decides:**
* **Screenshot extraction** (Groq `qwen/qwen3.8-27b`, pinned; Gemini 3.5-flash is an availability-only fallback). The model transcribes the PNG into a fixed JSON schema. Code then:
  * keeps only identifiers corroborated by rapidocr (whole token, **on the same OCR row** as the record's id) or found verbatim in the dataset, and logs the drops;
  * derives state with fixed rules.
  Scored against hand-written fixtures for all 11 PNGs: token precision 1.00, recall 0.90, linked-resource state 11/11.
* **Intent confirmation** (Groq `openai/gpt-oss-120b`, 16 batched, deduplicated calls). Multilingual rules decide; the model's label is recorded, and any disagreement is flagged (0 on the final run).
* **Bounded investigator** (Groq `openai/gpt-oss-120b`, `code/agent/investigator.py`):
  * runs only on the 30 cases the pipeline flags as uncertain (account ties, artifact-only, unassigned policy actions, screenshot drops, citation fallbacks);
  * has 4 read-only actions per case, with the audit_at cutoff enforced inside every tool;
  * proposes facts only, re-verified by a deterministic validator. Final run: 119 actions, 2 proposals (1 accepted-redundant, 1 rejected), 0 rows changed.
* **Consistency gate** (`code/audit/consistency.py`): every row is re-checked before writing (status ↔ phases, finding → fix → check chain, op/state, treatment vs deletion_rules, evidence date and content, ordering, cross-user). Any violation means nothing is written and the exit code is 3.
* **Fallbacks:** a secondary provider, rules-only intent, or OCR-only vision. `--strict` turns any of them, or any failed call, into an abort before `output.csv` is touched.

**Why a deterministic pipeline instead of a free-form agent loop.** The hard part of this dataset is joint reasoning: many accounts and anonymized records carry no identifier, so they can only be attributed by solving all 550 requests together. On top of that, the output contract (ordering, evidence rules, the audit_at cutoff) is strict. A rules-plus-solver pipeline is reproducible (identical output from cache), measurable (hold-outs, ablations, fixtures), cheap (27 LLM calls for the whole dataset) and explainable (`--explain` shows every link and citation). An agent loop would be non-deterministic per case and could not see the cross-request constraints.

## File guide

| Path | What it is |
|---|---|
| `code/main.py` | Entry point (CLI above) |
| `code/audit/` | Pipeline: `data`, `evidence` (parsers), `identity`, `linking`, `workflow` (global solver), `signals`, `state`, `policy`, `intent`, `pipeline` |
| `code/llm/` | `client` (cache, limiter, retries, events), `models` (pinned models, limits), `vision`, `intent_llm`, `smoke_test` |
| `code/llm/cache_snapshot/` | The exact 146 LLM responses (11 vision, 16 intent, 119 investigator) and 11 OCR results behind the final `output.csv`; contains no keys |
| `code/agent/investigator.py` | Bounded investigator agent and its deterministic validator |
| `code/audit/consistency.py` | Consistency gate |
| `code/evaluation/test_consistency.py`, `code/evaluation/robustness/` | Gate tests (14) and robustness suite (12) |
| `docs/interview_sheet.md` | Rule → code map, monitoring signals, edge cases, likely questions |
| `code/evaluation/` | Grader, validator, custom cases, hold-outs, audits, invariants, recall audit; **`evaluation_report.md`**, **`usage_report.md`** |
| `code/evaluation/fixtures/` | Hand-written expected screenshot extractions (test only, never runtime input) |
| `DECISIONS.md` | Every non-obvious rule with its evidence and measurements (D1–D47) |
| `docs/` | `rule_ledger.md` (derivations, hold-out tables), `data_profile.md`, `sample_trace.md`, `open_questions.md`, `llm_limits.md` |

## Results (details in `code/evaluation/evaluation_report.md`)

* **Samples (32):**
  * status accuracy 1.000;
  * step F1 0.992 (precision 1.000, recall 0.985);
  * evidence accuracy 1.000;
  * exact rows **29/32** (the 3 misses name resources that appear nowhere in participant data).
* **Custom cases (8):** 8/8 exact.
* **Independent audits by fresh sub-agents (2 × 20 eval cases):** status agreement 20/20 and 20/20; exact rows 14/20 and 14/20; 5 pipeline bugs found and fixed.
* **Account timing hold-out:** 0.992 leave-one-out / 0.976 hide-all, with the workflow-clock signature confirmed by 2-fold cross-validation (≥ 0.984 on every held-out fold). **Policy actions:** 120/121 correct, 0 wrong.
* **Final eval output:** 518 rows (152 COMPLETE, 356 INCOMPLETE, 10 NOT_APPLICABLE), 0 contract errors, deterministic.

## Where it falls short

* Identifier-free accounts in multi-request batches: 14 deterministic ties remain. They cannot change a status, only one resource_id.
* 2 anonymized policy actions stay unassigned. Eight others are given to the candidate owning the fewest other resources (D39); that rule is weakly supported (matched hold-out 1/1).
* Ten hand-crafted artifact cases have no admissible account.
* Conventions the samples never exercise (mixed-table absence proofs, legal holds) rest on policy text and audit agreement, not gold.

## Running without API keys

* **Default:** `python code/main.py` serves any response missing from `.cache/llm/` from the committed snapshot in `code/llm/cache_snapshot/`. A fresh clone with a new venv and no keys reproduces `output.csv` **byte-for-byte** (verified: 518 rows, 0 contract errors, 0 live calls).
* **`--no-snapshot` with no keys:** the screenshots go unread and intent falls back to the rules. **9 rows differ** from `output.csv`: case_34, 35, 39, 50, 52, 53, 55, 61 and 63 go INCOMPLETE → COMPLETE, because their only findings come from screenshots.
* **Live runs** need `GROQ_API_KEY` (`GEMINI_API_KEY` is used only as an availability fallback).
  * Calls are paced at 70 % of each model's 8000 tokens/minute limit.
  * 429s are retried up to 7 times, honouring `retry-after` and logging the response message.
  * `--strict` aborts without touching `output.csv` on any failed call or any fallback.
