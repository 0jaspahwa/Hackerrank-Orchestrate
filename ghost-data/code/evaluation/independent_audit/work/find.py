import csv,sys,hashlib,os,re,glob
D='dataset'
W=os.path.dirname(os.path.abspath(__file__))
ids={r['user_id']:r for r in csv.DictReader(open(D+'/user_identities.csv',encoding='utf-8'))}
aud=list(csv.DictReader(open(D+'/user_audit_log.csv',encoding='utf-8')))
reqs={}
for f in ['deletion_requests.csv','sample_deletion_requests.csv']:
    for r in csv.DictReader(open(D+'/'+f,encoding='utf-8')): reqs[r['case_id']]=r
man=list(csv.DictReader(open(D+'/evidence_files.csv',encoding='utf-8')))
files=[]
for m in man:
    p=D+'/'+m['file_path']
    if p.endswith('.pdf'): p=W+'/pdftext/'+os.path.basename(p)+'.txt'
    if p.endswith('.png'): continue
    files.append((m['file_id'],m['captured_at'],p))
act=open(D+'/system_activity.csv',encoding='utf-8').read().splitlines()
def h(e): return hashlib.sha256(e.strip().lower().encode()).hexdigest()
def terms_for(case):
    q=reqs[case]; em=q['email'].strip().lower()
    uids=set(u for u,r in ids.items() if r['email'].lower()==em)
    uids|=set(a['user_id'] for a in aud if a['field_name']=='email' and em in (a['old_value'].lower(),a['new_value'].lower()))
    T=set([em,h(em)])
    for u in uids:
        r=ids[u]; T|={u,r['name'],r['email'].lower(),r['phone'],r['email_hash'],h(r['email'])}
        for a in aud:
            if a['user_id']==u:
                for v in (a['old_value'],a['new_value']):
                    if v: T.add(v.lower() if '@' in v else v); 
                    if '@' in v: T.add(h(v))
    return q,uids,T
def grep(terms,audit_at=None):
    out=[]
    pats=[t for t in terms if t]
    def m(line):
        l=line.lower()
        for t in pats:
            tl=t.lower()
            if re.fullmatch(r'user_\d+',t):
                if re.search(r'\b'+re.escape(tl)+r'\b',l): return True
            elif re.fullmatch(r'[a-z_]+_\d+',t):
                if re.search(re.escape(tl)+r'(?!\d)',l): return True
            elif tl in l: return True
        return False
    for ln in act:
        if m(ln): out.append(('ACT',ln[:300]))
    for fid,cap,p in files:
        for i,ln in enumerate(open(p,encoding='utf-8').read().splitlines(),1):
            if m(ln): out.append((f'{fid}@{cap}{" LATE" if audit_at and cap>audit_at else ""} L{i}',ln.strip()[:400]))
    return out
if __name__=='__main__':
    case=sys.argv[1]; extra=sys.argv[2:]
    q,uids,T=terms_for(case)
    print(q); print('UIDS',uids)
    for u in uids: print(ids[u]); [print(' ',a) for a in aud if a['user_id']==u]
    print('TERMS',T|set(extra))
    for k,l in grep(T|set(extra),q['audit_at']): print(k,'|',l)
