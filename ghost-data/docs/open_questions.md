# Open questions (Phase 1)

Each question lists options, the sample evidence, and a recommendation (★). Nothing below is implemented yet.

---

## Q1. Post-capture files: can they be used to *discover* resources and decide their *state*?

Your hard rule says never to use evidence "captured after audit_at". The samples show the canonical answers include resources that are only visible in files whose **manifest** `captured_at` is after `audit_at`, but whose **internal** event time is before it:

* case_09 (audit 09-16T09:00): `account_3960` and `audit_record_4902` appear only in `evidence_06` (captured 09-18). The relevant lines are timestamped 09-16T08:52–08:57.
* case_23/24/29/30: the audit records appear only in `evidence_02` (captured 09-17), inside a snapshot whose own `captured_at` is 09-15T12:00 = audit_at.
* Citation never uses such a file: case_09 cites `evidence_93`, not `evidence_06`.

Options:
* A. Manifest `captured_at` gates everything. Literal reading of the brief. We lose 6 verification steps in these samples, and similar steps in eval.
* ★ B. **Event time ≤ audit_at gates discovery and state. Manifest `captured_at` ≤ audit_at gates citation.** Matches all 32 samples. Stays faithful to "information available at or before audit_at", because the log line or snapshot existed then.
* C. Like B, but post-capture files are allowed for discovery only, never for state.

## Q2. Claims and recreation events after the latest observation

Does a reported SUCCESS action after the latest observation (but before `audit_at`) change the state? What about a restore or sync after a deletion?

No sample has an action between the latest observation and audit_at that would flip the answer; observations are always the newest signal. Samples do show that claims never override observations (case_05, 16, 24, 25, 26, 02).

Options:
* ★ A. **Deletion, redaction and disable claims never change state.** The resource stays at its last observed state, since a claim is not evidence. **Recreation events** (`RESTORE_BACKUP … repopulated X`, `SYNC_RECORD … Active connector repopulated X`) that are ≤ audit_at and later than the last ABSENT observation of X make X PRESENT. The finding cites that activity_id. The source backup or flow becomes a REPORT_RESTORE_RISK / REPORT_ACTIVE_DATA_FLOW unless a later observation shows it blocked or disabled.
* B. Claims and recreation events are both ignored; only file observations count.
* C. SUCCESS claims after the last observation are trusted.

## Q3. Replica drift across files: which observation wins?

Within one snapshot, the samples answer it: any copy present means PRESENT (case_05, case_16). Across files, the latest file always agreed with the expected answer.

Options:
* ★ A. **Latest event time wins across files. Within the same file or timestamp, PRESENT (non-compliant) beats ABSENT (compliant).** A replica copy is still a copy.
* B. Any non-compliant observation in the episode window wins (most conservative).
* C. Primary-store observations override replica observations.

## Q4. When to cite an `activity_id` or `audit_id` instead of a `file_id`

No sample ever cites an activity_id or audit_id. Even when a CHECK_DATA_FLOW / CHECK_RESTORE_ACCESS activity exists, the file is cited (case_15 → evidence_01, case_14 → evidence_03).

Options:
* ★ A. **Always cite the latest file ≤ audit_at that observes the resource.** Fall back to an `activity_id` only for a finding that has no file observation at all (e.g., a RESTORE_BACKUP recreation of a resource never seen in a file). Never cite `audit_id`: identity history is not resource state.
* B. Cite the activity when it is later than the latest file.
* C. Never cite activities. Drop findings that lack a file.

## Q5. `resource_id` sort: lexicographic or numeric?

No sample has two steps with the same operation and system_id (checked all 32), so the samples don't decide it. It matters for, e.g., `search_898` vs `search_1308`.

Options:
* ★ A. **Plain lexicographic string sort.** The spec says "ascending"; a Python generator sorting tuples of strings does this; and `system_id` is already compared as a string.
* B. Natural or numeric sort on the trailing integer.

## Q6. What counts as an active data flow or restore risk for *this* user?

Samples: flows and backups are attributed by `customer_email` ∈ identity set, or `customer_reference` / `subject_selector` / `lookup_key` = user_id (case_11, 15, 17, 18, 20, 26–30). Generic connectors with no selector ("Order notifications", "Delivery tracking") and other users' flows in the same file or screenshot (data_flow_1121 in case_15) are excluded.

Options:
* ★ A. **A flow or backup is in scope only through an identity link (A3) within the episode.** Its state is the latest observation: enabled or restorable → finding; disabled or blocked → verification only. CHECK_* activities alone are claims: they corroborate but never create a finding.
* B. Also treat a CHECK_DATA_FLOW / CHECK_RESTORE_ACCESS activity in the case's workflow batch as sufficient for a finding, citing the activity.

## Q7. Resources linkable only through the deletion-workflow batch (timing)

23 of 111 expected (case, resource) pairs have no identifier anywhere. They are accounts and anonymized analytics, tied to the case only by the deletion worker's activity near `reported_at`. When several cases share a `reported_at` (up to 6 per timestamp), assignment can be ambiguous. I simulated this on the 32 sample accounts using window [rep−90 min, rep+15 min], account result ↔ reported_status, and dropping ids identity-linked to other cases:
* 22 unique and correct.
* 3 resolvable by preferring the case's own identity-linked id (case_11, 14, 24).
* 1 resolvable only with Q1-B (case_26, via the ev_02 inner snapshot).
* 1 ambiguous against unlinked eval cases in the same batch (case_23).
* 2 truly tied (case_28 vs case_29: both NOT_STARTED, both FAILED, neither linkable).
* 3 hand-crafted misses (case_04 has no activity; case_06/08 use a different worker template, outside the window).

Two audit records (case_18, case_20) and account_004 cannot be found by any method.

Options:
* A. Use identity links only. Unlinked accounts and analytics are omitted.
* ★ B. **Global batch assignment.** Candidates come from the case's workflow window. Account result must match reported_status; analytics, support and other policy actions are matched by slot. Identity-linked ids go to their own case first, then eliminate across **all** 550 cases sharing the batch. Assign only when exactly one candidate remains; otherwise omit.
* C. Like B, but on a tie pick the earliest-timestamp candidate rather than omitting. The expected value of a guess is ≤50%, and a wrong id costs a false step as well as a missed one.

## Q8. Episode lower bound

The rule "at least one subject link at/after `requested_at`" separates the samples perfectly. But an eval case might legitimately rely on a record observed only before the request, e.g., an attachment seen once in February that is never re-observed.

Options:
* ★ A. **A link at/after `requested_at`, or a workflow-batch activity in the case window, is required.** If the same user has another case whose window covers the earlier links, those links belong to that case.
* B. Use the request time minus a lookback (e.g., 30 days).
* C. Apply no lower bound when the user has only one case; apply A when the user has several (users 07, 17, 18).

## Q9. Absence by omission: what counts as a full store snapshot?

Four sample citations prove account absence by the account's absence from a complete account-store table. Those are `evidence_54` (primary and failover `customer_profiles`) and `evidence_51` (`account_service/customer_profiles: []`).

Options:
* ★ A. **Count only files that carry an account-store table or collection** (inventory `customer_profiles`, `customer_profiles_primary`/replicas, snapshot-bundle `account_service` collections, `customer_data_*` tables with `origin_system=account_service`), and only for account resources with no direct observation ≤ audit_at. Use the latest such file ≤ audit_at. If none exists, the resource is still verified ABSENT, but that only works for INCOMPLETE cases. A COMPLETE case needs evidence on every step, so that combination is flagged as a known risk.
* B. Also accept per-resource lookup files (service exports, lookup traces) as omission proof.

## Q10. Records with no resource_id (orders in the legal register PDF, order refs in screenshots)

Samples 08/12/17/21/24/29 have users listed in `legal_retention_register_20260529.pdf`, and none of their expected outputs include a legal-hold step.

* ★ A. **No step without a `resource_id`.** Orders and document refs are used only for linking.
* B. Invent `legal_hold_*` steps from the register. Not supported by any sample.

## Q11. Screenshot identity conflicts with the catalog activity

`marketing_profile_console.png` shows `marketing_001` for **Soo-jin Park / sj.park@naver.com**, which matches no user. `act_4008` links `marketing_001` to kwame-mensah@proton.me / user_09 (eval case_63). Every other screenshot agrees with its LINK activity (one minor name typo, "Ageyman").

Options:
* ★ A. **The catalog activity link wins.** It is structured and names the user_id. Vision extraction only corroborates the resource_id. `marketing_001` → case_63, MARKETING_PROFILE DELETE. Live with opt-in Yes means a REMAINING finding citing evidence_60 (captured 09-16T07:20 ≤ audit 09-17T09:20).
* B. Trust the image and treat marketing_001 as an unrelated decoy.

## Q12. Category for ambiguous analytics and support records

case_12 shows an `analytics_platform` record deleted rather than anonymized.

Options:
* ★ A. **Category comes from record nature first:** explicit `record_type`, then event/collection names (`contact_lookup`+`email_opt_in` → DELETE; `attachment`/file → SUPPORT_ATTACHMENT; `message`/`message_text` → SUPPORT_MESSAGE). Otherwise use the deletion worker's attempted op for that resource (70/70 agreement in samples), and otherwise the system default.
* B. Always use the system default (analytics → ANONYMIZE, support → REDACT). This contradicts case_12, 07, 10 and 13.

## Q13. Environment: `tesseract` is not installed

The planned deterministic image fallback needs the tesseract binary (pytesseract is only a wrapper).

Options:
* ★ A. **Install tesseract** (e.g., `winget install UB-Mannheim.TesseractOCR`) and document it in the README. The pipeline degrades gracefully when it is absent: images without OCR or LLM then contribute only through their LINK_RESOURCE_IDENTITY activity.
* B. Skip OCR and accept vision output after corroborating it against activity rows only.

---

# Round 2 (raised after the D9–D21 decisions)

## Q14. case_33 `export_1260` under the revised episode rule (D16)

User_18 has an earlier request in the dataset, eval case_52 (requested 06-16, audit 06-19). All of export_1260's links (export console screenshot 06-18, LINK activity_4004 06-18) fall inside that earlier episode, so D16 as written assigns it to case_52, not case_33.
* ★ A. **Keep D16 as written.** export_1260 belongs to case_52; case_33 gets export_1842 (statement NST-2026-0718-1842, 07-18).
* B. Keep export_1260 in both cases.

## Q15. Row order of output.csv: string or natural `case_id` order?

Eval ids run `case_33` … `case_550`. A plain string sort puts `case_100` before `case_33`. The spec only says "sorted by case_id".
* ★ A. **Natural (numeric) order**, the order of `deletion_requests.csv`. The grader matches rows by case_id, and the validator accepts either order.
* B. Plain string order.
