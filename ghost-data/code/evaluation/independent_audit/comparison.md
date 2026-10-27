# Independent audit vs pipeline (20 stratified eval cases)

The auditor was a fresh sub-agent with access only to `dataset/` and `problem_statement.md`; it never saw code, docs or outputs. Stratified sample (seed 20261025): 1 legal hold, 4 timing-assigned accounts, 4 screenshot/PDF, 3 historical email, 3 COMPLETE, 3 flow/backup, 2 NOT_APPLICABLE. Rows: `auditor_rows.json`. Comparison: `python code/evaluation/compare_audit.py` (raw output in `comparison_raw.txt`).

| Metric | First comparison | Final (after D36–D47) |
|---|---|---|
| Status agreement | 18/20 | **20/20** |
| Exact rows | 10/20 | **14/20** |
| Shared steps (auditor 99) | 87 | 96 |
| Evidence agreement on shared steps | 75/87 | 92/96 |

Exact rows went from 12/20 to 11/20 after D38 because case_175 now cites the latest non-inventory store (evidence_22) instead of an inventory file. That follows the gold-backed D38 rule, so the auditor's cite is the one at odds with the samples.

Every disagreement was reproduced with `python code/main.py --case <id> --explain` before any change.

| Case | Disagreement | Verdict | Action |
|---|---|---|---|
| case_57, case_308 | Pipeline: support EXPOSED_PII; auditor: REDACTED (record holds only subject_reference + `[REDACTED]` message) | **Pipeline bug** | Fixed (D36). 7 eval cases INCOMPLETE → COMPLETE |
| case_241 | Auditor keeps audit_record_436, found only in a bundle captured after audit_at but recorded exactly at audit_at | **Pipeline bug** (record time ignored) | Fixed (D36). 21 eval rows change |
| case_34 | Auditor keeps support_9538 (customer-details panel of the subject's screenshot) | **Pipeline bug** (page-level id not observed) | Fixed (D36) |
| case_390 | Auditor attributes analytics_448 by chain spacing | **Pipeline bug** (chain model too strict: one 270 s step) | Fixed (D37, hold-out 106 → 109/121, 0 wrong) |
| case_34 | Auditor also keeps audit_record_3753 (only link 09-15, before the 09-24 request; a prior failed deletion run on 08-28) | **Known ambiguity**: D16 episode scope excludes it | None |
| case_34 | support_9538 finding cites evidence_01 (auditor) vs evidence_04 (pipeline) | **Auditor convention**: A17 cites the latest file ≤ audit_at that observes the resource (71/71 on samples), here the 09-26 screenshot | None |
| case_57, case_308, case_175 | Absence proof for an unobserved account: auditor cites a mixed `customer_data_*` replica table or an inventory | **Auditor convention / D38**: gold case_04 shows inventories are not absence proofs; the samples never cite mixed replica tables | None |
| case_245, case_299 / case_301 | Account id in a multi-candidate batch (auditor marks these low confidence) | **Known ambiguity** (D15 ties; 0 status risk) | None |
| case_299, case_378, case_427 | Auditor attributes an anonymized analytics record that the pipeline leaves open | **Known ambiguity** (D30 omit). The "fewest other resources" rule would agree with the auditor here and fixes 2 samples, but scores 1/8 on the hold-out | Pending your decision |

**Final update (D43 / D46 / D39):** the workflow-clock signatures resolved several of the "known ambiguity" rows above, and exact rows rose 11 → 14. The account picks for case_299 / case_301 and the analytics records for case_299, case_378 and case_427 now come from the account and policy timing signatures, or from D39 for the residue. Remaining differences: account ids where the auditor itself was low-confidence, the D16 episode scope (case_34 audit_record_3753), and absence-proof citation conventions (D17 / D44 rejected).
