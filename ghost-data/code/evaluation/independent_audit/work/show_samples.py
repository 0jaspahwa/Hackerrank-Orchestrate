import csv,json
req={r['case_id']:r for r in csv.DictReader(open('dataset/sample_deletion_requests.csv',encoding='utf-8'))}
for r in csv.DictReader(open('dataset/sample_output.csv',encoding='utf-8')):
    q=req[r['case_id']]
    print('=====',r['case_id'],q['email'],q['requested_at'],q['reported_status'],q['reported_at'],q['audit_at'])
    print('  TEXT:',q['request_text'][:200])
    print('  ->',r['deletion_status'])
    for s in json.loads(r['steps_json']):
        print('   ',s['step_no'],s['phase'][:5],s['system_id'],s['resource_id'],s['operation'],s['state'],s['evidence_id'])
