# Interview sheet: Ghost Data Still Exists

## Where each rule or guardrail lives

| Rule / guardrail | Code (file::function) | Decision |
|---|---|---|
| Identity: current + historical emails, phones, email hashes | `code/audit/identity.py::IdentityResolver.resolve` | A2 |
| Evidence parsing into typed observations (SQL, JSON, 6 log formats, TXT, PDF, screenshots); per-file error isolation | `code/audit/evidence.py::EvidenceIndex.__init__`, `::_load_file` | D9, D36 |
| Identifier linking, catalog LINK → artifact | `code/audit/linking.py::Linker.linked_resource_times`, `::attach_artifacts` | A3, D27 |
| Episode scope (prior deletion episode) | `code/audit/pipeline.py::Auditor._identifier_scope` | D16 |
| One resource per prefix (screenshot-only duplicates dropped) | `code/audit/pipeline.py::Auditor._weak_duplicates` | D24, D34 |
| Global timing solve over all 550 requests | `code/audit/workflow.py::WorkflowSolver.solve`, `::_solve_request_centric`, `::_solve_policy` | D15 |
| Foreign-owner exclusion | `code/audit/workflow.py::WorkflowSolver._foreign`, `code/audit/pipeline.py::Auditor._resource_owners` | D26 |
| D43 account timing signature | `code/audit/signals.py::FollowupSignature.pick` | D43 |
| D46 policy-action signature, chain fit, D39 residue rule | `code/audit/workflow.py::WorkflowSolver._solve_policy`, `::_chain_fits` | D46, D37, D39 |
| State at audit_at (latest admissible observation, D9 post-capture rule) | `code/audit/state.py::StateResolver.at`, `::verdict` | A6–A8, D9, D11 |
| D17 absence proof (dedicated account stores only, no inventories) | `code/audit/state.py::StateResolver.omission_proof` | D17, D38, D44 |
| Policy mapping (category → treatment, unknown rule → DELETE) | `code/audit/policy.py::Policy.category`, `::treatment` | A9, D20, D42 |
| Steps, ordering, citation, activity-citation fallback | `code/audit/pipeline.py::Auditor.audit` | A13–A17, D47 |
| Intent rules (6 languages, explicit refusals win) | `code/audit/intent.py::classify` | D28, D36 |
| OCR corroboration (whole token, same OCR row) | `code/llm/vision.py::Corroborator.check`, `::same_row`, `::extract` | D32 |
| Screenshot state (compliant words need OCR) | `code/audit/state.py::screenshot_verdict` | D32, hardening |
| Investigator tools (as_of enforced) and validator | `code/agent/investigator.py::Investigator.search_identifier` / `get_resource` / `list_candidates` / `read_snippet`, `::validate` | final pass |
| Consistency gate | `code/audit/consistency.py::check` (called in `code/main.py::main` before writing) | final pass |
| Strict mode, snapshot, offline mode | `code/main.py::main`, `code/llm/client.py::complete` (`STRICT`, `SNAPSHOT`, `LLM_OFFLINE`) | D45 |
| Input hardening (timestamps, duplicate ids, unknown requester) | `code/audit/data.py::normalize_requests`, `::Dataset.load`; `code/audit/pipeline.py::Auditor.audit` | final pass |

## Production monitoring signals

On any alert the default is conservative: send the case to human review, keep INCOMPLETE, never auto-COMPLETE.

| Signal | Value on this dataset | Alert threshold | Conservative fallback |
|---|---|---|---|
| Share of COMPLETE by reported_status | COMPLETE-reported 35.8 %, PARTIAL 23.7 %, NOT_STARTED 22.3 % | Any bucket moves > 10 points between runs, or NOT_STARTED → COMPLETE > 30 % | Review a sample of the new COMPLETE rows before release |
| Account tie rate | 13 / 508 deletion cases (2.6 %) | > 5 % | Ties keep their deterministic pick, but the cases go to review; a tie never changes status |
| Corroboration drop rate (vision) | 5 misread tokens of 48 (≈ 10 %); 0 on linked resources | > 20 % per image, or any drop on a catalog-linked resource | Treat that screenshot as unread (state unknown → no auto-COMPLETE) |
| Fallback and 429 rate | 0 fallbacks; 36 of 130 logged attempts were 429s (all retried or aborted strictly) | Any fallback in strict mode; 429 > 30 % of attempts | Strict abort; keep the last good output |
| Cases with zero linked resources | 0 | > 0 | NOT_APPLICABLE + human review (never COMPLETE) |
| Consistency-gate violations | 0 | > 0 | Output not written; exit 3 |
| Investigator acceptance | see evaluation_report §9 | Any accepted *new* fact, or acceptance > 20 % | Facts are reported, not adopted, without `--adopt-investigator` and a human diff review |
| Evidence load errors | 0 | > 0 | Strict abort; non-strict: affected COMPLETE rows become human-review |

## Known edge cases and untested branches

* **Identifier-free ties:** 14 account ties and 2 unassigned policy actions cannot be resolved from evidence. They affect resource ids only, never status.
* **D39 "fewest other resources"** assigns 8 policy actions on weak evidence (matched hold-out 1/1).
* **Hand-built (artifact-only) cases** have no admissible account; screenshots are their only evidence, so their state depends on the vision read.
* **Conventions no sample exercises:** legal holds, marketing and object storage, mixed-replica absence proofs (rejected). These rest on policy text and audit agreement.
* **Untested branches:**
  * the Gemini vision fallback, unused in the final run;
  * the recreation path through an *enabled data flow*, with no instance before an audit_at;
  * the expired-retention security record;
  * multi-page PDFs;
  * non-UTC timestamps in evidence (normalized, but no sample uses them).
* **Unknown requester or unparseable timestamps:** answered NOT_APPLICABLE for human review. That is contract-valid, but semantically a review flag, not a refusal.

## Five likely interview questions

1. **Why not a free-form agent?** Because the hard part is a joint constraint problem over 550 requests that a per-case agent cannot see, and because the output must be reproducible and auditable. The pipeline solves all requests together, every decision is a named rule with a measurement, and the only autonomy is a bounded investigator whose facts must pass a deterministic validator.
2. **How do you know identifier-free attributions are right?** From leakage-free hold-outs. Identifier-linked truths are hidden and re-derived: accounts score 0.992 leave-one-out and 0.976 hide-all (2-fold CV ≥ 0.984 on every fold), and policy actions 120/121 with 0 wrong. Every remaining tie is unobserved or absent, so a wrong pick cannot change a status.
3. **What stops the model from changing an answer?** The model never writes a step or a status. Intent is decided by rules (the model's label is recorded; 0 disagreements). Screenshot facts must be read by OCR on the same row. Investigator facts are re-verified by `validate`. The consistency gate refuses to write any inconsistent row.
4. **What happens when data is missing or hostile?** The case degrades to a conservative answer, never an automatic COMPLETE. Unreadable evidence is skipped and reported (strict mode aborts). An unprovable COMPLETE or an unknown requester becomes NOT_APPLICABLE for human review. Injected instructions in requests, evidence or screenshots changed no status in the 11-test robustness suite.
5. **Is the output reproducible?** Yes. Every LLM response is cached and the exact responses behind `output.csv` are committed (`code/llm/cache_snapshot/`), so a fresh clone with no API keys reproduces the file byte-for-byte, and `--strict` guarantees that a live run either uses only the pinned models or writes nothing.

## Two more answers

**Why do some cases come back NOT_APPLICABLE when deletion was requested?** Only when nothing can be proven: an unknown requester, a COMPLETE that cannot cite proof, nothing linked, or no usable timestamp. The output contract has no review status. INCOMPLETE requires evidenced findings, and COMPLETE requires proof for every verification. NOT_APPLICABLE is therefore the only value that authorises no destructive action and claims no compliance. Every such case is written to `runs/human_review.csv` with a reason code and what was missing (`code/main.py::main`, reasons set in `code/audit/pipeline.py::Auditor.audit`). This affects 0 of the 518 real rows.

**"23 of 28 investigations hit the action limit." Is the agent failing?** No. These are the cases the hold-outs already showed cannot be resolved from the evidence:
* identifier-free account ties where every candidate fits the timing signature;
* artifact-only cases with no admissible account;
* policy actions whose only discriminator is circular.
The 4-action cap is what keeps the agent bounded: an uncertain case costs at most 4 cheap read-only calls and then the deterministic answer stands. The validator also did its job on the one wrong proposal: export_1260 → case_33, a resource from the same user's earlier episode (D16 / D22), was rejected as `excluded_prior_episode`.
