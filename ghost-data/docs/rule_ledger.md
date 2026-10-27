# Rule ledger (Phase 1)

This ledger is derived from the hand trace of all 32 samples (`docs/sample_trace.md`) and the dataset profile (`docs/data_profile.md`). "Verified" means every sample is consistent with the rule and no sample contradicts it. Open questions Q1–Q13 were decided on 2026-10-24 (DECISIONS.md D9–D21). Rows below show the final rule.

## A. Decision table

| # | Rule | Evidence from samples | Status |
|---|---|---|---|
| A1 | **Intent first.** A request that does not ask to delete, erase or close the account is `NOT_APPLICABLE` with `[]`. | No sample exercises this. In eval, `case_533`–`case_542` are the only non-deletion texts, and each one is corroborated by a `CLASSIFY_REQUEST … non-deletion` activity. | Verified on eval inputs only |
| A2 | **Identity set** = `user_id`, current email, phone and email_hash, plus every historical email, phone and email_hash from the audit log, plus `sha256(lower(strip(email)))` of each historical email. Emails match case-insensitively and exactly; phones match after stripping non-digits except `+`. | case_10 (old email in support_replica), case_12 (old email **hash** as `correlation_key`), case_29/30 (request email is historical); decoys rejected in case_06 (Berglund/Joana), case_10 (nadia.popescu), case_28 (`smartinez6461.jr`) | Verified |
| A3 | **Linking channels** (any one is enough): (a) identifier in the same record; (b) log `trace_id` join from the `*_started` key to the `*_completed` resource_id; (c) `customer_reference` / `subject_reference` / `subject_selector` / `lookup_key` = user_id; (d) `source_resource_id`; (e) `LINK_RESOURCE_IDENTITY` activity, including document refs such as statement numbers; (f) the case's own deletion-workflow activity batch (see A4). | (b) ev_94/ev_01 throughout; (c) case_26–28 support/backups/flows; (d) case_06 search_001→account_006; (f) case_03, 21, 25–28, 31 | Verified; (f) is solved globally (§D, D15) |
| A4 | **Workflow batch.** The deletion worker's rows for a case sit in [`reported_at` −90 min, `reported_at` +3 h]. The account submission result equals the case's `reported_status` mapped COMPLETE→SUCCESS, PARTIALLY_COMPLETE→PARTIAL, NOT_STARTED→FAILED. Ids that are identity-linked to another case are eliminated. | Result↔status holds 22/22 generated samples. Unique-or-eliminated assignment is correct for case_03, 18, 20, 21, 22, 25, 27, 30, 31, 32. | Decided D15: global solve, measured in §D |
| A5 | **Episode scope.** A resource belongs to a case only if at least one of its subject links has an event time ≥ `requested_at`, or the resource is in the case's workflow batch. Links that exist only before `requested_at` belong to an earlier deletion episode. | Excludes object_19 (case_07, links 02-13), account_6523 and tombstone_17 (case_17, links 03-10). Every expected resource passes. | **Revised D16**: exclude only when the user has prior-episode evidence (earlier request, earlier deletion-worker action, earlier tombstone/LINK). Eval effect in §D |
| A6 | **Time.** Only observations with event time ≤ `audit_at` (inclusive) decide state. The event time is the log line timestamp, the inner snapshot `captured_at`, `observed_at`, or `recorded_at`, falling back to the manifest `captured_at`. Later data is ignored even when its file is earlier, and vice versa. | case_27 (ev_02 inner 09-17 ACTIVE ignored), case_26 (older 09-09 ACTIVE superseded by 09-15 404), case_31 (ev_51 at exactly audit_at is used) | Decided D9: a post-capture file may only *discover* (record time ≤ audit_at); never state when a ≤ audit_at file observes the resource; never cited |
| A7 | **State = latest observation ≤ audit_at.** Activity `reported_result` and `reported_status` never establish state. A finding requires positive evidence (present, identifiers exposed, enabled, restorable). A FAILED or PARTIAL claim on its own does not create a finding. | Claims refuted: case_05 (SUCCESS but present), case_16 (search SUCCESS but present), case_24 (SUCCESS ×2 but present), case_25/26 (NOT_STARTED and FAILED but 404 → COMPLETE), case_02 (PARTIAL but absent). case_09 account_3960 FAILED → no finding. | Verified |
| A8 | **Replica drift.** Within one file, the resource is PRESENT if any table or copy (primary, read, reporting, regional, failover) still holds it. The step uses the logical `system_id` and the same `resource_id`, never a replica-specific id. | case_05 (failover-only → account_service account_005), case_16 (account_994 read-replica only) | Verified; D11: latest event time wins across files |
| A9 | **Category → required action** (`deletion_rules.csv`): USER_PROFILE (account) DELETE; SEARCH_INDEX_ENTRY DELETE; USER_EXPORT DELETE; SUPPORT_ATTACHMENT DELETE; SUPPORT_MESSAGE REDACT; ANALYTICS_PROFILE ANONYMIZE; contact or marketing lookup keyed by email/hash with opt-in (SECONDARY_IDENTIFIER / MARKETING_PROFILE) DELETE; SECURITY_AUDIT_LOG RETAIN; BACKUP_ARCHIVE RETAIN with restore blocked; ACCOUNT_TOMBSTONE REDACT; DATA_FLOW (not a rule category) disable. | Every sample step. case_12 analytics_9914 (contact_lookup, opt-in, old hash) → DELETE. case_13 support_4218 typed SUPPORT_ATTACHMENT → DELETE. case_07 support_001 (customer-uploaded PDF) → DELETE. case_10 support_002 (legacy contact index) → DELETE. | Verified |
| A10 | **Category signals**, in priority order: explicit `record_type`; record nature (`attachment_pii_scan`/file vs `message_pii_scan`/`message_text`; `contact_lookup … email_opt_in`; `restore_*`; `connector_*`); then the deletion worker's attempted op for that resource (DELETE / REDACT / ANONYMIZE) as a tie-break. The tie-break must agree with policy. | The deletion-worker op agrees with the expected final treatment on 70/70 sample verification steps whose resource has such an activity (0 disagreements). | Verified |
| A11 | **Compliance tests** at the latest observation: DELETE-class compliant ⇔ absent (404, `rows=0`, NOT_FOUND, `hits.total=0`, or omitted from a full store snapshot). REDACT/ANONYMIZE compliant ⇔ every direct identifier (name, email, phone, email_hash; user_id too for ANONYMIZE) is null or `[REDACTED]`, or result REDACTED/ANONYMIZED. Tombstone compliant ⇔ only `subject_reference` remains. Flow compliant ⇔ `enabled=false`/DISABLED. Backup compliant ⇔ `restore_allowed=false`/BLOCKED. Audit or legal record compliant ⇔ present. | case_26/27/28 (support with customer_reference only = REDACTED), case_31 vs case_32 (tombstone), case_04/21/25 (analytics nulls) | Verified |
| A12 | **Finding op**: DELETE-class non-compliant → `REPORT_REMAINING_RECORD`; REDACT/ANONYMIZE-class (incl. tombstone) non-compliant → `REPORT_EXPOSED_PII`; enabled flow → `REPORT_ACTIVE_DATA_FLOW`; restorable backup → `REPORT_RESTORE_RISK`. All `PRESENT`/`ACTIVE`. | case_11/18/29/30 (all four), case_32 (tombstone EXPOSED), case_12 (analytics but DELETE-class → REMAINING) | Verified |
| A13 | **Remediation** = one step per finding: DISABLE_DATA_FLOW, BLOCK_RESTORE, DELETE_RECORD, REDACT_RECORD, ANONYMIZE_RECORD. Retained records (audit, legal, backup archive) are never deleted. | all INCOMPLETE samples | Verified |
| A14 | **Verification** = the required final state of **every** in-scope resource, compliant or not: account ABSENT, deleted classes ABSENT, audit/legal PRESENT, REDACTED, ANONYMIZED, flow DISABLED, backup BLOCKED. The account is always included even when already absent and unobserved. Backups get only VERIFY_RESTORE_BLOCKED, never VERIFY_RECORD_PRESENT. | all 32 | Verified |
| A15 | **Status**: `COMPLETE` ⇔ zero findings, giving verification-only steps that all carry evidence. Otherwise `INCOMPLETE`. | all 32 | Verified |
| A16 | **Ordering**: phase, then operation index in the allowed-operations table, then `system_id` asc, then `resource_id` asc. step_no is 1..n. | 32/32 exact | Verified; D13: resource_id compares as a plain string |
| A17 | **Citation**: FINDING and COMPLETE-case VERIFICATION steps cite the file with the latest manifest `captured_at` ≤ `audit_at` that observes the resource_id, inclusive. REMEDIATION and INCOMPLETE-case VERIFICATION use `""`. | 71/71 directly observed; ev_93 chosen over ev_06 in case_09 (ev_06 captured after audit although its line is earlier) | Verified |
| A18 | **Absence by omission**: when an account is never observed directly, cite the latest file ≤ audit_at that contains a **complete** account-store table or collection that lacks it (e.g., ev_54 `customer_profiles_primary` + replica; ev_51 `account_service/customer_profiles: []`). | 4/4 (case_01, 02, 04 → ev_54; case_31 → ev_51) | Decided D17: only full account-store tables or collections |
| A19 | **Baseline resources**: every case has one account_service account. Every generated case has one security audit_record (`subject_reference` = user_id). | 32/32 accounts; 23/23 generated cases have an audit record (2 of them unobservable) | Verified |
| A20 | **Only participant data**: `sample_output.csv` is used for grading only, never for lookup. | — | Design rule |

## B. Ordering reference

```
FINDING:      REPORT_REMAINING_RECORD < REPORT_EXPOSED_PII < REPORT_ACTIVE_DATA_FLOW < REPORT_RESTORE_RISK
REMEDIATION:  DISABLE_DATA_FLOW < BLOCK_RESTORE < DELETE_RECORD < REDACT_RECORD < ANONYMIZE_RECORD
VERIFICATION: VERIFY_RECORD_ABSENT < VERIFY_RECORD_PRESENT < VERIFY_RECORD_REDACTED < VERIFY_RECORD_ANONYMIZED
              < VERIFY_DATA_FLOW_DISABLED < VERIFY_RESTORE_BLOCKED
then system_id asc, then resource_id asc
```

System ids seen in expected output: `account_service, search_service, support_platform, export_service, analytics_platform, security_service, integration_service, backup_service, identity_service`.

## C. Categories the samples never exercise — proposed handling

| Category / object | Where it appears | Proposed handling (from policy text) |
|---|---|---|
| LEGAL_HOLD_RECORD (r13 RETAIN) — **decided D18** | `legal_hold_N` rows in ev_21/24/36/40/43/45/47 (subjects user_218, 259, 388, 430, 458, 471, 503, 509), `retained_order_records`, `retained_until` 2033 | Treat like an audit record: `VERIFY_RECORD_PRESENT` (system `legal_service`), never a finding, never remediated, as long as `retained_until` > audit_at. The legal register PDF (ev_27) lists orders for user_08/12/17/21/24/29 with no resource_id, so it produces **no step**; samples 08/12/17/21/24/29 confirm this. |
| MARKETING_PROFILE (r10 DELETE) | `marketing_001` (ev_60 PNG + act_4008 → kwame-mensah / user_09 / eval case_63) | REMAINING → DELETE → VERIFY_ABSENT, system `marketing_platform`. Caveat: the screenshot shows a different person (sj.park@naver.com); see Q11. |
| SECONDARY_IDENTIFIER (r02 DELETE) | Records keyed only by an old email/phone/hash, e.g. analytics `contact_lookup` (case_12) | DELETE. In practice case_12 is this category, filed under `analytics_platform`. |
| CACHE_ENTRY (r04 DELETE) | `cache_N` only in REFRESH_CACHE activity, no identifiers, no evidence | No step: it cannot be linked or observed. |
| EMAIL_DELIVERY_LOG (r11 REDACT) | `email_delivery_N` only in SEND_EMAIL activity | No step, for the same reason. |
| PRODUCT_TELEMETRY (r08 ANONYMIZE) | Not found in any participant file | No step. |
| Object storage attachment | `object_19` delivery_confirmation_19.pdf (ev_46 + act_4007 "belongs only to priya.raman") → eval case_61 | SUPPORT_ATTACHMENT (r06) → REMAINING / DELETE / VERIFY_ABSENT, system `object_storage` (from activity target_system). |
| Export from a document ref | `export_1842` = statement NST-2026-0718-1842 (ev_18 PDF + act_4009), `export_1260` (ev_23 PNG + act_4004) → user_18 / cases 33, 52 | USER_EXPORT DELETE. The PDF only proves the export exists if the statement is within the episode; use the LINK activity to map ref → resource_id. |
| Restore / sync recreation — **decided D10** (only RESTORE_BACKUP "repopulated" or an evidenced-enabled flow) | `RESTORE_BACKUP backup_9287 → search_4882` (case_55 window); 31× `Active connector repopulated downstream resource search_N` from `data_flow_N` | If the recreation event is ≤ audit_at and after the deletion: the downstream resource is PRESENT (REMAINING), the source is an active risk (REPORT_RESTORE_RISK / REPORT_ACTIVE_DATA_FLOW), and remediation disables or blocks the source before deleting the copy. See Q2. |
| Security audit record outside retention | Every observed `retention_until` is > 2031 | RETAIN only while within retention. Expired → not covered by r12, so we propose DELETE. Not expected to occur. |
| Account tombstone with PII in another store | ev_52 failover holds 16 tombstones | Same as case_32, per copy (A8). |

## D. Workflow timing assignment (D15) — measurements

Code: `code/audit/workflow.py` (solver), `code/audit/signals.py` (account tie-breaks), `code/evaluation/timing_holdout.py` (all numbers below; deterministic, about 1 min).

**Offsets relative to `reported_at`**, measured on identifier-linked (pinned) actions:

| Action (deletion_worker) | Offset (h) | Notes |
|---|---|---|
| `DELETE_RECORD` account "submitted primary profile" | −1.25 … −0.73 (median −0.99) | result ↔ reported_status: COMPLETE↔SUCCESS 133, PARTIALLY_COMPLETE↔PARTIAL 69, NOT_STARTED↔FAILED 50 (4 outliers were activity on other episodes' resources). Solver window −1.40 … −0.50 |
| "Policy action completed" RETAIN legal_hold / REDACT tombstone | −0.48 … −0.47 | chain position 0 (ANONYMIZE analytics shares it) |
| … REDACT support | −0.37 … −0.25 | the chain shifts when earlier positions are absent |
| … DISABLE_DATA_FLOW | −0.25 … −0.12 | |
| … BLOCK_RESTORE | −0.13 … −0.05 | |
| "received the final result" / CHECK_* (failures) | +0.6 … +2.2 | about 18.5 / 19.5 min apart; **not timing-assigned** (the window spans dozens of requests, and these resources still carry identifiers) |

* **Policy chain:** within one request, consecutive policy actions are exactly 390 s or 449 s apart, alternating (e.g. case_26: −1716, −1267, −877, −428 s). Other requests at the same `reported_at` have different jitter.
* **Account submission times** carry no request-specific structure. Pairwise order versus case_id, requested_at, email and user_id is 79/79, 77/81, 84/74 and 81/77, i.e. a coin flip.

**Solver** (global, over all 550 requests):
* Accounts are request-centric (one per request). The candidate window and result↔status give each request a domain. Identifier-linked accounts are pinned. Unit propagation and elimination then run to a fixpoint. A resource identifier-linked only to another user is a hard exclusion.
* Policy actions are activity-centric: each action has exactly one owner, and a request may have none. An action is assigned when it has a single candidate, or when exactly one candidate's existing chain fits within ±3 s.

**Account slot status after solve** (timing + foreign exclusion + co-location):

| | pinned | unique | signal | ambiguous | infeasible | no event |
|---|---|---|---|---|---|---|
| all 550 | 251 | 166 | 3 | 101 | 3 | 26 |
| eval 518 | 238 | 157 | 2 | 100 | 3 | 18 |

**Account hold-out** (251 requests whose account is identifier-linked):

| Config | leave-one-out | hide-all (lower bound) |
|---|---|---|
| timing only | 227/251 = 0.904 | 185/251 = 0.737 |
| + foreign-user exclusion | 228/251 = 0.908 | 186/251 = 0.741 |
| + co-location (adopted) | **230/251 = 0.916** | **192/251 = 0.765** |

Precision by decision type (hide-all, adopted config): unique 154/189 = 0.81 (errors are cascades from hidden neighbours), co-location signal 15/17 = 0.88, deterministic tie 23/44 = 0.52.

**Policy-action hold-out** (121 identifier-linked policy actions; one pin removed at a time): **103 correct, 0 wrong, 18 left unassigned**. The chain signal was 97/97. "Chain elimination" (all other candidates misfit) was wrong 2/2 and is not used. Full solve: 121 pinned, 18 unique, 33 by chain, 76 unresolved (left unassigned and flagged).

**Signals tried and rejected:** account order versus case_id / requested_at / email / user_id (no signal, above); chain elimination (2/2 wrong).

**Ties:** a still-open account domain takes the earliest-timestamp candidate (`how="tie"`). This is logged in the trace and flagged in evaluation_report.md.

## E. Episode scope (D16) — eval effect

`code/evaluation/episode_scope.py`. Of 1,395 eval (request, resource) links discoverable at or before audit_at, rule A (Phase 1) and rule B (revised) both keep 1,392 and drop the same 3:
* case_33 `export_1260`. Prior request case_52 (same user_18, audit 06-19) covers every link (06-18 screenshot and LINK). **This conflicts with the example given for D16**, so it is flagged in DECISIONS.
* case_34 `support_9538`, `audit_record_3753`. These are prior-episode deletion-worker actions on 08-28 (account_5762 FAILED, support_9538 FAILED, with no request in the dataset). The 09-26 account-console screenshot (evidence_04) names support_9538 inside case_34's window, so it is kept once image text is available.

## F. Phase 3 pipeline results (2026-10-25)

`python code/main.py --samples` then `python code/evaluation/grade.py --pred runs/sample_output.csv --expect dataset/sample_deletion_requests.csv`

| Metric (32 samples) | Value |
|---|---|
| status accuracy | 1.000 |
| step precision / recall / F1 | 0.995 / 0.969 / 0.982 |
| evidence accuracy (matched steps) | 1.000 |
| exact row match | 26/32 |

The 6 rows that do not match exactly, and why no principled rule fixes them:

| Case | Miss | Reason |
|---|---|---|
| case_04 | VERIFY_ABSENT account_004 | No activity or evidence names account_004; only the `account_00N` naming convention would produce it (a case-family shortcut) |
| case_18 | VERIFY_PRESENT audit_record_6645 | The id appears nowhere in participant data |
| case_20 | VERIFY_PRESENT audit_record_5569 | The id appears nowhere in participant data |
| case_23 | account_7015 instead of account_410 | 3-way FAILED-account tie (410 / 7015 / 9155; none observed anywhere), broken deterministically |
| case_21 | missing analytics_5335 | Policy action open between case_21 and case_23 (no chain anchor; D15 never tie-picks policy actions) |
| case_25 | missing analytics_1895 | Policy action open between case_24 and case_25 (same) |

**Account ambiguity risk** (`code/evaluation/ambiguity_report.py`): 100 open account slots (99 eval). 0 can flip deletion_status. In 95, every candidate is unobserved. In the other 5, all candidates are ABSENT or unobserved, and the only nearby evidence (evidence_80 lookups) carries no identifiers. A wrong pick changes the cited evidence in 2 COMPLETE eval cases (case_225, case_226) and only one step's resource_id in the other 97.

## G. Corrected hold-outs (leakage-free, 2026-10-25)

`python code/evaluation/holdout_all.py`. Protocol: the hidden request loses all its identifier pins. Its user is removed from the owner map (D26), and the truth resource is removed from the pins of the same user's other requests. Co-location sees only the hidden request's own non-account links. The pins, owners and filled slots are the pipeline's own (episode-scoped, D16), so there are 226 account truths instead of the 251 in §D, which used unscoped pins.

**Was §D leaky?** No. `timing_holdout.py` never passed the full owner map (only non-requester owners) and hid every pin of the hidden request. The leak was only in the first `policy_ties.py` run, which passed `A.owners` and gave 121/121; that was fixed before any decision used it. The numbers below differ from §D because the pins are now episode-scoped.

| Account config | §D LOO | §D hide-all | corrected LOO | corrected hide-all | Δ vs timing (LOO / hide-all) |
|---|---|---|---|---|---|
| timing only | 0.904 | 0.737 | 0.889 (201/226) | 0.748 (169/226) | — |
| + foreign (requesters, D15) | 0.908 | 0.741 | 0.894 | 0.752 | +0.004 / +0.004 |
| + owners, all users (D26) | 0.916* | 0.765* | 0.889 | 0.748 | **+0.000 / +0.000** |
| + co-location | 0.916* | 0.765* | 0.912 | 0.779 | +0.022 / +0.031 |
| + filled slots | — | — | 0.889 | 0.748 | **+0.000 / +0.000** |
| adopted (all four) | — | — | **0.916 (207/226)** | **0.783 (177/226)** | +0.027 / +0.035 |

\* §D measured these cumulatively (foreign + co-location, then + non-requester owners), not separately.

| Policy config | §D | corrected |
|---|---|---|
| unique only | — | 9/121, 0 wrong |
| + chain | 103/121 (chain 97/97) | 103/121, 0 wrong |
| + prefix (D24) | 106/121 (with tombstone) | 21/121 alone, 0 wrong |
| + tombstone (D25) | — | 9/121 alone; chain+prefix 105 → 106 with it |
| adopted (all three) | 106/121, 0 wrong | **106/121, 0 wrong** |

**Flags:**
* **Owners (D26):** no gain on the hold-out; the samples are unchanged with or without it; it changes 8 eval assignments. Its target (resources of people who filed no request) cannot occur as hold-out truth.
* **Filled slots:** no gain anywhere. Removing it changes 0 assignments in the full solve.
* **Tombstone (D25):** no gain alone; +1 in combination (105 → 106); 0 eval assignment changes.

Sample ablation: adopted 26/32 exact. Without prefix, 24; without chain, 22; without tombstone or owners, 26.

## H. Phase 5: custom cases and independent audit (2026-10-25)

**Custom cases** (`code/evaluation/custom_cases/`, `python code/evaluation/custom_eval.py`). Each case replaces one request and gets its own global solve. 8/8 exact, status 1.000, step F1 1.000.

| Case | Targets | First run | Final |
|---|---|---|---|
| custom_01 | French non-deletion containing "suppression" | FAIL (rules said deletion) | PASS after D36 multilingual refusals |
| custom_02 | Japanese non-deletion containing 削除 | FAIL | PASS (same fix) |
| custom_03 | Historical email only, odd casing and spaces (case_12) | PASS | PASS |
| custom_04 | Evidence captured after audit_at ignored (case_27, audit 09-16T00:00) | FAIL (runner bug: two variants of case_27 in one solve) | PASS |
| custom_05 | Mirror: audit 09-17T12:00 makes evidence_02 admissible | FAIL (runner bug; then my expected row was wrong on support_4707) | PASS (expected row corrected from raw evidence) |
| custom_06 | Similar-email decoy search_1874 on the same screenshot (case_35) | PASS | PASS |
| custom_07 | Legal hold kept and verified PRESENT (case_511) | PASS | PASS |
| custom_08 | RESTORE_BACKUP recreated search_4882 before audit_at (case_55, audit 08-25) | PASS | PASS |

**Independent audit:** see `code/evaluation/independent_audit/comparison.md`. Status agreement went from 18/20 to 20/20. 4 pipeline bugs were found and fixed (D36, D37); the remaining differences are known ambiguity or auditor convention.

**Determinism:** two full runs from cache produce byte-identical `output.csv` and traces, with 0 live LLM calls.
