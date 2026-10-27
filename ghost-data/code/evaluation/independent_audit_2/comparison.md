# Second independent audit vs pipeline (20 new stratified eval cases)

This audit used a new, fresh sub-agent. It had no access to the code, docs, output or the first audit; it saw only `dataset/` and `problem_statement.md`.
Strata (seed 20261026): legal hold ×2, tombstone ×2, export ×2, support attachment, support message, replica drift ×2, restore/backup, user with several requests ×2, evidence captured exactly at audit_at ×2, historical email ×2, random ×3.
Comparison: `python code/evaluation/compare_audit.py output.csv independent_audit_2 audit2_selection.json` (raw output in `comparison_raw.txt`).

| Metric | First comparison (baseline output) | Final output |
|---|---|---|
| Status agreement | 20/20 | 20/20 |
| Exact rows | 12/20 | **14/20** |
| Shared steps (auditor 115) | 104 | 110 |
| Evidence agreement on shared steps | 100/104 | 106/110 |

Each disagreement was reproduced with `python code/main.py --case <id> --explain` before any change.

| Case | Disagreement | Verdict | Action |
|---|---|---|---|
| case_443 | support_8341: auditor DELETE (attachment), pipeline REDACT (message) | **Pipeline bug.** `message_excerpt` is NOT NULL in every inventory `support_records` row, so it is no evidence of a message; D20 then falls through to the deletion worker's op (DELETE_RECORD) | Fixed (D42): 5 eval rows; samples and custom cases unchanged |
| case_443, case_505 | Account id in a 2-candidate batch: the auditor used a "timing signature" (first follow-up − account submission, mod 60 s) | **Known ambiguity, but a new measured signal.** On the leak-free account hold-out the signature lifts accuracy from 0.923 to 0.992 (leave-one-out) and from 0.768 to 0.976 (hide-all). Samples go 26 → 27 exact (fixes case_23) | **Adopted (D43)** after 2-fold CV (≥ 0.984 on every held-out fold) |
| case_206 | analytics_9580 attributed by the auditor via the same signature | Resolved by the policy-action signature (D46) | Adopted |
| case_33, case_52 | Auditor adds account_2373 | **Auditor error.** Its only link is evidence_01 at 09-15, after both audits (D9) | None |
| case_33 | Auditor keeps export_1260 | **Decided (D22):** export_1260 belongs to case_52 | None |
| case_162, case_206, case_543, case_545 | Absence proof for an unobserved account: auditor cites a mixed `customer_data_*` replica snapshot captured exactly at audit_at; pipeline cites the latest pure account store | **Known ambiguity (D17).** No sample decides it. Allowing mixed tables changes 56 eval rows with samples unchanged | **Rejected (D44):** gold case_04 prefers the dedicated account store over broader captures |
