# DECISIONS

Each entry covers one non-obvious rule and cites the sample cases that justify it. Full derivations are in `docs/rule_ledger.md` (rule ids A1–A20); unresolved items are in `docs/open_questions.md`.

## D1 — Claims never establish state (A7)
Activity `reported_result` and the request's `reported_status` are ignored for state. Only observations decide. A FAILED claim alone creates no finding.
Samples: case_05, 16, 24 (SUCCESS claimed, record present); case_02, 25, 26 (PARTIAL or NOT_STARTED claimed, record absent → COMPLETE); case_09 (account FAILED, no finding).

## D2 — Any surviving replica copy means PRESENT, reported under the logical system (A8)
Samples: case_05 (failover-only row → `account_service/account_005`); case_16 (read-replica-only row → `account_994`).

## D3 — Treatment follows record nature, not only system (A9, A10)
A contact lookup with opt-in, keyed by an old email hash, is DELETEd even though it lives in `analytics_platform`. Customer-uploaded attachments are DELETEd; messages are REDACTed. The deletion worker's attempted op is a tie-break hint (70/70 agreement).
Samples: case_12, case_07, case_10, case_13 vs case_11/18/19.

## D4 — Verification lists the final state of every in-scope resource (A14)
This includes the already-absent account and the retained security audit record. Backups get only VERIFY_RESTORE_BLOCKED.
Samples: all 32 (e.g., case_06 account_006, case_09 audit_record_4902).

## D5 — Ordering: phase → operation-table index → system_id → resource_id (A16)
Samples: 32/32 exact.

## D6 — Citation = latest file with manifest captured_at ≤ audit_at (inclusive) observing the resource (A17)
Samples: 71/71 directly observed citations, e.g., case_31 cites ev_51 captured exactly at audit_at. case_09 cites ev_93, not ev_06 (ev_06 was captured after audit_at although its line is earlier).

## D7 — Account absence may be proven by omission from a full account-store snapshot (A18)
Samples: case_01, 02, 04 → evidence_54; case_31 → evidence_51.

## D8 — One user can have several deletion episodes; links that predate the request belong to the earlier one (A5)
Samples: case_07 excludes object_19 (Feb); case_17 excludes account_6523 and tombstone_17 (Mar).

## Decisions on open questions (user-approved, 2026-10-24)

### D9 — Q1: post-capture files are for discovery only
A file whose manifest `captured_at` > `audit_at` may be used **only** to discover that a resource belongs to the user, and only when that record's own timestamp (log line ts, inner snapshot `captured_at`, `observed_at`) is ≤ `audit_at`. It never supplies state when a file captured ≤ `audit_at` observes the resource. It is never cited.
Evidence: case_09 (`account_3960`, `audit_record_4902` only in evidence_06, captured 09-18, lines 09-16T08:52–08:57 < audit 09-16T09:00; backup_4978 cites evidence_93, not evidence_06). case_23/24/29/30 audit records only in evidence_02 (captured 09-17, inner snapshot 09-15T12:00 = audit).

### D10 — Q2: only explicit recreation evidence revives a deleted resource
After a deletion, a resource becomes present again only through `RESTORE_BACKUP … repopulated downstream resource X` (activity_3387: backup_9287 → search_4882) or a data flow evidenced as enabled. Routine SYNC_RECORD / INDEX_RECORD / UPDATE_RECORD rows never create findings on their own. Deletion, redaction and disable claims never change state.
Evidence: claims overridden by observations in case_02, 05, 16, 24, 25, 26.

### D11 — Q3: latest event time wins across files; within one file, a surviving copy wins
Evidence: case_05 (failover-only row), case_16 (read-replica-only row), case_26 (09-09 ACTIVE superseded by 09-15 404).

### D12 — Q4: cite files; activity_id only as a fallback; never audit_id
Cite the latest file captured ≤ audit_at that observes the resource. Cite an `activity_id` only for a finding with no file observation (e.g. a RESTORE_BACKUP recreation). Never cite an `audit_id`.
Evidence: no sample cites an activity even where CHECK_* rows exist (case_14 → evidence_03, case_15 → evidence_01).

### D13 — Q5: resource_id sorts as a plain string (lexicographic)
No sample disambiguates (0 same-op, same-system pairs in 32 samples).

### D14 — Q6: flow and backup scope requires an identity link within the episode
State is the latest observation: enabled or restorable → finding; disabled or blocked → verification only. CHECK_DATA_FLOW / CHECK_RESTORE_ACCESS rows corroborate but never create a finding alone.
Evidence: case_15 excludes data_flow_1121 (same screenshot, other user); case_11/15/17/18/20/26–30.

### D15 — Q7: timing links are a global constraint solve over all 550 requests
Every identifier-linked resource is pinned. Unlinked deletion-worker actions are then assigned by time offset from `reported_at` and by reported_status ↔ account result (COMPLETE↔SUCCESS, PARTIALLY_COMPLETE↔PARTIAL, NOT_STARTED↔FAILED), eliminating across every request. A remaining account tie takes the earliest candidate, is logged in the trace, and is flagged in evaluation_report.md. Policy actions are never tie-picked: they are assigned only when unique or chain-fitting. Post-report failure rows are never timing-assigned. Hold-out: accounts 0.916 leave-one-out / 0.765 hide-all; policy actions 103/121 correct, 0 wrong (docs/rule_ledger.md §D).
Evidence: result↔status holds 22/22 generated samples (197/197 identifier-linked accounts, per the user's measurement); timing-only resources in case_03, 18, 20–22, 25–32.

### D16 — Q8 (revised): exclude only when a prior deletion episode exists
A resource linked to the user is excluded only when there is evidence of an **earlier deletion episode** for the same user (an earlier request in the dataset, an earlier deletion-worker cluster, or an earlier tombstone/LINK for that user), and the resource's links all belong to that earlier episode. An earliest link that predates `requested_at` is not, on its own, a reason to exclude.
Evidence: case_07 excludes object_19 (user_07 also has case_61, audit 02-14); case_17 excludes account_6523 and tombstone_17 (user_17 also has case_46, audit 03-11). Measured effect (rule_ledger §E): 3 of 1,395 eval links dropped. **Flag:** case_33's export_1260 is still dropped under this rule, because user_18's earlier request case_52 (audit 06-19) covers all its links (06-18). Kept as implemented pending your confirmation.

### D17 — Q9: omission proof only from full account-store snapshots
Evidence: case_01/02/04 → evidence_54; case_31 → evidence_51.

### D18 — Q10 (revised): legal holds are real resources
Order rows without a resource_id produce no step. `legal_hold_N` resources exist (evidence files plus RETAIN_RECORD activity to legal_service). When linked to the user they are LEGAL_HOLD_RECORD → RETAIN → `VERIFY_RECORD_PRESENT`, system `legal_service`.
Evidence: samples 08/12/17/21/24/29 have legal-register order rows and no legal step; legal_hold rows in evidence_21/24/36/40/43/45/47.

### D19 — Q11: the catalog LINK activity beats a conflicting screenshot identity
`marketing_001` → user_09 (activity_4008), despite the image showing sj.park@naver.com.

### D20 — Q12: category precedence
Explicit `record_type` → record nature (event or collection names) → the deletion worker's attempted op → the system default.
Evidence: case_12, 07, 10, 13 vs 11/18/19; 70/70 op agreement.

### D21 — Q13: tesseract is optional
The pipeline must still run without it. Install status 2026-10-24: the winget install ran out of disk (C: had 0 MB free), so `C:\Program Files\Tesseract-OCR\tesseract.exe` is present but fails to start (exit 53). It must be reinstalled after freeing space.

## Decisions on round-2 questions (user-approved, 2026-10-25)

### D22 — Q14: export_1260 belongs to case_52
D16 stays as written. User_18's earlier request case_52 (audit 06-19) covers every export_1260 link (06-18), so case_33 does not get it; case_33 gets export_1842 (statement NST-2026-0718-1842, LINK activity_4009).

### D23 — Q15: rows in natural case_id order
`output.csv` rows follow the numeric case_id order, the same order as `dataset/deletion_requests.csv` (`case_33` … `case_550`). The validator accepts string or natural order.

## Rules added while building the pipeline (Phase 3, 2026-10-25)

### D24 — One resource per system prefix per request (solver constraint)
A policy action is never assigned to a request that already owns a different resource with the same prefix. Measured: 32/32 gold samples have at most one resource per prefix. 545/550 identifier-linked sets do too; the 5 exceptions have two support records. Fixes case_03 (analytics_9406: case_44 already owns analytics_5589) and case_11 (analytics_9447 belongs to an unrequested person's batch: account_146 / e.lindberg18 at the same reported_at). Policy hold-out went from 103 to 106 correct out of 121, still with 0 wrong.

### D25 — Tombstone requests own no other policy resource
11/11 identifier-linked tombstone requests have no analytics, support, flow, backup or legal resource, and vice versa. A tombstone action is never assigned to a request owning one of those, and vice versa.

### D26 — Resources carrying another person's identifiers are never timing-assigned
This extends D15's foreign-user exclusion to all 850 known users, including people who filed no request. Deletion batches contain such people (e.g. account_146 → e.lindberg18, who has no request). Account hold-out is unchanged at 0.916 / 0.765, with no regression.

### D27 — Catalog LINK rows cite the artifact they follow
Each of the 12 `LINK_RESOURCE_IDENTITY` rows is timestamped exactly 7 min after the capture of one PDF or PNG (e.g. evidence_09 06:00:00 → activity_4001 06:07:01). That file becomes an observation of the linked resource and is the citation. Its state comes only from the artifact's extracted text. An unread screenshot gives *state unknown*, which is treated as compliant and flagged in the trace (9 eval cases until image extraction is wired).

### D28 — Intent rules (rules-only stage)
An explicit refusal or limitation ("not asking for deletion", "do not close", "without removing", "marketing lists only", …) → NOT_APPLICABLE. Otherwise a deletion phrase in any of the 6 languages → deletion. Result: exactly case_533–542 are NOT_APPLICABLE, each corroborated by its `CLASSIFY_REQUEST` row (10/10, 0 disagreements). An LLM confirmation layer comes next.

## Decisions on the residual sample misses (user-approved, 2026-10-25)

### D29 — Known losses: case_04, case_18, case_20, case_23
These are accepted as known losses and will be listed in `evaluation/evaluation_report.md`. Resource-naming conventions are never used.
* case_04 `account_004`: no participant file names it; only the `account_00N` convention would produce it.
* case_18 `audit_record_6645`, case_20 `audit_record_5569`: these ids appear nowhere in participant data.
* case_23 `account_410`: a 3-way tie between unobserved FAILED accounts (410 / 7015 / 9155); the deterministic pick takes 7015.

### D30 — Policy actions left with two candidates are omitted, not picked (measured)
Source: `python code/evaluation/policy_ties.py`. On the leakage-free policy hold-out (each pinned action's pin and its owner identity removed), 106/121 actions are still assigned correctly by unique/chain and 15 stay open: 4 with 2 candidates, 11 with 3. A deterministic pick (lowest natural case_id) on the 4 two-candidate actions was right **0/4**. The true owner was among the 2 candidates in 4/4 events; hold-out truth is always a requester, so non-requester owners cannot show up here. On the 32 samples, pick vs omit gives 25 vs **26** exact rows, step F1 0.9792 vs **0.9816** and precision 0.9843 vs 0.9947. Pick was right for case_21 but wrong for case_24 (took case_25's analytics_1895) and case_19 (analytics_3264, not in gold), so 1/3. Combined accuracy is 1/7, far below the 0.5 break-even for exact rows and the F1/2 ≈ 0.49 break-even for step F1. **Omit** stays (D15). In the full solve, 30 two-candidate actions remain unassigned; the expected change from picking them would be about −30 × (1 − 2p) exact rows.
So case_21 (analytics_5335) and case_25 (analytics_1895) also remain known losses.

### D31 — Corrected hold-out figures (supersede the numbers quoted in D15, D24–D26)
Leakage-free re-run (`code/evaluation/holdout_all.py`, rule_ledger §G): accounts adopted **0.916 leave-one-out / 0.783 hide-all** over 226 episode-scoped truths. Timing only gives 0.889 / 0.748, co-location +0.022 / +0.031, foreign +0.004 / +0.004. Policy actions adopted **106/121, 0 wrong**; chain alone takes it from 9 to 103.
**Flagged as not beating the baseline**, with proposed action pending your decision:
* D26 owner exclusion: +0.000 on the hold-out, samples unchanged, 8 eval assignments change. Proposal: **remove** (unmeasurable benefit).
* Filled account slots (pipeline detail of D15): +0.000, 0 assignment changes. Proposal: **remove** (dead weight).
* D25 tombstone exclusivity: +0.000 alone, +1 action in combination with chain + prefix, 0 eval changes. Proposal: **keep** (it beats the baseline in the adopted combination, with 0 wrong).

## LLM layer (2026-10-25)

### D32 — The model describes, code decides
* **Intent:** the rule verdict (D28) decides. The LLM label (groq `openai/gpt-oss-120b`; 16 batched, deduplicated, cached calls) is recorded per case, and any disagreement is flagged in the trace and listed in `runs/intent_disagreements.json`. Result: 0 disagreements on the 10 NOT_APPLICABLE cases and 1 overall: sample case_25 ("tell me whether the account … have already been removed"). The LLM says "status inquiry, not a new deletion"; the rules say deletion, and gold has case_25 as COMPLETE, so the rules are right.
* **Screenshots:** the vision model only transcribes into a fixed schema. Code corroborates every resource id, email, user id and phone (OCR token with word boundaries, the **same OCR row** as the record's id, or verbatim elsewhere in the participant data), drops and logs the rest (`runs/vision_dropped.jsonl`), and derives state with fixed rules (`state.screenshot_verdict`).
* A screenshot row with no identifiers of its own inherits the page subject only when it is the page's own entity (id in the heading or subtitle) or the page shows a single resource. Example: the case_35 decoy search_1874 (`g.silva76.jr`), whose email qwen misread as the subject's, is now neither linked nor kept.

### D33 — Pinned vision model: groq `qwen/qwen3.8-27b`
Prompt v2 scores token P 1.000 / R 0.896 and 11/11 linked-resource states. Gemini 3.5-flash (prompt v1) read 7/11 images before its free-tier daily quota (20 requests/day/model) ran out, at P 1.000 / R 0.750 and 5/11 states. Details are in docs/llm_limits.md. Gemini is an availability-only fallback, and any fallback extraction is flagged in the trace and in usage_report.md.

## Decisions on the corrected hold-outs and case_45 (user-approved, 2026-10-25)

### D34 — case_45 keeps only audit_record_003; filled slots removed; D26 kept
* **case_45.** The user's rationale: rule_12 retains SECURITY_AUDIT_LOG only within a *supplied* retention period (audit_record_003 has one via security_retention_extract.pdf, plus the catalog link), and D24 (one resource per prefix) agrees. **Implementation note:** a literal "requires a supplied retention period" test cannot be the rule. 17/24 gold sample audit records have no retention value in any participant file yet are verified PRESENT, and the test would drop the audit record from 300 eval cases. The rule is therefore implemented through D24 (`Pipeline._weak_duplicates`): when a request has several resources with one prefix, a resource whose only identifier links are list-page screenshot rows is dropped in favour of the strongly linked one(s). Effect: case_45 keeps audit_record_003 only. The 32 samples are unchanged.
* **Filled account slots:** removed (no measured effect, 0 output change).
* **D26 owner exclusion: kept.** The hold-out cannot exercise it, because hold-out truth resources are requester-owned by construction, so +0.000 means untestable, not useless. Its real effect: in the eval solve it swaps 4 account pairs (case_174/175, 240/241, 336/337, 339/340) by excluding accounts that carry the other request's user's identifiers. *Correction to the stated rationale:* case_03's account (account_7566) is identifier-pinned with or without D26. account_6523 was kept out of case_03/case_17 by D16 episode scoping, not by D26.
* **Tombstone exclusivity (D25): kept.**

### D35 — Intent prompt and vision token budget
* The intent prompt states that a follow-up about an earlier deletion request (asking to check, inspect, verify, confirm or finish a deletion) is DELETION. The rules stay the decision; the LLM label is a recorded confirmation.
* Vision `max_tokens` = 1500. The largest prompt-v2 extraction is 1129 output tokens; Groq reserves max_tokens against the 8000 TPM window, so 8192 made calls collide (8 of 30 attempts got a 429). A response truncated at max_tokens raises and is never cached.

## Phase 5: generalisation fixes (2026-10-25)
Every fix below was driven by a custom case or an independent-audit disagreement reproduced with `--explain`. The 32 samples stay at 26/32 exact, status 1.000, step F1 0.9816 after each one.

### D36 — Bug fixes found by the custom cases and the independent audit
* **Support record with only a stable reference and `message_text: "[REDACTED]"`** (no requester fields) fell through to PRESENT. It is now CLEAN; an unredacted message body still counts as exposure. Found via audit cases case_57 and case_308; 7 eval cases flip INCOMPLETE → COMPLETE.
* **Snapshot-bundle records carry their own time** (`recorded_at` / `observed_at`). The snapshot's `captured_at` is used only as a fallback (D9). Found via audit case case_241: audit_record_436 was recorded at 06-23T06:00 (= audit_at) inside a bundle captured 06-30. 21 eval rows change.
* **Page-level ids on single-customer screenshots:** ids the vision model lists for the page but not as a record (e.g. `support_9538` in Haruka Sato's customer-details panel) inherit the page subject. List pages have no page subject, and row identifiers must share the id's OCR row, so decoy rows still never inherit. Found via audit case case_34.
* **Multilingual refusals in intent rules** (FR / ES / PT / JA / KO), e.g. "Je ne demande pas la suppression", "削除は希望しません". Found via custom_01 and custom_02. The 550 real requests are unchanged (still exactly case_533–542 NOT_APPLICABLE).
* **Grader:** "exact row" now also requires the status to match (a COMPLETE-with-no-steps prediction for a NOT_APPLICABLE row was counted exact).

### D37 — Policy chain: adjacent-anchor fit (measured, adopted)
The chain test required a candidate to sit a whole number of chain steps from *every* anchor. Measured consecutive steps are 390 s (34) and 449 s (37), plus one 270 s flow → backup step (case_390), which broke the all-anchor test for analytics_448 (audit case case_390). Now the nearest anchor before and after must each be exactly one step (390 / 449 s ± 3 s) away. Leakage-free policy hold-out: **109/121 correct, 0 wrong** (was 106/121, 0 wrong). Samples are unchanged.

### D38 — Inventory captures never prove absence (refines D17)
Gold sample case_04 cites evidence_54 (failover account store, 09-02) for account_004, although inventory evidence_90 (09-05, ≤ audit_at) also lacks it. Inventory `customer_profiles` tables list a subset of customers, so only failover/primary account stores and snapshot-bundle `account_service` collections count. 21 eval COMPLETE cases change their absence citation.

### Pending — "fewest other resources" tie rule for open policy actions (not adopted)
Give an open policy action to the unique candidate owning the fewest other data resources (excluding account and audit_record). Samples: 3/3 correct (fixes case_21 and case_25, and keeps analytics_3264 from case_19), 26 → 28 exact rows. The auditor agrees on 3 of 4 eval actions. Leakage-free hold-out: 1 correct, 7 wrong, 7 abstain. The hold-out is biased against this rule (its truth actions are the identifier-carrying, failed-workflow ones whose owners own many resources), but it is the only unbiased measurement we have. D30 (omit) therefore stays until you decide.

## Final phase (2026-10-25)

### D39 — Tie-break for open policy actions: matched hold-out (B1). D30 (omit) kept, pending your decision
`code/evaluation/tiebreak_matched.py`. With the adjacent chain (D37), almost every held-out action is now resolved by chain or uniqueness, so the "fewest other resources" rule only fires on the residue.
* Matched set A (true owner otherwise holds only account + audit record): n=6; the rule decided 1 → **1 correct, 0 wrong**; 3 resolved by uniqueness, 2 abstained.
* Matched set B (true owner holds only compliant resources): n=112; the rule decided 1 → 1 correct, 0 wrong.
* Unmatched (all 121): the rule decided 4 → 1 correct, 3 wrong, and one wrong pick cascaded into a wrong chain assignment.
* Other evidence: samples 3/3 correct (26 → 28 exact); independent audit 1 agrees on 3 of 4.
* **Eval impact: 40 rows change** (0 status changes).
The matched hold-out passes the stated bar (≥ 50 %, more right than wrong), but on a single decision. Under the stop rule ("ask before any change of > 15 eval rows") it is **not adopted**; `output.csv` keeps D30 (omit), and `runs/output_fewest.csv` holds the alternative.

### D40 — case_126 citation conflict (flagged, not changed)
account_8819 is PRESENT in a bundle captured after audit_at (record time 08-11, ≤ audit 08-17). The only file captured ≤ audit_at that covers the account store is evidence_17 (07-25, before the 08-03 request), which lacks it. D9 read literally would make the pre-request omission win and the case COMPLETE. As implemented, the finding cites evidence_17, which proves absence, not presence. One row; left for your decision.

### D41 — Invariants and recall (A1–A3)
* **Recall:** every unstepped requester-linked resource has a reason: 33 post-audit only, 2 prior episode (D16: case_33 export_1260, case_34 audit_record_3753), 5 D24 prefix conflicts (case_45 audit_record_001/002/004/005/006), 0 "other".
* **Invariants:**
  * 0 resources shared across users; 0 extra same-prefix resources; 0 finding/remediation/verification mismatches; 0 INCOMPLETE without a finding; 0 citations captured after audit_at.
  * 10 deletion cases have no account step: the artifact-only group. Each user's account appears only in a 09-15 log (after every one of those audits) and in later deletion runs, so none is admissible.
  * 12 COMPLETE/INCOMPLETE rows cite an omission store for an account whose state comes from a post-capture file: 11 consistent (absent both ways), 1 is D40.
* **Precision:** the 15 weakest steps are deterministic account ties. Checked against the raw files, the result matches the status for every candidate, and no candidate carries any identifier in any file, so none can be narrowed and none can change a status.

### D42 — `message_excerpt` is not evidence of a support message (bug fix, adopted)
Every row of the inventory `support_records` table has a NOT NULL `message_excerpt` (schema), attachments included, so the D20 "record nature" check no longer uses it. Only `message_text` / `message_pii_scan` mark a message, and otherwise the deletion worker's op decides. Found by the second audit (case_443 support_8341). 5 eval rows (case_95, 143, 251, 347, 443) change REDACT → DELETE. Samples 26/32 and custom cases 8/8 are unchanged.

### D43 — Account "timing signature" (measured; NOT adopted, pending decision)
Suggested by the second auditor. The first deletion-worker row on one of the request's own resources after its account submission lands at (Δ mod 60 s) ∈ {10, 11, 21, 22, 40, 41, 51, 52}, for 184/184 identifier-pinned accounts (`signals.FollowupSignature`). Leak-free account hold-out (`holdout_all.py`, 246 truths): adopted signals give 0.923 LOO / 0.768 hide-all; adding the signature gives **0.992 / 0.976**. Samples: 26 → 27 exact (case_23 fixed), step F1 0.9816 → 0.9869. Eval: **42 rows change** (account ids only, 0 status changes). It exceeds the 15-row stop rule, so it is left for your decision (`runs/output_signature.csv` holds the alternative).

### D44 — Mixed `customer_data_*` replica snapshots as absence proofs (NOT adopted, pending)
Both independent auditors cite these (often captured exactly at audit_at) where D17 cites the latest pure account store. No sample distinguishes the two. Allowing them changes 56 eval rows; samples unchanged (26/32).

### D45 — Final live run (C1–C4)
The headroom check passed at first (qwen ≈ 98k of 200K daily tokens left). The strict live run with an empty cache then hit 4 consecutive per-minute 429s on qwen for evidence_42 (retry backoff 3–24 s, too short), and the Gemini fallback truncated, so strict mode aborted; output.csv was untouched. After that attempt, qwen's remaining daily budget (~70k) is below 2× the need (~90k), so per C1 no further live run today; the rolling window frees up from about 2026-10-25T20:20Z. **Final output.csv is the cached run** (every LLM result in it came from real calls on 2026-10-24/25). The client now honours `retry-after` with up to 7 attempts (≤ 60 s each), and `--strict` aborts at the first failure.

## Decisions on D39 / D40 / D43 / D44 (user-approved, 2026-10-25)
The 15-row stop rule was lifted for these approved changes only. No-regression gates held after every step: samples ≥ 26/32, custom 8/8, status agreement 20/20 on both audits.

### D43 — Account timing signature: ADOPTED after 2-fold cross-validation
`python code/evaluation/signature_cv.py`. The residue set is learned on one random half of the 246 identifier-pinned accounts; leakage-free leave-one-out accuracy is measured on the other half, then the halves swap.

| Seed | Learn → test | Residues learned | LOO on held-out fold |
|---|---|---|---|
| 20261025 | A → B | {10, 11, 21, 22, 40, 41, 51, 52} | **0.984** (121/123) |
| 20261025 | B → A | {10, 11, 21, 22, 41, 51, 52} | **1.000** (123/123) |
| 7 | A → B | {10, 11, 21, 22, 40, 51, 52} | **0.984** (121/123) |
| 7 | B → A | {10, 11, 21, 22, 40, 41, 51, 52} | **0.992** (122/123) |

Every fold is ≥ 0.95, so it is adopted. Full-set hold-out: 0.992 LOO / 0.976 hide-all (was 0.923 / 0.768). Effect: account ties 55 → 14, open account slots 100 → 28 (0 with status risk), samples 26 → 27 (case_23), 42 eval rows.

### D46 — Policy-action signature: ADOPTED (replaces most of the D39 need)
For identifier-pinned policy actions, (action − the request's own account submission) mod 60 s ∈ {9, 10, 11, 39, 40, 41} (64/64): the account residues {10, 11, 40, 41} shifted by chain steps of 390 / 449 s (≡ 30 / 29 mod 60). An open policy action goes to the unique candidate whose assigned account sits at such an offset. Leak-free policy hold-out: **120/121 correct, 0 wrong** (was 109/121, 0 wrong). Samples 27 → 28. 61 eval rows. Open policy actions 72 → 10.

### D39 — "Fewest other resources": ADOPTED only for actions still open after D46
Applied last, to the residue (10 open actions → 8 assigned, 2 stay open). Samples 28 → 29 exact. 7 eval rows.

### D44 — Mixed replica snapshots as absence proofs: REJECTED, D17 stays strict
Gold case_04 cites the dedicated account store (evidence_54, 09-02) over a later, broader capture that also lacks the account (evidence_90 inventory, 09-05). Mixed `customer_data_*` replica snapshots are broader captures too, so they are not absence proofs either.

### D47 — case_126 (D40): keep the finding, cite the pre-audit deletion activity
A finding whose only observation is a post-capture file (D9) no longer cites an omission store (which proves the opposite). It cites the latest pre-audit deletion-worker activity on that resource (Q4 activity fallback). case_126: REPORT_REMAINING_RECORD account_8819, PRESENT, cites activity_3114. 1 row.

## Final hardening pass (2026-10-25)

### D48 — Bounded investigator agent
Read-only tools with as_of = audit_at enforced inside each tool. At most 4 actions; pinned Groq gpt-oss-120b, temperature 0, cached; pydantic-typed. It runs only on the 30 uncertain eval cases.
* **Validator:** a fact is accepted only if the file exists and was captured ≤ audit_at; the identifier is the requester's; the resource and identifier sit in the same record; no other user owns the resource; it is not pinned to another case; and it was not excluded as a prior episode.
* **Final run:** 119 actions, 2 proposals (1 accepted-redundant, 1 rejected), 0 rows changed.
* **Bug fixed before adoption:** the first validator ignored same-user pins and accepted case_33 ← export_1260 (D22 says it belongs to case_52).
* Facts change rows only with `--adopt-investigator`, after a reviewed diff.

### D49 — Consistency gate
`audit/consistency.py::check`, run before every write: 7 check families, 14/14 broken-row tests blocked, 0 violations on the final output.

### D50 — Conservative input handling
* **Unknown requester, an unprovable COMPLETE, or nothing linked:** NOT_APPLICABLE for human review, never an automatic COMPLETE.
* **Unparseable audit_at:** falls back to reported_at, then requested_at.
* **Duplicate case_id:** an error.
* **Unknown rule action:** handled as DELETE.
* **Missing or corrupted evidence:** skipped and reported (strict aborts).
* **Screenshot state:** a compliant state word (deleted / disabled / blocked) counts only if the OCR also reads it, an injection guard.

None of these changes `output.csv` on the real data (verified byte-identical); the robustness suite passes 12/12.

### D51 — Complete live run (Part 4): not yet possible
Headroom on 2026-10-25T11:4x UTC:
* qwen: 70.7k left vs 40.3k needed → pass.
* gpt-oss: 60.1k left vs 125.7k needed (intent 38.2k + investigator 66.5k, × 1.2) → fail. It passes after 2026-10-25T21:46Z (≈ 03:16 IST, Oct 26).

Until then `output.csv` is the cached run. Every response in it, now including the 119 investigator calls made live today, is committed in `code/llm/cache_snapshot/`.

### D52 — Human-review queue for conservative NOT_APPLICABLE answers
A case answered NOT_APPLICABLE only because nothing could be proven is also written to `runs/human_review.csv` (`case_id, reason_code, missing`) and counted in the run summary (`human review queue: N cases`). This covers an unresolved requester, an unprovable COMPLETE, nothing linked, or no usable timestamp. Reason codes: `UNRESOLVED_REQUESTER`, `UNPROVABLE_COMPLETE`, `NOTHING_LINKED`, `UNUSABLE_TIMESTAMPS`.
Why NOT_APPLICABLE: The output contract has no review status. INCOMPLETE requires evidenced findings, and COMPLETE requires proof for every verification. NOT_APPLICABLE is therefore the only value that authorises no destructive action and claims no compliance.
It affects **0 of the 518 real rows** (queue empty, `output.csv` byte-identical). The robustness test `human_review_queue` triggers three codes in one run and checks the queue (4 rows, all NOT_APPLICABLE). Writing that test exposed a crash: the placeholder date for an all-unparseable request broke the global solver. Fixed with a valid sentinel date, and such requests are kept out of the solve.
