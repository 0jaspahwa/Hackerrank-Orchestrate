import json
OPS=['REPORT_REMAINING_RECORD','REPORT_EXPOSED_PII','REPORT_ACTIVE_DATA_FLOW','REPORT_RESTORE_RISK','DISABLE_DATA_FLOW','BLOCK_RESTORE','DELETE_RECORD','REDACT_RECORD','ANONYMIZE_RECORD','VERIFY_RECORD_ABSENT','VERIFY_RECORD_PRESENT','VERIFY_RECORD_REDACTED','VERIFY_RECORD_ANONYMIZED','VERIFY_DATA_FLOW_DISABLED','VERIFY_RESTORE_BLOCKED']
ST={'REPORT_REMAINING_RECORD':'PRESENT','REPORT_EXPOSED_PII':'PRESENT','REPORT_ACTIVE_DATA_FLOW':'ACTIVE','REPORT_RESTORE_RISK':'ACTIVE','DISABLE_DATA_FLOW':'DISABLED','BLOCK_RESTORE':'BLOCKED','DELETE_RECORD':'ABSENT','REDACT_RECORD':'REDACTED','ANONYMIZE_RECORD':'ANONYMIZED','VERIFY_RECORD_ABSENT':'ABSENT','VERIFY_RECORD_PRESENT':'PRESENT','VERIFY_RECORD_REDACTED':'REDACTED','VERIFY_RECORD_ANONYMIZED':'ANONYMIZED','VERIFY_DATA_FLOW_DISABLED':'DISABLED','VERIFY_RESTORE_BLOCKED':'BLOCKED'}
REM={'REPORT_REMAINING_RECORD':'DELETE_RECORD','REPORT_ACTIVE_DATA_FLOW':'DISABLE_DATA_FLOW','REPORT_RESTORE_RISK':'BLOCK_RESTORE'}
VER={'DELETE_RECORD':'VERIFY_RECORD_ABSENT','REDACT_RECORD':'VERIFY_RECORD_REDACTED','ANONYMIZE_RECORD':'VERIFY_RECORD_ANONYMIZED','DISABLE_DATA_FLOW':'VERIFY_DATA_FLOW_DISABLED','BLOCK_RESTORE':'VERIFY_RESTORE_BLOCKED'}
PH=['FINDING','REMEDIATION','VERIFICATION']
def phase(op): return 'FINDING' if op.startswith('REPORT') else ('VERIFICATION' if op.startswith('VERIFY') else 'REMEDIATION')
def build(status,findings,extra_ver,notes,unc):
    steps=[]
    for s,r,op,ev,remop,c,j in findings: steps.append((s,r,op,ev,c,j))
    for s,r,op,ev,remop,c,j in findings: steps.append((s,r,remop,'',c,'Remediation for finding: '+j))
    for s,r,op,ev,remop,c,j in findings: steps.append((s,r,VER[remop],'',c,'Post-remediation check: '+j))
    for s,r,op,ev,c,j in extra_ver: steps.append((s,r,op,ev,c,j))
    steps.sort(key=lambda x:(PH.index(phase(x[2])),OPS.index(x[2]),x[0],x[1]))
    out=[]
    for i,(s,r,op,ev,c,j) in enumerate(steps,1):
        out.append(dict(step_no=i,phase=phase(op),system_id=s,resource_id=r,operation=op,state=ST[op],evidence_id=ev,confidence=c,justification=j))
    return dict(deletion_status=status,steps=out,notes=notes,uncertain_points=unc)
def F(s,r,op,ev,c,j,remop=None): return (s,r,op,ev,remop or REM[op],c,j)
A='account_service'
R={}
# legal hold
R['case_432']=build('INCOMPLETE',[
 F('export_service','export_9343','REPORT_REMAINING_RECORD','evidence_68','high','evidence_68 (service_resource_export 2026-03-20T07:23:49Z) export_9343 owner_email=anair9899@fastmail.com still listed; activity_807 PARTIAL is only a claim'),
 F('search_service','search_3844','REPORT_REMAINING_RECORD','evidence_68','high','evidence_68 index query for anair9899@fastmail.com returns hit search_3844 at 2026-03-20T04:53:49Z'),
],[
 (A,'account_6489','VERIFY_RECORD_ABSENT','','medium','account_6489 (activity_798, -62m, PARTIAL): legal-hold pre-action activity_801 falls +41s mod 60 after it (per-case timing signature) and PARTIAL matches PARTIALLY_COMPLETE; batch-mate case_431 (NOT_STARTED) gets FAILED account_6101'),
 ('legal_service','legal_hold_438','VERIFY_RECORD_PRESENT','','medium','legal_hold_438 subject_reference=user_430 (evidence_40 inner capture 03-20T08:00, bundle exported 03-22 = after audit) + activity_801 RETAIN_RECORD; rule_13 RETAIN'),
 ('security_service','audit_record_3548','VERIFY_RECORD_PRESENT','','medium','audit_record_3548 subject_reference=user_430 only visible in evidence_40 (exported after audit_at); rule_12 RETAIN'),
],'Subject user_430. Export and search entry still present at 2026-03-20T07:23 (evidence_68). Legal hold must be retained.',
 ['Account resource not observed in any evidence; chosen from deletion-workflow timing.','legal_hold_438 and audit_record_3548 only appear in evidence_40 whose manifest captured_at (03-22) is after audit_at; included as required retained records with empty evidence.'])
R['case_511']=build('INCOMPLETE',[
 F(A,'account_1035','REPORT_REMAINING_RECORD','evidence_47','high','evidence_47 (reporting replica captured 2026-02-08T07:00 = audit_at): account_1035 customer_reference user_509, historical email nicolas.f550081@outlook.com, ACTIVE only in customer_data_reporting_replica (absent from primary) = replication drift; activity_271 replication checkpoint'),
],[
 ('legal_service','legal_hold_4887','VERIFY_RECORD_PRESENT','','high','evidence_47 legal_hold_4887 subject_reference user_509 in primary+replica; rule_13 RETAIN'),
 ('security_service','audit_record_4883','VERIFY_RECORD_PRESENT','','high','evidence_47 audit_record_4883 subject_reference user_509; rule_12 RETAIN'),
],'Request email is the historical address of user_509. Account deleted from primary but remains in reporting replica.',[])
# tombstone
R['case_545']=build('COMPLETE',[],[
 (A,'account_2577','VERIFY_RECORD_ABSENT','evidence_52','medium','account_2577 (activity_3718 PARTIAL matches PARTIALLY_COMPLETE; tombstone_946 redaction is +40s mod 60 after it = same-case timing signature); absent from evidence_52 primary and failover tables'),
 ('security_service','audit_record_5041','VERIFY_RECORD_PRESENT','evidence_52','high','evidence_52 audit_record_5041 subject_reference user_545 in primary+failover'),
 ('identity_service','tombstone_946','VERIFY_RECORD_REDACTED','evidence_52','high','evidence_52 tombstone_946 subject_reference user_545, name/email/phone/email_hash all null, DELETED, in primary and failover'),
],'Despite PARTIALLY_COMPLETE claim, evidence_52 (captured at audit_at) shows tombstone properly redacted in both primary and failover; no other copies.',['Account id inferred from workflow timing only.'])
R['case_543']=build('COMPLETE',[],[
 (A,'account_8931','VERIFY_RECORD_ABSENT','evidence_52','medium','account_8931 (activity_3717 SUCCESS) chosen over account_7320 (also SUCCESS, batch-mate case_544 COMPLETE) because tombstone_205 redaction (activity_3721) falls +41s mod 60 after it; absent from evidence_52'),
 ('security_service','audit_record_6244','VERIFY_RECORD_PRESENT','evidence_52','high','evidence_52 audit_record_6244 subject_reference user_543'),
 ('identity_service','tombstone_205','VERIFY_RECORD_REDACTED','evidence_52','high','evidence_52 tombstone_205 subject_reference user_543 with all direct identifiers null in primary and failover'),
],'Tombstone correctly redacted (contrast batch-mate tombstone_8648 of case_544, which still carries name/email/hash).',['Account choice between account_8931 and account_7320 rests on timing signature.'])
# export
R['case_492']=build('INCOMPLETE',[
 F('export_service','export_6406','REPORT_REMAINING_RECORD','evidence_64','high','evidence_64 L6-7 (readback 2026-02-18T06:02) export_6406 available=true owner_email=eamponsah5039@fastmail.com'),
 F('search_service','search_9733','REPORT_REMAINING_RECORD','evidence_64','high','evidence_64 L18-19 search_9733 hit email=eamponsah5039@fastmail.com'),
 F('analytics_platform','analytics_1575','REPORT_EXPOSED_PII','evidence_64','high','evidence_64 L4-5 analytics_1575 email=eamponsah5039@fastmail.com still attributable; rule_07 ANONYMIZE','ANONYMIZE_RECORD'),
 F('support_platform','support_6737','REPORT_EXPOSED_PII','evidence_64','high','evidence_64 L24-25 support_6737 requester_email exposed; worker used REDACT_RECORD (activity_376) and record_type SUPPORT_MESSAGE -> rule_05 REDACT','REDACT_RECORD'),
],[
 (A,'account_3589','VERIFY_RECORD_ABSENT','','high','account_3589 linked to eamponsah5039 (evidence_45 lookup, activity_368); timing signature +52s mod 60 to first post-action'),
 ('security_service','audit_record_9341','VERIFY_RECORD_PRESENT','','medium','audit_record_9341 lookup_value user_490 only in evidence_45 (captured after audit); retained per rule_12'),
],'All workflow actions PARTIAL; readback at 08:39 before audit shows remaining copies.',['audit_record_9341 only seen in post-audit evidence_45.'])
R['case_420']=build('INCOMPLETE',[
 F('export_service','export_389','REPORT_REMAINING_RECORD','evidence_39','high','evidence_39 (syslog captured 2026-03-25T08:59:44, before audit 09:00) export_389 available=true correlation_key=storres487178@fastmail.com (historical email)'),
 F('search_service','search_8472','REPORT_REMAINING_RECORD','evidence_39','high','evidence_39 trace tr-0441096 match_key storres487178@fastmail.com -> search_8472 result=hit'),
],[
 (A,'account_7606','VERIFY_RECORD_ABSENT','','high','evidence_39 trace tr-3269055 account_7606 404 for storres487178; activity_749'),
 ('security_service','audit_record_9687','VERIFY_RECORD_PRESENT','','high','evidence_39 trace tr-9853223 subject_ref user_418 -> audit_record_9687 rows=1'),
],'Spanish deletion request; request email is historical address of user_418.',[])
# support
R['case_227']=build('INCOMPLETE',[
 F('search_service','search_8181','REPORT_REMAINING_RECORD','evidence_80','high','evidence_80 (2026-06-28T06:25:56) search query arthur-fontaine83@proton.me hit search_8181'),
 F('support_platform','support_5830','REPORT_REMAINING_RECORD','evidence_80','high','evidence_80 support_5830 requester_email=arthur-fontaine83@proton.me; ticket attachment (evidence_22 ticket_attachments, worker DELETE_RECORD activity_2360) -> rule_06 DELETE'),
],[
 (A,'account_5549','VERIFY_RECORD_ABSENT','','medium','account_5549 (activity_2349 SUCCESS); all three batch accounts SUCCESS, chosen by timing signature (support_5830 delete +52s, search +21s mod 60)'),
 ('security_service','audit_record_9043','VERIFY_RECORD_PRESENT','','medium','audit_record_9043 subject user_225 only in evidence_22 (exported after audit)'),
],'Historical email arthur-fontaine83 used.',['Account choice by timing signature only.','support_5830 category taken from post-audit bundle + worker action type.'])
R['case_443']=build('INCOMPLETE',[
 F('search_service','search_4925','REPORT_REMAINING_RECORD','evidence_66','high','evidence_66 (inventory 2026-03-10T05:38:48) customer_search_documents search_4925 email seoyeon-choi@proton.me'),
 F('support_platform','support_8341','REPORT_REMAINING_RECORD','evidence_66','medium','evidence_66 support_records support_8341 requester_email seoyeon-choi@proton.me, excerpt about account access (attachment-type pattern), worker attempted DELETE_RECORD (activity_771) -> treated as subject-only attachment (rule_06 DELETE)'),
],[
 (A,'account_1776','VERIFY_RECORD_ABSENT','','medium','account_1776 (activity_769 SUCCESS) chosen over account_4042 (SUCCESS, batch-mate case_444) by timing signature (+52s/+22s mod 60 to support_8341/search_4925 actions)'),
],'No security audit record for user_441 in any evidence, so none verified.',['Support record could be a SUPPORT_MESSAGE (REDACT/EXPOSED_PII) rather than attachment; chose DELETE from worker action + excerpt pattern.','No audit record found.','Account choice by timing.'])
# replica drift
R['case_211']=build('INCOMPLETE',[
 F(A,'account_4348','REPORT_REMAINING_RECORD','evidence_20','high','evidence_20 (read replica captured 2026-07-08T09:00 = audit) customer_data_read_replica has account_4348 user_209 ACTIVE with name/email/phone; missing from primary'),
 F('search_service','search_9955','REPORT_REMAINING_RECORD','evidence_20','high','evidence_20 search_9955 (Kavya Nair, kavya.n9800@outlook.com, user_209) in primary and replica despite activity_2482 SUCCESS claim'),
],[
 ('security_service','audit_record_7804','VERIFY_RECORD_PRESENT','','high','evidence_20 audit_record_7804 subject user_209'),
],'Account remains in read replica; search entry remains despite SUCCESS claim.',[])
R['case_355']=build('INCOMPLETE',[
 F(A,'account_6539','REPORT_REMAINING_RECORD','evidence_33','high','evidence_33 (regional replicas captured 2026-04-24T08:00 = audit) customer_data_replica_eu_west has account_6539 user_353 ACTIVE with PII; absent from primary and ap_south'),
],[
 ('security_service','audit_record_1151','VERIFY_RECORD_PRESENT','','high','evidence_33 audit_record_1151 subject user_353'),
],'Regional replica drift (eu_west).',[])
# restore/backup
R['case_532']=build('COMPLETE',[],[
 (A,'account_470','VERIFY_RECORD_ABSENT','evidence_62','high','evidence_62 (2026-01-29T11:44:59) account_470 status 404; activity_91 SUCCESS; timing signature matches analytics_3744/support_7724 chain'),
 ('security_service','audit_record_4828','VERIFY_RECORD_PRESENT','evidence_62','high','evidence_62 audit_record_4828 subject user_530'),
 ('support_platform','support_7724','VERIFY_RECORD_REDACTED','evidence_62','high','evidence_62 support_7724 customer_reference user_530, requester name/email/phone null'),
 ('analytics_platform','analytics_3744','VERIFY_RECORD_ANONYMIZED','evidence_62','medium','evidence_62 analytics_3744 all identifiers null; linked via activity_92 anonymization +41s mod 60 after account_470 (analytics_893 belongs to batch-mate)'),
 ('integration_service','data_flow_6987','VERIFY_DATA_FLOW_DISABLED','evidence_62','high','evidence_62 data_flow_6987 customer_reference user_530 enabled=false stopped'),
 ('backup_service','backup_6344','VERIFY_RESTORE_BLOCKED','evidence_62','high','evidence_62 backup_6344 customer_reference user_530 restore_allowed=false'),
],'Historical email mahlangu.thabo80 used; backup restore blocked, flow disabled.',['analytics_3744 vs analytics_893 chosen by timing.'])
# multi-request user_18
R['case_52']=build('INCOMPLETE',[
 F('export_service','export_1260','REPORT_REMAINING_RECORD','evidence_23','high','evidence_23 (export console screenshot 2026-06-18T12:00) export_1260 owner ingrid.dahl@outlook.com status Ready; activity_4004 catalog link'),
],[
 (A,'account_2373','VERIFY_RECORD_ABSENT','','low','account_2373 linked to ingrid.dahl only by post-audit evidence_01 / activity_3324 (08-19); no deletion workflow ran before this audit'),
],'Reported COMPLETE at 06-18 but no deletion activity exists before audit 06-19; only pre-audit evidence is the export screenshot.',['Account id only from post-audit evidence.','Account itself was likely still present at audit (deleted 08-19) but no pre-audit evidence proves it, so no account finding emitted.','audit_record_8486/search_2352 only visible 09-15; excluded.'])
R['case_33']=build('INCOMPLETE',[
 F('export_service','export_1260','REPORT_REMAINING_RECORD','evidence_23','medium','evidence_23 export_1260 Ready for ingrid.dahl; no deletion before audit (activity_3328 is 08-19); listed expiry is only a claim'),
 F('export_service','export_1842','REPORT_REMAINING_RECORD','evidence_18','high','evidence_18 (account history PDF 2026-07-18T11:00) statement NST-2026-0718-1842 for Ingrid Dahl user_18; activity_4009 links export_1842'),
],[
 (A,'account_2373','VERIFY_RECORD_ABSENT','','low','account_2373 from post-audit evidence_01 / activity_3324'),
],'Second request of user_18; still no workflow before audit 07-19.',['Whether export_1260 should still be reported (screenshot lists expiry 25 Jun).','Account id only from post-audit evidence; no account finding emitted.'])
# evidence at audit_at
R['case_162']=build('COMPLETE',[],[
 (A,'account_5983','VERIFY_RECORD_ABSENT','evidence_15','medium','account_5983 is the only account deletion at -45..-75 min before reported_at (activity_2944 FAILED = NOT_STARTED pattern); absent from evidence_15 primary and read replica (captured 2026-08-02T07:00 = audit)'),
 ('security_service','audit_record_6362','VERIFY_RECORD_PRESENT','evidence_15','high','evidence_15 audit_record_6362 subject user_160'),
],'Despite NOT_STARTED claim, snapshot at audit_at shows no remaining data.',['Account id from timing window only.'])
R['case_505']=build('INCOMPLETE',[
 F('search_service','search_2591','REPORT_REMAINING_RECORD','evidence_47','high','evidence_47 (captured 2026-02-08T07:00 = audit) search_2591 Nana Addo nana.a7986@zoho.com user_503 in primary and replica'),
 F('integration_service','data_flow_5281','REPORT_ACTIVE_DATA_FLOW','evidence_47','high','evidence_47 data_flow_5281 subject_selector nana.a7986@zoho.com enabled=true continuous_sync; activity_338 shows it repopulated search_2591'),
],[
 (A,'account_8136','VERIFY_RECORD_ABSENT','','medium','account_8136 (activity_334 PARTIAL) chosen over account_7244 (PARTIAL, batch-mate case_504) by timing signature (legal hold +41s, sync +22s, search +51s mod 60)'),
 ('legal_service','legal_hold_2227','VERIFY_RECORD_PRESENT','','high','evidence_47 legal_hold_2227 subject user_503; rule_13'),
 ('security_service','audit_record_3441','VERIFY_RECORD_PRESENT','','high','evidence_47 audit_record_3441 subject user_503'),
],'Active connector recreated search entry after deletion attempt.',['Account id by timing.'])
# historical email
R['case_206']=build('COMPLETE',[],[
 (A,'account_4522','VERIFY_RECORD_ABSENT','evidence_20','medium','account_4522 only SUCCESS candidate in batch (case_207/208 PARTIALLY_COMPLETE -> PARTIAL accounts); analytics_9580 anonymization +40s mod 60 after it; absent from evidence_20'),
 ('security_service','audit_record_9878','VERIFY_RECORD_PRESENT','evidence_20','high','evidence_20 audit_record_9878 subject user_204'),
 ('analytics_platform','analytics_9580','VERIFY_RECORD_ANONYMIZED','evidence_20','medium','evidence_20 analytics_9580 all identifiers null; activity_2512 anonymization tied to this case by timing'),
],'Request uses historical email ama.agyeman84; nothing found under either email or hash at audit.',['Account/analytics linked by timing only.'])
R['case_326']=build('COMPLETE',[],[
 (A,'account_3785','VERIFY_RECORD_ABSENT','evidence_73','high','evidence_73 trace rb-093204-020 lookup HYUNWOO.KANG94@GMAIL.COM -> account_3785 404 (2026-05-09T09:32)'),
 ('security_service','audit_record_1219','VERIFY_RECORD_PRESENT','evidence_73','high','evidence_73 audit_record_1219 subject user_324 rows=1'),
 ('support_platform','support_305','VERIFY_RECORD_REDACTED','evidence_73','high','evidence_73 support_305 customer_reference user_324 requester fields null'),
 ('analytics_platform','analytics_2072','VERIFY_RECORD_ANONYMIZED','evidence_73','medium','evidence_73 analytics_2072 all identifiers null; activity_1597 is +40s mod 60 after account_3785 (analytics_9681 is +25s -> other case)'),
 ('integration_service','data_flow_2468','VERIFY_DATA_FLOW_DISABLED','evidence_73','high','evidence_73 data_flow_2468 customer_reference user_324 enabled=false stopped'),
 ('backup_service','backup_2894','VERIFY_RESTORE_BLOCKED','evidence_73','high','evidence_73 backup_2894 customer_reference user_324 restore_allowed=false'),
],'NOT_STARTED claim but readback before audit shows all treatments applied. Uppercase historical email.',['analytics_2072 vs analytics_9681 by timing.'])
# random
R['case_491']=build('COMPLETE',[],[
 (A,'account_7455','VERIFY_RECORD_ABSENT','evidence_64','high','evidence_64 L32-33 lookup chioma-oladipo@proton.me -> account_7455 404'),
 ('security_service','audit_record_7073','VERIFY_RECORD_PRESENT','evidence_64','high','evidence_64 L38-39 audit_record_7073 subject user_489 rows=1'),
],'search_8910 hit appears only 2026-02-20 (after audit) in evidence_45 and is ignored.',['search_8910 post-audit recreation ignored per time rule.'])
R['case_291']=build('INCOMPLETE',[
 F('backup_service','backup_7079','REPORT_RESTORE_RISK','evidence_76','high','evidence_76 L22-23 backup_7079 archive_present=true restore_allowed=true customer_email n.collins20@gmail.com; activity_1858 confirms restorable'),
],[
 (A,'account_8914','VERIFY_RECORD_ABSENT','','medium','account_8914 (activity_1842 PARTIAL matches PARTIALLY_COMPLETE); CHECK_RESTORE_ACCESS is +52s mod 60 after it'),
],'No security audit record for user_289 found in any evidence.',['Account id by timing.','No audit record verified.'])
R['case_67']=build('INCOMPLETE',[
 F(A,'account_9079','REPORT_REMAINING_RECORD','evidence_93','high','evidence_93 (inventory 2026-09-16T08:37:39) customer_profiles account_9079 rafael.r9464@outlook.com DELETION_PENDING; activity_3641 SUCCESS + activity_3646 replication are claims'),
],[
 ('security_service','audit_record_2877','VERIFY_RECORD_PRESENT','','medium','audit_record_2877 subject user_65 read at 2026-09-16T08:54 in evidence_06 (file captured after audit)'),
],'search_9837 hit only seen 2026-09-18 (after audit) and excluded.',['search_9837 excluded by time rule.','audit_record_2877 only in post-audit file.'])
json.dump(R,open('F:/hackerrank/hackerrank-orchestrate-october26/code/evaluation/independent_audit_2/auditor_rows.json','w',encoding='utf-8'),indent=1,ensure_ascii=False)
for k,v in R.items(): print(k,v['deletion_status'],[(s['phase'][:3],s['resource_id'],s['operation'],s['evidence_id']) for s in v['steps']])
