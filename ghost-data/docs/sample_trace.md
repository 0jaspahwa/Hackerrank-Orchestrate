# Phase 1 — Hand trace of all 32 sample cases

Notation:
* `ev_NN Lk` = file line k.
* `ev_92 svc/x` = path inside `service_resource_export`.
* `act_N` = activity_id.
* "Link" = how the resource is tied to the subject.
* "Cite" = why that evidence_id and not another.

Cite-rule check: for every cited step whose resource is observed directly, the cited file is the **latest manifest `captured_at` ≤ `audit_at` that observes that resource_id**. That holds 71/71. The 4 remaining citations are absence-by-omission (marked †).

Rules referenced (`deletion_rules.csv`): r01 USER_PROFILE DELETE, r03 SEARCH DELETE, r05 SUPPORT_MESSAGE REDACT, r06 SUPPORT_ATTACHMENT DELETE, r07 ANALYTICS ANONYMIZE, r09 USER_EXPORT DELETE, r12 SECURITY_AUDIT RETAIN, r14 BACKUP RETAIN+block, r15 TOMBSTONE REDACT. Data flows are not a rule category; they are recreation paths.

---

## Hand-crafted group (case_01–08, 10)

These have no identifiers in activity. The workflow rows use the unique template "Deletion worker reported the requested operation as completed. Target resource: X" within ±10 min of `reported_at`. Account ids follow `account_00N` for `user_0N`.

**case_01** amina (user_01), audit 09-03T11:30 → COMPLETE
1. VERIFY_RECORD_ABSENT account_001, ev_54 †. Link: act_3342 (DELETE SUCCESS, −17 s before reported_at). State: `evidence_54` (failover snapshot 09-02T23:00) lists the full `customer_profiles_primary` and `_failover_replica` tables and account_001 is in neither. Cite: latest full account-store snapshot ≤ audit (ev_89 09-01 only holds per-resource lookups, ev_90 is after audit). r01.

**case_02** mateo (user_02), audit 09-04 → COMPLETE, although the claim was PARTIALLY_COMPLETE
1. VERIFY_RECORD_ABSENT account_002, ev_54 †. Link: act_3376 (PARTIAL "could not confirm", +5 min). Same omission proof as case_01. The PARTIAL claim is overridden by evidence.

**case_04** thandiwe.n (user_04), audit 09-07T10:20 → COMPLETE
1. VERIFY_RECORD_ABSENT account_004, ev_54 †. There is no account activity in the window; the id comes from the `account_00N` convention.
2. VERIFY_RECORD_ANONYMIZED analytics_901, ev_56. Link: act_3463 ANONYMIZE "reported … completed" (−6 min). State: ev_56 records[0] has user_id/email/phone/email_hash all null. r07. Excluded: analytics_824 (dahl.freja, another user, still identifiable).

**case_05** leila (user_05), audit 09-08 → INCOMPLETE (claim COMPLETE)
1. REPORT_REMAINING_RECORD account_005, ev_54. ev_54 L13: row present in `customer_profiles_failover_replica` only, ACTIVE with full PII. **Replica drift**: the primary lacks it. act_3482 (DELETE SUCCESS) and act_3483 (SYNC to failover "final checkpoint") are claims refuted by the snapshot.
2. DELETE_RECORD account_005. 3. VERIFY_RECORD_ABSENT account_005. The replica copy is reported under system `account_service` with the same resource_id; there is no separate replica step.

**case_06** jonas.berg (user_06), audit 09-10T09:15 → INCOMPLETE
1. REPORT_REMAINING_RECORD search_001, ev_57 L9 (`search_documents_primary`, email jonas.berg, `source_resource_id=account_006`, indexed 09-09). act_3546 DELETE FAILED. r03.
2. DELETE_RECORD search_001. 3. VERIFY_RECORD_ABSENT account_006 (no finding; act_3545 SUCCESS, and search doc points to it). 4. VERIFY_RECORD_ABSENT search_001.
Excluded: search_8814 (Jonas **Berglund**), search_4203 (**Joana** Berg). These are similar-name decoys; exact email match is required.

**case_07** priya.raman (user_07), audit 09-12T08:00 → INCOMPLETE
1. REPORT_REMAINING_RECORD support_001, ev_58 (PDF p1 "Support reference CS-4827 / support_001", priya.raman@gmail.com, +91 98765 73142, uploader customer, captured 09-12T07:30). This is an attachment belonging only to the subject, so r06 DELETE, not REDACT. The request text also names "the identity document I sent to support".
2. DELETE_RECORD support_001. 3. VERIFY_RECORD_ABSENT account_007 (act_3590). 4. VERIFY_RECORD_ABSENT support_001.
Excluded: object_19 (delivery_confirmation_19.pdf, act_4007 + ev_46, Feb 2026). Every link to it predates `requested_at` 08-31; it belongs to eval case_61 (audit 02-14).

**case_08** gabriel.costa (user_08), audit 09-14T16:10 → INCOMPLETE
1. REPORT_REMAINING_RECORD export_001, ev_59 records[0] (owner_email gabriel.costa, `download_available:true`, expires 10-04). act_3555 DELETE FAILED. r09.
2. DELETE_RECORD export_001. 3. VERIFY_RECORD_ABSENT account_008 (act_3554). 4. VERIFY_RECORD_ABSENT export_001.
Excluded: export_843 (aoi-tanaka). The legal register PDF lists user_08 order ORD-20264118 but there is no resource_id for it, so it produces no step (see open question Q10).

**case_10** nadia.ionescu (historical email of user_10), audit 09-18T12:25 → INCOMPLETE
1. REPORT_REMAINING_RECORD support_002, ev_61 L4–6. Trace tr-rp-002: lookup_key = the old email, `contact_query_completed resource_id=support_002 rows=1`, `deletion_marker_check marker_found=false`. act_3596 DELETE "completed" is refuted. The deletion worker's op was DELETE, so delete rather than redact; the record is a legacy contact-index entry.
2. DELETE_RECORD support_002. 3. VERIFY_RECORD_ABSENT account_010 (act_3595). 4. VERIFY_RECORD_ABSENT support_002.
Excluded: support_5914 (nadia.**popescu**, similar-email decoy), support_4104 (other user).

## Generated group (case_03, 09, 11–32)

Each has one account (always VERIFY_RECORD_ABSENT) and usually one security audit_record (always VERIFY_RECORD_PRESENT, r12; `retention_until` 2031–2034 > audit).

**case_03** l.fontaine46 (user_43), audit 09-26T11:00 → COMPLETE
1. VERIFY_RECORD_ABSENT account_7566, ev_94 L18–19 (lookup_key=l.fontaine46, rows=0, 404).
2. VERIFY_RECORD_PRESENT audit_record_2016, ev_94 L34–35 (lookup_key user_43, rows=1, retention 2034).
3. VERIFY_RECORD_ANONYMIZED analytics_9406, ev_94 L42–43 (lookup_key=null, all identifiers null). **Link only via act_3852** (ANONYMIZE "Policy action completed", −29 min). Unique in its batch: the other case sharing reported_at 09-15T11:00 is case_44 (charlotte/user_42), and its analytics_5589 is linked to user_42.
Excluded: account_2064, analytics_5589, support_1066, data_flow_223, backup_6266 (case_44/user_42); account_6523 (leonie, March episode).

**case_09** k.appiah88 (user_61), audit 09-16T09:00 → INCOMPLETE
1. REPORT_RESTORE_RISK backup_4978, ev_93 (inventory 09-16T08:37:39, `backup_restore_entries` customer_email=k.appiah88, restore_allowed=1). act_3709 CHECK_RESTORE_ACCESS "can still be restored". r14: retain the archive and block restore. Cite: ev_93 is the latest file whose **manifest** time ≤ audit. ev_06 L83 (event 08:57:31) is later by event time but the file was captured 09-18, so it is not cited.
2. BLOCK_RESTORE backup_4978. 3. VERIFY_RECORD_ABSENT account_3960. 4. VERIFY_RECORD_PRESENT audit_record_4902. 5. VERIFY_RESTORE_BLOCKED backup_4978.
**account_3960 and audit_record_4902 are only linkable through ev_06** (file captured 09-18 > audit, lines timestamped 09-16T08:52–08:57 < audit). See Q1. act_3703 (account DELETE **FAILED**) produces no finding: a failure claim alone is not evidence of presence.

**case_11** novak.tomasz (user_32), audit 10-01T12:00 → INCOMPLETE. All from ev_03 (handover, 10-01T12:00 = audit, inclusive).
* Findings: REMAINING search_052 (L26 FOUND, r03), EXPOSED_PII analytics_2034 (L16 IDENTIFIABLE, r07), EXPOSED_PII support_864 (L46 SUPPORT_MESSAGE IDENTIFIABLE, r05), ACTIVE_DATA_FLOW data_flow_9522 (L25 ACTIVE; act_3817 "remains enabled").
* Remediation: DISABLE data_flow_9522 → DELETE search_052 → REDACT support_864 → ANONYMIZE analytics_2034.
* Verification: ABSENT account_4248 (L11 NOT_FOUND), ABSENT search_052, PRESENT audit_record_9394 (L40), REDACTED support_864, ANONYMIZED analytics_2034, DISABLED data_flow_9522.

**case_12** minjun.kim (user_12; old email minjun.kim90), audit 09-15T12:00 → INCOMPLETE
1. REPORT_REMAINING_RECORD analytics_9914, ev_01 L92: `contact_lookup_completed rows=1 email_opt_in=true correlation_key=44a65154…`, which is the **old** email hash (audit_283). This is a contact/marketing lookup keyed by a secondary identifier, so DELETE, not ANONYMIZE. The deletion worker's own attempt was DELETE_RECORD (act_3435 FAILED).
2. DELETE_RECORD analytics_9914. 3. VERIFY_RECORD_ABSENT account_9797 (L76 404). 4. VERIFY_RECORD_ABSENT analytics_9914. 5. VERIFY_RECORD_PRESENT audit_record_2945 (L63 correlation_key user_12).

**case_13** noor-khalil (user_33), audit 10-01 → INCOMPLETE. All from ev_03.
* REMAINING search_1308 (L27), REMAINING support_4218 (L43 **SUPPORT_ATTACHMENT** FOUND → r06 DELETE).
* DELETE ×2; verify ABSENT account_9335 (L15), ABSENT search_1308, ABSENT support_4218, PRESENT audit_record_4816 (L34).

**case_14** y.tanaka32 (user_37), audit 10-01 → INCOMPLETE (claim COMPLETE). ev_03.
* RESTORE_RISK backup_3870 (L19 BACKUP_ARCHIVE ACTIVE; act_3690). BLOCK_RESTORE.
* Verify ABSENT account_3677 (L9), PRESENT audit_record_987 (L41), BLOCKED backup_3870.

**case_15** lucia-torres (user_15), audit 09-15T12:00 → INCOMPLETE
1. ACTIVE_DATA_FLOW data_flow_9846, ev_01 L88 (`connector_heartbeat enabled=true`, started line subject_key=lucia-torres). Cite ev_01, not ev_14 (screenshot 08-07, older).
2. DISABLE. Verify ABSENT account_4745 (L22 correlation_key=lucia-torres, 404), PRESENT audit_record_1596 (L103), DISABLED data_flow_9846.
Excluded: data_flow_1121 (same screenshot, jain.aditi / user_14).

**case_16** diya.k4436 (user_53), audit 09-21T10:00 → INCOMPLETE. ev_05 (read replica captured 09-21T10:00 = audit).
1. REMAINING account_994: the row is in `customer_data_read_replica` **only** (drift). act_3571 FAILED, act_3577 replication "final checkpoint". r01.
2. REMAINING search_1007: in primary **and** replica, although act_3580 claims DELETE SUCCESS. r03.
3–4. DELETE account_994, DELETE search_1007. Verify ABSENT account_994, ABSENT search_1007, PRESENT audit_record_9528.

**case_17** leonie.g9352 (user_17), audit 09-15T12:00 → INCOMPLETE. ev_01.
* RESTORE_RISK backup_3245 (L53 `restore_candidate_evaluated eligible=true deletion_block=false`; act_3385). BLOCK.
* Verify ABSENT account_4092 (L21), PRESENT audit_record_1797 (L33), BLOCKED backup_3245.
Excluded: account_6523 (ev_67 03-10 404) and tombstone_17 (ev_42 + act_4011, 03-10). All links predate `requested_at` 08-19; they form the March episode, eval case_46. act_3850 (DELETE account_6523, 09-15) carries no identity.

**case_18** mori.ren (user_50), audit 09-26T11:00 → INCOMPLETE. ev_94.
* REMAINING search_4013 (L52–53 hit), EXPOSED analytics_037 (L22–23 email present), EXPOSED support_5569 (L10–11 requester_email present), ACTIVE data_flow_4890 (L24–25 enabled=true).
* Remediation DISABLE → DELETE → REDACT → ANONYMIZE.
* Verify ABSENT account_5521, ABSENT search_4013, PRESENT audit_record_6645, REDACTED support_5569, ANONYMIZED analytics_037, DISABLED data_flow_4890.
* **account_5521 links only via act_3732** (−68 min in a batch of 3 cases). **audit_record_6645 appears nowhere in participant data.**

**case_19** e.varga90 (user_19), audit 09-15T12:00 → INCOMPLETE. ev_01.
* REMAINING search_6083 (L36 hit, correlation_key email); EXPOSED support_9553 (L15 `message_pii_scan matched_value=e.varga90`, a message, so r05 REDACT).
* Verify ABSENT account_9102 (L72), PRESENT audit_record_6793 (L44), REDACTED support_9553, ABSENT search_6083.

**case_20** akosua.gyasi (user_48), audit 09-26T11:00 → INCOMPLETE. ev_94.
* REMAINING search_4262 (L8–9), ACTIVE data_flow_1770 (L40–41).
* Verify ABSENT account_5037, ABSENT search_4262, PRESENT audit_record_5569, DISABLED data_flow_1770.
* account_5037 links only via act_3734 (FAILED, NOT_STARTED claim). **audit_record_5569 appears nowhere.**

**case_21** amara-okeke (user_21), audit 09-15T12:00 → COMPLETE. All cite ev_92.
* ABSENT account_6440 (svc account 404): link only via act_3661 (the single SUCCESS account in the 6-case batch whose only COMPLETE case is case_21).
* PRESENT audit_record_3737 (subject_reference user_21).
* ANONYMIZED analytics_5335 (all null): link only via act_3665.

**case_22** seoyeon-han (user_51), audit 09-21T10:00 → INCOMPLETE. ev_05.
* REMAINING search_8911 (primary + replica, email); EXPOSED analytics_4440 (user_id/email/phone/hash all present, r07). act_3578 ANONYMIZE FAILED.
* Verify ABSENT account_4990 (link via act_3573 only), ABSENT search_8911, PRESENT audit_record_3482, ANONYMIZED analytics_4440.

**case_23** thandiwe.m6866 (user_23), audit 09-15T12:00 → INCOMPLETE. ev_92.
* REMAINING export_7364 (owner_email), REMAINING search_898 (hit).
* Verify ABSENT account_410 (act_3662 only), ABSENT export_7364, ABSENT search_898, PRESENT audit_record_4128. audit_record_4128 is only in ev_02 (file captured 09-17; inner snapshot `captured_at` 09-15T12:00 = audit). See Q1.

**case_24** keiko.nakamura (user_24), audit 09-15T12:00 → INCOMPLETE (claim COMPLETE). ev_92.
* REMAINING account_5334 (status 200 with email), although act_3599 claims SUCCESS. REMAINING search_5577 (hit), although act_3610 claims SUCCESS.
* Verify ABSENT ×2, PRESENT audit_record_7095 (only in ev_02 inner 09-15T12:00).
Not used: legal register PDF lists user_24 (order only, no resource).

**case_25** s.yoon04 (user_25), audit 09-15T12:00 → COMPLETE (claim NOT_STARTED). ev_92.
* ABSENT account_3136 (404; link act_3597 FAILED only), PRESENT audit_record_451, ANONYMIZED analytics_1895 (nulls; link act_3602 only).

**case_26** almeida.mariana (user_26), audit 09-15T12:00 → COMPLETE (claim NOT_STARTED). ev_92.
* ABSENT account_5684 (404 at 09:01:48). ev_02 shows it ACTIVE in an inner snapshot captured 09-09, which is older, and the file itself is post-audit.
* PRESENT audit_record_5416; REDACTED support_4715 (customer_reference only, identifiers null); ANONYMIZED analytics_4566 (act_3603 only); DISABLED data_flow_6244 (enabled=false); BLOCKED backup_3120 (restore_allowed=false).

**case_27** ananya-nair (user_27), audit 09-15T12:00 → COMPLETE. ev_92.
* ABSENT account_3866; PRESENT audit_record_3830; REDACTED support_4707; ANONYMIZED analytics_4574; DISABLED data_flow_2295; BLOCKED backup_5970.
* Decoy: ev_02 shows account_3866 ACTIVE with inner `captured_at` **09-17T12:00**, which is after audit, so it is ignored.

**case_28** smartinez6461 (user_28), audit 09-15T12:00 → COMPLETE. ev_92.
* ABSENT account_5482 (act_3529 only); PRESENT audit_record_9265; REDACTED support_2351; ANONYMIZED analytics_6077 (act_3534 only); DISABLED data_flow_2337; BLOCKED backup_838.
* Excluded: search_8168 (`smartinez6461.jr@gmail.com`, decoy in ev_02).

**case_29** noah.f438085 (historical email of user_29; current noah.f4380), audit 09-15T12:00 → INCOMPLETE. ev_92.
* REMAINING search_1993; EXPOSED analytics_5208 (email present); EXPOSED support_5281 (requester_email present); ACTIVE data_flow_3827 (enabled=true).
* Verify ABSENT account_7012 (act_3532 only), PRESENT audit_record_3954 (ev_02 inner only), REDACTED, ANONYMIZED, DISABLED.

**case_30** camille.bernard86 (historical email of user_30), audit 09-15T12:00 → INCOMPLETE. ev_92. Same shape as case_29: search_9359, analytics_3697, support_6477, data_flow_8524; account_4163 (act_3465 only, unique in its batch); audit_record_1707 (ev_02 inner only).

**case_31** a.eze08 (user_541), audit 09-15T12:00 → COMPLETE. ev_51 (captured 09-15T12:00 = audit).
1. ABSENT account_4857 †. ev_51's `account_service/customer_profiles` snapshot is `records: []`. Link: act_3682 (SUCCESS account in the 09-06 batch, after eliminating 3677→case_14 and 1810→case_40).
2. PRESENT audit_record_6909 (subject user_541).
3. REDACTED tombstone_440 (subject_reference user_541; name/email/phone/hash null; DELETED). r15.

**case_32** addo.kofi (user_542), audit 09-15T12:00 → INCOMPLETE. ev_51.
1. EXPOSED_PII tombstone_9178 (subject_reference kept, but name/email/phone/email_hash still present). r15 REDACT; act_3692 FAILED.
2. REDACT. 3. ABSENT account_8858 (act_3685 PARTIAL ↔ PARTIALLY_COMPLETE claim, the last unassigned account in the batch). 4. PRESENT audit_record_7068. 5. REDACTED tombstone_9178.

## Coverage of the 111 expected (case, resource) pairs

Computed by script, not estimated.

| How the resource is linked to the subject | Count | Pairs |
|---|---|---|
| A. Identifier in a file with manifest captured_at ≤ audit_at (includes `source_resource_id` links such as account_006) | 77 | — |
| B. Identifier only in a file captured after audit_at | 8 | case_09 account_3960 + audit_record_4902 (ev_06 lines at 08:52–08:57 < audit); case_23/24/29/30 audit records and case_26 account_5684 (ev_02 inner snapshot ≤ audit); case_27 account_3866 (ev_02 inner 09-17 > audit, but also resolvable through its activity batch) |
| C. Only a deletion-workflow activity (no identifier anywhere) | 23 | accounts 001/002/007/008/010/5521/5037/6440/4990/410/3136/5482/7012/4163/4857/8858; analytics 9406/901/5335/1895/4566/4574/6077 |
| E. Not present anywhere in participant data | 3 | case_04 account_004 (only the `account_00N` naming convention); case_18 audit_record_6645; case_20 audit_record_5569 |
