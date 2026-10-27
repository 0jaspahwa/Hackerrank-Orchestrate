# Evaluation report: Ghost Data Still Exists

Every number below is reproducible from the repo, from cache only (no live LLM calls):

| Measurement | Command |
|---|---|
| Sample metrics | `python code/main.py --samples` then `python code/evaluation/grade.py --pred runs/sample_output.csv --expect dataset/sample_deletion_requests.csv` |
| Custom cases | `python code/evaluation/custom_eval.py` |
| Independent audits | `python code/evaluation/compare_audit.py output.csv [independent_audit_2 audit2_selection.json]` |
| Timing hold-outs | `python code/evaluation/holdout_all.py`, `tiebreak_matched.py`, `policy_ties.py` |
| Invariants / recall | `python code/evaluation/invariants.py`, `recall_audit.py` |
| Contract | `python code/evaluation/validate_output.py` |

Decisions referenced as Dnn are in `DECISIONS.md`; derivations are in `docs/rule_ledger.md`.

## 1. Public samples (32 cases, scored against `dataset/sample_output.csv`)

`sample_output.csv` is used for grading only. The pipeline never reads it.

| Metric | Value |
|---|---|
| Case-level deletion-status accuracy | **1.000** (10/10 COMPLETE, 22/22 INCOMPLETE) |
| Exact row match (status + full ordered step list) | **29/32 = 0.906** |
| Step precision / recall / F1 (phase, system, resource, operation, state) | **1.000 / 0.985 / 0.992** |
| Exact-step F1 (including evidence_id) | 0.966 |
| Evidence accuracy on matched steps | **1.000** |
| Resource recall | 0.973 |
| Order accuracy when the step set is right | 1.000 |

Field-level accuracy (each gold step aligned to the best predicted step of the same resource and phase):

| step_no | phase | system_id | resource_id | operation | state | evidence_id |
|---|---|---|---|---|---|---|
| 0.959 | 0.984 | 0.984 | 0.984 | 0.984 | 0.984 | 0.984 |

The 3 non-exact sample rows are unrecoverable from participant data (section 6).

How the sample score evolved, each step gated by the hold-outs:

| Change | Exact rows | Step F1 |
|---|---|---|
| First deterministic pipeline | 23/32 | 0.960 |
| Solver constraints D24–D26 | 26/32 | 0.982 |
| D43 account timing signature | 27/32 | 0.987 |
| D46 policy-action signature | 28/32 | 0.990 |
| D39 fewest-resources for the residual open actions | **29/32** | **0.992** |

## 2. Custom evaluation cases (8, written for this project)

`code/evaluation/custom_cases/`: `cases.json` (inputs), `expected.csv` (hand-derived rows). Each case replaces one existing request with a variant and gets its own global solve, so variants never compete. Final result: **8/8 exact**, status 1.000, step F1 1.000, evidence 1.000.

| Case | What it tests that the samples do not | First run | Final |
|---|---|---|---|
| custom_01 | French **non-deletion** request that contains "suppression" | FAIL (rules said deletion) | PASS |
| custom_02 | Japanese non-deletion request containing 削除 | FAIL | PASS |
| custom_03 | Historical-email-only request with odd casing and spaces (case_12) | PASS | PASS |
| custom_04 | Evidence captured after audit_at must be ignored (case_27, audit moved to 09-16) | FAIL (runner bug) | PASS |
| custom_05 | Mirror: audit at 09-17T12:00 makes evidence_02 admissible | FAIL (runner bug, then a wrong expected row) | PASS |
| custom_06 | Similar-email decoy (`g.silva76.jr`) on the same screenshot (case_35) | PASS | PASS |
| custom_07 | Legal hold verified PRESENT while a drifted replica account is deleted (case_511) | PASS | PASS |
| custom_08 | RESTORE_BACKUP recreated `search_4882` before audit_at (case_55, audit 08-25) | PASS | PASS |

## 3. Independent audits (fresh sub-agents with no access to code, docs or output)

| | Audit 1 (20 cases) | Audit 2 (20 new cases) |
|---|---|---|
| Strata | timing accounts, screenshots/PDF, historical email, COMPLETE, flows/backups, NOT_APPLICABLE, legal hold | legal holds, tombstones, exports, support attachment/message, replica drift, restore, multi-request users, evidence at audit_at, historical email, random |
| Status agreement, first comparison | 18/20 | 20/20 |
| Status agreement, final | **20/20** | **20/20** |
| Exact rows (first comparison → final) | 10 → **14** | 12 → **14** |
| Evidence agreement on shared steps (final) | 92/96 | 106/110 |
| Pipeline bugs found and fixed | 4 (support redaction, snapshot record time, page-level screenshot ids, chain fit) | 1 (support attachment vs message) |

The remaining disagreements are either known ambiguity (account ties, open policy actions, the D16 episode scope, the D17 absence-proof convention) or auditor conventions that contradict the samples. Tables: `independent_audit/comparison.md`, `independent_audit_2/comparison.md`.

## 4. Timing assignment (identifier-free accounts and policy actions)

All 550 requests are solved jointly (D15). Hold-outs hide identifier links and check what timing recovers. They are leakage-free (`holdout_all.py`): the hidden request's user is scrubbed from the owner map and from the same user's other links.

| Account assignment (246 truths) | Leave-one-out | Hide-all |
|---|---|---|
| Timing only | 0.911 | 0.740 |
| + foreign-user exclusion | 0.915 | 0.744 |
| + co-location | 0.923 | 0.768 |
| foreign + owners + co-location | 0.923 | 0.768 |
| **Adopted: + timing signature (D43)** | **0.992** | **0.976** |

**D43 2-fold cross-validation** (`signature_cv.py`): the residue set is learned on one random half of the 246 pinned accounts and tested by leakage-free leave-one-out on the other half.

| Seed | Learn → test | LOO on held-out fold |
|---|---|---|
| 20261025 | A → B | 0.984 |
| 20261025 | B → A | 1.000 |
| 7 | A → B | 0.984 |
| 7 | B → A | 0.992 |

All four folds are ≥ 0.95 (the adoption bar).

| Policy actions (121 truths) | Correct | Wrong | Unassigned |
|---|---|---|---|
| Unique only | 9 | 0 | 112 |
| + chain, all-anchor fit | 103 | 0 | 18 |
| chain (adjacent fit, D37) + prefix + tombstone exclusivity | 109 | 0 | 12 |
| **Adopted: + policy-action signature (D46)** | **120** | **0** | **1** |

**Ambiguity risk (D15):** after D43, 28 account slots stay ambiguous (was 100) and 14 are broken deterministically (was 55). 0 can flip a deletion_status: every candidate is unobserved or ABSENT. A wrong pick changes one step's resource_id.

**Tie-break for open policy actions:**
* Deterministic lowest-id pick: right 0 of 4 on the hold-out → rejected (D30).
* After D46 only 10 actions stay open. "Fewest other resources" (D39) assigns 8 of them; matched hold-out 1/1, samples 28 → 29. 2 actions stay unassigned.

## 5. Failures found and fixes made (chronological)

| Found by | Failure | Fix |
|---|---|---|
| Sample grading | Analytics/policy actions mis-assigned in batches that include people with no request | One resource per prefix per request (D24); tombstone exclusivity (D25); owners from all 850 identities (D26) |
| Hold-out | First policy-tie measurement leaked owner identity (121/121) | Scrubbed owners; all hold-outs re-run leakage-free (D31) |
| Live LLM runs | Limiter deadlock (image token estimate > TPM); 8192 max_tokens caused 429s; Gemini free tier is 20 requests/day | Estimate cap, max_tokens 1500 (largest output 1129), fail fast on daily quota, pinned Groq vision (D33, D35) |
| Vision fixtures | Truncated misreads passed substring corroboration; a misread row email tied a decoy row to the subject | Whole-token corroboration plus **same-OCR-row** check for row identifiers (D32) |
| Custom cases | Non-English refusals classified as deletion | Multilingual refusal patterns (D36) |
| Custom cases | Grader counted a wrong-status row as exact; variants competed in one solve | Grader and runner fixed |
| Audit 1 | Support row with only `[REDACTED]` text counted as exposed | Message-aware redaction test (D36) |
| Audit 1 | Snapshot-bundle records stamped with the bundle time | Record's own `recorded_at` / `observed_at` (D36, D9) |
| Audit 1 | Page-level ids on a customer screenshot ignored | Observed as the page subject's (D36) |
| Audit 1 | Chain model rejected a real 270 s step | Adjacent-anchor fit (D37): 106 → 109/121, 0 wrong |
| Sample gold (case_04) | Inventory tables used as absence proofs | Inventories excluded (D38) |
| Audit 2 | Schema-mandatory `message_excerpt` read as "message" | Removed as a nature signal (D42) |
| Audit 2 (auditor's observation) | Identifier-free accounts tie-broken by lowest event time | Timing signature (D43): 2-fold CV ≥ 0.984; ties 55 → 14 |
| Measurement after D43 | Anonymized policy actions left open (72) | Policy-action signature (D46): hold-out 109 → 120/121, 0 wrong; open 72 → 10 |
| Invariants (A2) | case_126 finding cited a store that proves absence | Cite the pre-audit deletion activity instead (D47) |

## 6. Known losses and open ambiguity

**Samples (3 non-exact, all unrecoverable from participant data, D29).** Verified: `account_004`, `audit_record_6645` and `audit_record_5569` appear in no evidence file, no `system_activity.csv` row, and no screenshot OCR text.
* case_04 `account_004`: named only by a naming convention.
* case_18 `audit_record_6645` and case_20 `audit_record_5569`: absent from all participant data.

case_23, case_21 and case_25, earlier losses, are now exact (D43, D46, D39).

**Eval:**
* 10 artifact-only (hand-built) cases have no admissible account. Like sample case_04, none has a deletion-worker account activity within ±4 h of reported_at. Each user's account appears only in a 09-15 log captured after the audit, and in later deletion runs.
* 14 account ties are broken deterministically (no status risk).
* 2 policy actions stay unassigned.
* D44 (mixed replica snapshots as absence proofs) was rejected: gold case_04 prefers the dedicated account store.

## 7. Honest limitations

* Most uncertainty is in **identifier-free ownership**. Accounts and anonymized analytics are tied to requests only by the workflow's clock. With the timing signature, hold-outs estimate 99 % (leave-one-out) / 98 % (hide-all) account accuracy, and 2-fold cross-validation confirms ≥ 98 % on held-out data. The eval set itself has no gold.
* The rules were derived from 32 samples and checked against 2 × 20 audited cases. Conventions the samples never exercise (mixed-table absence proofs, legal holds, marketing and object storage) rest on policy text plus audit agreement, not on gold.
* Screenshot reading depends on one model. Its outputs are cached, corroborated and scored 11/11 on linked-resource state, but a different model or prompt can read differently; the final output uses the cached extraction.
* Intent is decided by rules. The LLM only confirms (0 disagreements). A request phrased unlike the dataset's templates relies on the multilingual refusal list.

## 8. Final verification

**Tie search: searched, nothing beats the baseline.** 14 account ties and 2 policy actions remain.
* The residue class is fixed by the follow-up type: pre-report policy successes give {10, 11, 40, 41}; post-report "final result" rows give {21, 22, 51, 52}. In the 7 ties that have own follow-ups, both candidates fall in the same class.
* Offsets relative to reported_at show no further structure.
* In 6 ties the only discriminator is a policy action that was itself assigned from the tie-picked account (circular). A joint-consistency variant could not be measured on held-out data at a meaningful size.
* 1 tie (case_411) has no anchor at all.
* No signal reached the bar of ≥ 0.95 held-out accuracy with 0 wrong.

**Residual audit disagreements by pattern** (both audits, final output):

| Pattern | Audit rows | Eval rows in the same situation | Status |
|---|---|---|---|
| Citation: absence proof for an unobserved account | 7 | 67 | Decided (D17 strict, D44 rejected, gold case_04) |
| Citation: finding cites the newest file (a screenshot) over an older text file | 1 | 2 | Gold rule (latest file; 71/71 on samples) |
| Ambiguous tie (3-way SUCCESS accounts, case_299/301) | 2 | 13 | Known ambiguity, searched above |
| Missing resource: audit_record_3753 (prior episode) | 1 | 2 | Decided (D16) |
| Missing resource: export_1260 in case_33 | 1 | 1 | Decided (D22) |
| Missing resource: account_2373 (case_33/52) | 2 | 33 links exist only after audit | Auditor error (D9) |
| Extra resource | 0 | — | — |

**Fresh-clone check:** a copy without `.venv`, `.cache`, `runs` or `.env`, with a new venv from `code/requirements.txt` and no API keys, reproduces `output.csv` byte-for-byte from `code/llm/cache_snapshot/` (518 rows, 0 contract errors, 0 live calls). `--case case_11 --explain` works. With `--no-snapshot` and no keys, 9 rows differ (case_34, 35, 39, 50, 52, 53, 55, 61 and 63 go INCOMPLETE → COMPLETE, because their screenshot-only findings are lost).

## 9. Bounded investigator agent (`code/agent/investigator.py`)

The investigator runs inside every LLM-enabled run (`main.py`), only on the cases the pipeline flags as uncertain: **30 eval cases**.

| Flag | Cases |
|---|---|
| Account tie | 13 |
| Artifact-only | 10 |
| Candidate of an unassigned policy action | 6 |
| Screenshot with corroboration drops | 4 |
| Citation fallback | 1 |

The flags overlap. The cases are 33, 35, 39, 45, 50, 52, 53, 55, 56, 59, 61, 63, 98, 99, 100, 104, 126, 203, 225, 243, 294, 345, 347, 404, 408, 411, 497, 498, 499 and 527.

**Bounds:**
* Read-only tools only (`search_identifier`, `get_resource`, `list_candidates`, `read_snippet`, `propose`, `stop`).
* Each tool enforces as_of = audit_at itself.
* At most 4 actions per case, 700 max tokens, temperature 0, `reasoning_effort` low.
* pydantic-typed actions and facts.
* Pinned Groq `openai/gpt-oss-120b`; every response cached and snapshotted.

**Final run:**

| Measure | Count |
|---|---|
| Cases investigated | 30 |
| Actions | 119 (live calls on 2026-10-25, ~66.5k tokens, 0 failures; replayed from cache since) |
| Proposals | 2 |
| Accepted | 1 (`already_linked`, redundant) |
| Rejected | 1 (`excluded_prior_episode`) |
| Stopped without a proposal | 28 (5 explicit stops, 23 used the full 4-action budget) |
| Rows changed | **0** |

The first validator version accepted case_33 ← `export_1260`. That resource belongs to user_18's earlier episode (D16 / D22) and is identifier-pinned to case_52. Its "pinned elsewhere" check skipped links of the same user, contradicting the spec, and it was fixed before anything was adopted. Accepted facts would enter `Auditor(extra_links=...)`, the same decision layer as every other link; row changes are reported and adopted only with `--adopt-investigator`.

**Example trajectories** (full records in the per-case trace, `trace["investigator"]`):

*Accepted (redundant), case_45 (artifact-only, screenshot drop on evidence_37):*
1. `search_identifier(+12025550103)` → nothing new
2. `search_identifier(oliver-bennett@gmail.com)`
3. `search_identifier(user_03)`
4. `propose(audit_record_001, user_03, evidence_37)` → validator **accepted_redundant / already_linked**. The record is already identifier-linked, and D24 keeps only the catalog-linked audit_record_003 in scope, so the row is unchanged.

*Rejected, case_33 (artifact-only):*
1. `search_identifier(+46707873930)`
2. `search_identifier(ingrid.dahl@outlook.com)` → export_1260 in evidence_23
3. `propose(export_1260, ingrid.dahl@outlook.com, evidence_23)` → validator **rejected / excluded_prior_episode**. Every link to export_1260 belongs to user_18's earlier request case_52 (D16, D22).

*Stopped with no proposal, case_404 (account tie):* three identifier searches (phone, email, email-hash prefix) found only resources already linked → `stop("All identifiers already linked")`.

## 10. Consistency gate (`code/audit/consistency.py::check`)

Called by `main.py` before every write; on any violation it prints the rows and exits 3 without writing. Checks:
* status ↔ phases;
* finding → remediation → verification chain;
* operation ↔ phase/state;
* operations ↔ the treatment `deletion_rules.csv` requires for the category;
* evidence exists, is dated ≤ audit_at, and observes the resource or proves its absence;
* ordering and step_no;
* no resource in two users' rows.

Final output: **518 rows, 0 violations.** `code/evaluation/test_consistency.py` feeds 14 deliberately broken rows (3 status, 2 chain, op/state, treatment, 4 evidence, 2 order, cross-user) and the gate blocks **14/14**; valid rows pass.

The gate also caught real defects during the robustness tests: COMPLETE rows that could no longer cite proof once evidence was missing or corrupted. The pipeline now answers those NOT_APPLICABLE for human review instead.

## 11. Robustness (`code/evaluation/robustness/run_robustness.py`)

Each test mutates a copy of `dataset/` and runs the full pipeline offline (cache and snapshot only, 0 live calls). **12/12 pass.**

| Test | Result | Detail |
|---|---|---|
| malformed_timestamp | PASS | exit 0, valid True; case_57 -> NOT_APPLICABLE (audit_at falls back to reported_at); case_58 non-canonical time unchanged row: True |
| duplicate_case_id | PASS | exit 2, output written: False, message: INPUT ERROR (output not written): duplicate case_id in requests: ['case_33'] |
| email_matches_no_user | PASS | exit 0, valid True; case_57 -> NOT_APPLICABLE (never auto-COMPLETE) |
| empty_request_text | PASS | exit 0, valid True; empty -> NOT_APPLICABLE, whitespace -> NOT_APPLICABLE |
| mixed_case_padded_email | PASS | exit 0, valid True; rows identical to baseline: True |
| unknown_rule_category | PASS | exit 0, valid True; unknown action -> DELETE fallback reported; all rows identical: True |
| missing_evidence_file | PASS | exit 0, valid True, rows changed 10; --strict aborts: exit 2, output written False |
| corrupted_sql | PASS | exit 0, valid True, rows changed 10 (file skipped, reported) |
| injection_request_text | PASS | exit 0, valid True; statuses unchanged: True |
| injection_evidence_file | PASS | exit 0, valid True; case_43 (account_8681) status INCOMPLETE -> INCOMPLETE |
| human_review_queue | PASS | exit 0, valid True; queue 4 rows ['UNPROVABLE_COMPLETE', 'UNRESOLVED_REQUESTER', 'UNUSABLE_TIMESTAMPS']; all queued rows NOT_APPLICABLE: True |
| injection_screenshot_text | PASS | case_39 (data_flow_1121) status INCOMPLETE -> INCOMPLETE (compliant words must also be read by OCR on the image) |

Documented handling:
* **Unparseable timestamps:** audit_at falls back to reported_at, then requested_at; with none usable, the case goes to human review.
* **Duplicate case_id:** clear error, exit 2, no output.
* **Unknown requester, an unprovable COMPLETE, no linked resources, or no usable timestamp:** NOT_APPLICABLE, never an automatic COMPLETE. Each is also queued in `runs/human_review.csv` with a reason code (D52); 0 of the 518 real rows are affected.
* **Unknown rule category or action:** handled as DELETE, the strictest treatment, and reported.
* **Missing or corrupted evidence:** the file is skipped and reported; `--strict` aborts.
* **Injection:** request text only ever reaches the rules (the LLM label is recorded, not used). Evidence text is data. A screenshot's compliant state words must also be read by OCR on the image.
