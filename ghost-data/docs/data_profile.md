# Phase 1 — Dataset profile

All facts below were re-derived from `dataset/` with throwaway scripts (not part of the pipeline).

## Verified brief facts

| Claim in brief | Verified value |
|---|---|
| 518 eval / 32 sample cases | 518 / 32 |
| Sample statuses | 22 INCOMPLETE, 10 COMPLETE, 0 NOT_APPLICABLE |
| Eval email match | 418 current, 100 historical-only, 0 unmatched |
| `email_hash = sha256(email.strip().lower())` | 850/850 users |
| Audit-log fields | `email` 107, `email_hash` 107, `phone` 92 (2024-12-14 → 2026-06-03) |
| Evidence files | 94 (24 SQL, 26 log, 1 txt, 29 JSON, 4 PDF, 11 PNG) |
| `system_activity.csv` | 4011 rows, 2026-01-04 → 2026-10-01; `SUCCESS` 2914 / `FAILED` 721 / `PARTIAL` 376 |
| Non-deletion eval requests | exactly 10 (`case_533`–`case_542`), see below |

Tooling on this machine: `pdftotext` (poppler) is present, `pypdf` and `pytesseract` (Python) are installed, but **the `tesseract` binary is NOT installed**. That affects the planned OCR fallback.

## Request texts

* 518 eval rows, 375 distinct texts, built from only **67 distinct sentences**. Templates exist in EN plus PT, KO, ES, JA, FR (12–14 each).
* 10 one-off non-deletion texts: change email, invoices copy, "what data do you hold", marketing-only opt-out, surname correction, login reset ("Do not close the account"), portable download, stop promo emails ("not asking for deletion"), phone update, data copy ("don't delete or change anything").
* All 10 are corroborated by a `CLASSIFY_REQUEST … classified request for account_N as non-deletion` activity. The `account_N` values map to the request email through `evidence_50` (security_audit_ledger).

## Identities

* `user_identities.csv` has 850 users with a stable `user_id` (`user_N`, 2–3 digits).
* The same user can appear in several cases, each a separate **deletion episode**: `user_07` (sample case_07 + eval case_61), `user_17` (case_17 + case_46), `user_18` (case_33 + case_52).

## system_activity.csv templates (event_type → source→target, details template)

| event_type | count | meaning |
|---|---|---|
| DELETE_RECORD | 890 | deletion_worker on account/search/support/export/analytics; `submitted primary profile account_N` (accounts) or `received the final result for X` |
| UPDATE_RECORD | 672 | profile preference updates / support-case index updates (noise) |
| SYNC_RECORD | 447 | profile→analytics sync; replication checkpoint (`account_replication_cluster`, `account_failover_replica`); **31× `Active connector repopulated downstream resource search_N`** (source resource = `data_flow_N`) |
| INDEX_RECORD | 400 | account→search index refresh |
| SEND_EMAIL | 368 | `email_delivery_N` (no identifiers) |
| REFRESH_CACHE | 284 | `cache_N` (no identifiers) |
| RETAIN_RECORD | 206 | security archival of audit_record_N; legal_hold_N policy actions |
| ANONYMIZE_RECORD / REDACT_RECORD | 204 / 173 | deletion_worker policy actions (`Policy action completed for X` = SUCCESS, or `received the final result` = FAILED/PARTIAL) |
| CREATE_EXPORT | 155 | export_service → object_store |
| CHECK_DATA_FLOW | 89 | `confirmed that data flow data_flow_N remains enabled` |
| DISABLE_DATA_FLOW / BLOCK_RESTORE | 36 / 36 | policy actions |
| CHECK_RESTORE_ACCESS | 28 | `archive backup_N can still be restored` |
| LINK_RESOURCE_IDENTITY | 12 | data_catalog links a resource (or a document ref such as `NST-2026-0718-1842`) to an email **and** user_id; one per PDF/PNG artifact |
| CLASSIFY_REQUEST | 10 | privacy intake: non-deletion |
| RESTORE_BACKUP | 1 | `backup_9287` restore `repopulated downstream resource search_4882` |

Activity rows almost never carry identifiers. The only identity-bearing ones are the 12 LINK rows. Deletion-worker rows for one case cluster around that case's `reported_at`: account submission ≈ −75…−45 min, then policy actions at fixed slots (analytics :31, support/tombstone :38, data_flow :45, backup :52). Failed actions of NOT_STARTED cases land after `reported_at`. The account-submission result matches the case's `reported_status` in 22/22 generated samples (COMPLETE→SUCCESS, PARTIALLY_COMPLETE→PARTIAL, NOT_STARTED→FAILED).

## Evidence inventory

"Event time" is the timestamp inside the record. The manifest `captured_at` is the export time, and the two often differ.

| Family | Files | Structure | Identifiers carried | How state is expressed |
|---|---|---|---|---|
| `customer_data_inventory_*.sql` (11) | 63,66,69,72,75,78,81,84,87,90,93 | sqlite tables `customer_profiles`, `customer_search_documents`, `customer_analytics_profiles`, `support_records`, `customer_exports`, `connector_subscriptions`, `backup_restore_entries`, `security_audit_records`, `snapshot_scope` | email, phone, email_hash, `customer_reference`/`subject_reference`=user_id | row present = PRESENT; null identifier columns = redacted/anonymized; `enabled` 0/1; `restore_allowed` 0/1; `retention_until` |
| read / regional / reporting / failover replicas (13) | 05,10,15,20,24,29,33,38,43,47,52 + 54,57 | `customer_data_primary` + `customer_data_{read_replica,replica_ap_south,replica_eu_west,reporting_replica,failover_replica}`; rows hold `record_json`. 54/57 use flat `customer_profiles_*` / `search_documents_primary` | same as above, inside JSON | row in any table = PRESENT in that copy (drift = present only in replica); `account_status`, `enabled`, `restore_allowed`, `message_text:"[REDACTED]"` |
| `customer_data_snapshot_bundle_*.json` (12) | 02,07,12,17,22,26,31,35,40,45,49,51 | `snapshots[]` each with its **own** `captured_at`, `data_store`, `collection`, `records[]`; 22/31/45 also carry `record_type/result` rows under `privacy_treatment_service` / `support_storage_service` / `backup_restore_service` | email, phone, hash, user_id, `subject_selector` | as above; an empty `records: []` = store snapshot proving absence (evidence_51) |
| `service_resource_export_*.json` (11) | 62,65,68,71,74,77,80,83,86,89,92 | `service_exports.{system}.{collection}[]`, each with `observed_at` | email/user_id in `profile`, `hits.documents`, `customer_email`, `customer_reference` | `status_code` 404/200; `profile:null`; `hits.total`; `enabled`; `restore_allowed`; null identifier fields |
| `resource_lookup_trace_*.log` (11) | 64,67,70,73,76,79,82,85,88,91,94 | `*_started` line (lookup_key = email/user_id/null) + `*_completed` line, same `trace_id` | lookup_key, inline email/customer_reference | `rows=0 http_status=404` absent; `result=hit`; `enabled=`; `restore_allowed=`; null identifier fields |
| Observability logs (12) | 01,06,11,16,21,25,30,34,39,44,48,53 | 6 line formats: key=value, JSON, `[LVL] [svc] [trace] event:`, pipe-delimited, RFC5424 syslog, `requestId=… message="…"`. Started line has `lookup_key`/`match_key`/`subject_key`/`scan_key`/`subject_ref`; completed line has `resource_id`; some lines carry `correlation_key` (email, user_id or **email_hash**) or `matched_value` inline | email, user_id, email_hash | `rows/http_status`, `result=hit`, `message_pii_scan`/`attachment_pii_scan` (exposed PII), `analytics_identity_scan direct_identifiers=N`, `connector_heartbeat enabled=true`, `restore_candidate_evaluated eligible=true deletion_block=false`, `contact_lookup_completed email_opt_in=true` |
| record_lookup lists | 03 (txt handover), 08, 41 (log), 13, 36, 50 (json) | `system | resource_id | record_type | result | subject` | email (sometimes UPPERCASE) or user_id | `result` ∈ FOUND / NOT_FOUND / IDENTIFIABLE / ANONYMIZED / REDACTED / ACTIVE / DISABLED / BLOCKED |
| One-off records | 56 analytics report, 59 export jobs, 61 support_replica.log | flat records | email/user_id/hash or nulls | nulls = anonymized; `download_available`; `marker_found=false` |
| PDFs (4) | 18 statement (user_18, NST-2026-0718-1842), 27 legal register (orders for user_08/12/17/21/24/29, no resource ids), 55 compliance journal (audit_record_003, user_03), 58 delivery-correction attachment (support_001, priya.raman) | `pdftotext -layout` is sufficient | email, user_id, document refs | document existence / reference only |
| PNGs (11) | 04 account console (user_11, account_5762, support_9538), 09 search console (search_428 user_13; search_1874 decoy `g.silva76.jr`), 14 connector console (data_flow_1121 jain.aditi, data_flow_9846 lucia-torres), 19 analytics (analytics_7438 user_20), 23 export console (export_1260 ingrid.dahl), 28 support console (support_1034 kagyeman8947, name spelled "Ageyman"), 32 backup console (backup_9287 areed1433 + 3 business decoys), 37 security explorer (user_03 audit_record_001–006), 42 reference registry (user_17 "Retained reference", no PII), 46 object storage (delivery_confirmation_19.pdf …), 60 marketing console (marketing_001 **sj.park@naver.com**, not a known user) | vision / OCR | email, user_id, resource ids | Live/Ready/Healthy/Retained badges |

Each of the 12 LINK_RESOURCE_IDENTITY activities corresponds to one PDF/PNG. They tie it to `user_03,07,09,11,13,14,16,17,18,20,22`, which belong to eval cases 45, 61, 63, 34, 35, 39, 55, 46, 33/52, 50, 53.

Resource-id prefixes: `account, search, analytics, support, export, data_flow, backup, audit_record, tombstone, legal_hold, cache, email_delivery, object, marketing`. `evidence_53` holds 300 `profile_service` lines with 5-digit `account_NNNNN` ids and `correlation_key` emails (bulk noise and decoys).
