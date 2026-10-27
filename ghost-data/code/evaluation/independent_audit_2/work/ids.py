import csv,hashlib,sys
D='F:/hackerrank/hackerrank-orchestrate-october26/dataset/'
ids=list(csv.DictReader(open(D+'user_identities.csv',encoding='utf-8')))
aud=list(csv.DictReader(open(D+'user_audit_log.csv',encoding='utf-8')))
req={r['case_id']:r for f in ('deletion_requests.csv','sample_deletion_requests.csv') for r in csv.DictReader(open(D+f,encoding='utf-8'))}
def h(e): return hashlib.sha256(e.strip().lower().encode()).hexdigest()
def resolve(email):
    e=email.strip().lower()
    users=set(u['user_id'] for u in ids if u['email'].lower()==e)
    users|=set(a['user_id'] for a in aud if a['field_name']=='email' and e in (a['old_value'].lower(),a['new_value'].lower()))
    users|=set(u['user_id'] for u in ids if u['email_hash']==h(e))
    return users
if __name__=='__main__':
    for c in sys.argv[1:]:
        q=req['case_'+c]
        us=resolve(q['email'])
        print('### case_'+c,q['email'],q['requested_at'],q['reported_at'],q['audit_at'],'users',us)
        for u in us:
            for r in ids:
                if r['user_id']==u: print('  ID',r)
            for a in aud:
                if a['user_id']==u: print('  AUD',a)
