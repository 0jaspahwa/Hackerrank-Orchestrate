# Independent auditor method

Scope: 20 eval cases, hand-derived using only `problem_statement.md` and `dataset/`. No pipeline code or output was opened. Helper scripts (search and trace-join only, no decision logic) are in `work/`.

## Conventions taken from the 32 samples

- **Status.**
  - `NOT_APPLICABLE` with `[]` when the request does not authorize deletion.
  - `COMPLETE` contains only VERIFICATION steps, and each one cites a file id.
  - `INCOMPLETE` contains findings that cite evidence, then remediation and verification steps that all use `""`.
- **Ordering.** Steps are sorted by phase, then by the operation order in the spec table, then by `system_id`, then by `resource_id`. Examples: `REPORT_EXPOSED_PII` for analytics_platform comes before the one for support_platform, and remediation runs `DISABLE_DATA_FLOW` > `BLOCK_RESTORE` > `DELETE` > `REDACT` > `ANONYMIZE`.
- **Every finding gets a matching remediation and verification.** The verification phase also lists all other in-scope resources:
  - the account (`VERIFY_RECORD_ABSENT`), even when it was already absent;
  - the security audit record (`VERIFY_RECORD_PRESENT`);
  - records that were already compliant, such as redacted support, anonymized analytics, disabled flows and blocked backups.
- **Choosing the operation.** It follows `deletion_rules.csv`:
  - search, export, attachment, marketing and account records are deleted;
  - support messages are redacted;
  - analytics records are anonymized;
  - audit and legal-hold records are retained (`VERIFY_RECORD_PRESENT`).

  Sample case_12 shows that when the workflow itself applied `DELETE_RECORD` to an analytics resource, the expected answer is also DELETE. So I let the workflow's intended action decide which rule applies.
- **Legal-hold records.** Not all of them produce steps. user_12 is in the legal register PDF but sample case_12 has no legal step. A `legal_hold_*` resource is included only when the workflow or a manifest names one.
- **Which evidence to cite.** Cite the file captured at or before `audit_at` that shows the state. A file captured exactly at `audit_at` counts. One file usually covers the whole case.

## How I linked resources to a subject

1. **Identifiers.** I built the subject's identifiers from `user_identities.csv` plus `user_audit_log.csv`: current and historical email, sha256 of each lower-cased email, name, current and old phone, and user_id.
2. **Search.** I searched all text evidence (logs, JSON objects, SQL rows, extracted PDF text) for those identifiers. In logs I joined lines that share a trace id (`trace_id=`, `requestId=`, `[tr-…]`, `rb-…`) so that each resource_id is tied to its lookup key. I viewed the PNG screenshots directly.
3. **Unlinked resources.** Some resources carry no identifiers: accounts, anonymized analytics, and sometimes the whole policy-action chain. For those I used the deletion-workflow rows just before `reported_at`, with three rules:
   - **Batch.** The account `DELETE_RECORD` rows for cases reported at hour H fall roughly in H-1:15 to H-0:50. Policy actions follow at H-0:29, H-0:22, H-0:15, H-0:08 (analytics, support, flow, backup, ~7m30s apart). Each case's chain shares one cadence, which is how I picked one of two analytics ids.
   - **Status mapping.** A case's `reported_status` matched its own account delete result (COMPLETE=SUCCESS, PARTIALLY_COMPLETE=PARTIAL, NOT_STARTED=FAILED) in every batch I could verify independently. That was more than 15 cases, including samples 09, 11, 13, 14, 31 and 32. I used this to pick the account when no identifier links it.
   - **Last resort.** When several accounts share the same result, I used the descending-case-id order seen in most batches. These picks are marked low confidence.
4. **State at the audit time.** Only evidence captured at or before `audit_at` decides state. Later files were used only to learn ids, never to cite or to change the answer (case_175 is a timing trap). Workflow results were treated as claims.

## Main residual risks

- Account ids chosen without an identifier link: case_245, case_299 and case_301 are low confidence; case_308, case_57, case_427 and case_482 are medium.
- Anonymized-analytics attribution by cadence: case_308, case_57, case_390, case_175, case_378, case_427 and case_299.
- Screenshot-family scope: case_34 may expect only the account. case_63's screenshot shows a different persona than the catalog link.
