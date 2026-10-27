# Second independent audit: method and inferred conventions

Scope: the 20 eval cases in `auditor_rows.json`, derived by hand from `dataset/` and `problem_statement.md` only. I did not open the pipeline code, the root `output.csv`, or the other audit.
Helper scripts are in `work/`. They are used only for searching and timing arithmetic, and they import no project modules.

## Identity
- user = the `user_identities` row whose current email matches, or whose `user_audit_log` email history matches (case-insensitive). Identifiers searched: current and historical emails, name, current and old phones, current and old email hashes, `user_id` (word match).
- Evidence hits are joined across log lines by `trace_id`. A `*_started` line carries the lookup key and the `*_completed` line carries the resource_id.

## Time
- An evidence file can be cited only when the manifest `captured_at` is at or before `audit_at`. Equality is allowed, as sample case_31 cites evidence_51 at exactly audit_at. A snapshot bundle's inner `captured_at` does not override a later manifest time; sample case_21 cites evidence_92 rather than the later evidence_02.
- Post-audit evidence is used only to identify a resource (its id or category), never to establish its state.

## Account resource (the workflow's own rows)
- The account deletion is a `DELETE_RECORD -> account_service` row 45-75 min before `reported_at`. Batches share a `reported_at`, so several candidates are common.
- The result maps to the claim: COMPLETE -> SUCCESS, PARTIALLY_COMPLETE -> PARTIAL, NOT_STARTED -> FAILED. This holds for every generated sample.
- **Timing signature.** In all 32 samples, each case's first follow-up action falls a fixed number of seconds (mod 60) after its account deletion. A completed policy action at about -29 min falls at +40/41 s, and a failed or post action at about +40-60 min falls at +51/52 s. Later actions in the same case alternate by about 29-30 s. I used this to break ties within a batch and to attach unlabeled anonymized analytics rows to the right case.

## Policy and step rules
- Present search/export/account/attachment -> REPORT_REMAINING_RECORD + DELETE.
- Identifiable analytics -> REPORT_EXPOSED_PII + ANONYMIZE.
- Support conversation with exposed requester -> EXPOSED_PII + REDACT.
- Attachments vs messages: the worker uses DELETE_RECORD for attachments ("account access" excerpt, ticket_attachments) and REDACT_RECORD for messages (delivery/order excerpts).
- An enabled connector -> REPORT_ACTIVE_DATA_FLOW + DISABLE. A restorable backup -> REPORT_RESTORE_RISK + BLOCK.
- A replica-only account copy (reporting, read, regional or failover) -> REPORT_REMAINING_RECORD on the account. No separate data-flow finding, which follows samples case_05 and case_24.
- Security audit records and legal holds get VERIFY_RECORD_PRESENT. Tombstones get VERIFY_RECORD_REDACTED when all direct identifiers are null.
- Verification covers: the account (always ABSENT), every remediated record, and every retained, redacted, anonymized, disabled or blocked record.
- Ordering: phase, then the operation order in the spec table, then system_id, then resource_id. This matches every sample.
- Evidence citation:
  - COMPLETE: every verification cites the single snapshot file that covers the user's resources at audit time. It still cites that file for an account that is simply absent from it, as in sample case_31.
  - INCOMPLETE: findings cite the file that shows the state. Remediation and verification steps use "".
