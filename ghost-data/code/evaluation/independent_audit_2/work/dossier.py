import csv,json,re,sys,os
sys.path.insert(0,os.path.dirname(__file__))
from ids import ids,aud,req,h,resolve
D='F:/hackerrank/hackerrank-orchestrate-october26/dataset/'
man=list(csv.DictReader(open(D+'evidence_files.csv',encoding='utf-8')))
acts=list(csv.DictReader(open(D+'system_activity.csv',encoding='utf-8')))
TRACE=re.compile(r'(?:trace_id[=":]+\s*"?|\[)((?:tr|rb)-[\w-]+)|\s((?:tr|rb)-[\w-]+)\s')
def records():
    out=[]
    for m in man:
        p=D+m['file_path']; fid=m['file_id']
        if p.endswith(('.png','.pdf')): continue
        txt=open(p,encoding='utf-8').read()
        if p.endswith('.json'):
            j=json.loads(txt)
            def walk(o,ctx):
                if isinstance(o,dict):
                    c2=dict(ctx)
                    for k in ('captured_at','data_store','collection','exported_at'):
                        if k in o and isinstance(o[k],str): c2[k]=o[k]
                    if 'resource_id' in o:
                        out.append((fid,m['captured_at'],json.dumps(ctx)+' '+json.dumps(o,ensure_ascii=False),None))
                        return
                    for k,v in o.items(): walk(v,c2)
                elif isinstance(o,list):
                    for v in o: walk(v,ctx)
            walk(j,{})
        else:
            for i,l in enumerate(txt.splitlines()):
                t=None
                mm=re.search(r'((?:tr|rb)-[\w-]+)',l)
                if mm: t=mm.group(1)
                out.append((fid,m['captured_at'],'L%d: '%(i+1)+l,t))
    return out
R=records()
def ident(users):
    s=set()
    for u in users:
        s.add(('w',u))
        for r in ids:
            if r['user_id']==u:
                for k in ('name','email','phone','email_hash'): s.add(('s',r[k].lower()))
        for a in aud:
            if a['user_id']==u:
                s.add(('s',a['old_value'].lower())); s.add(('s',a['new_value'].lower()))
    return s
def match(text,idset):
    tl=text.lower()
    for k,v in idset:
        if not v: continue
        if k=='s' and v in tl: return True
        if k=='w' and re.search(r'\b'+re.escape(v)+r'\b',tl): return True
    return False
def run(c):
    q=req['case_'+c]; us=resolve(q['email']); I=ident(us)
    print('#'*20,'case_'+c,q['email'],'req',q['requested_at'],'rep',q['reported_status'],q['reported_at'],'AUDIT',q['audit_at'],us)
    hits=[r for r in R if match(r[2],I)]
    traces=set((r[0],r[3]) for r in hits if r[3])
    hits+= [r for r in R if r[3] and (r[0],r[3]) in traces and r not in hits]
    rids=set()
    for r in hits:
        rids|=set(re.findall(r'\b((?:account|search|analytics|support|export|data_flow|backup|audit_record|tombstone|legal_hold|object|marketing|cache|telemetry|email_log|delivery)_\d+)\b',r[2]))
    for a in acts:
        if match(a['details'],I): rids.add(a['resource_id']); 
        mm=re.findall(r'\b((?:search|account|support|analytics)_\d+)\b',a['details'])
    print('RIDS',sorted(rids))
    rx=re.compile(r'\b('+'|'.join(map(re.escape,rids))+r')\b') if rids else None
    allr=set(hits)
    if rx: allr|=set(r for r in R if rx.search(r[2]))
    # trace expansion again
    traces=set((r[0],r[3]) for r in allr if r[3])
    allr|=set(r for r in R if r[3] and (r[0],r[3]) in traces)
    for r in sorted(allr,key=lambda r:(r[1],r[0],r[2])):
        flag='LATE ' if r[1]>q['audit_at'] else ''
        print(flag+r[0],r[1],r[2][:700])
    print('--- activities')
    for a in acts:
        if (rx and rx.search(a['resource_id']+' '+a['details'])) or match(a['details'],I):
            flag='LATE ' if a['event_time']>q['audit_at'] else ''
            print(flag+' '.join(a.values()))
if __name__=='__main__':
    for c in sys.argv[1:]: run(c)

from datetime import datetime as _dt, timedelta as _td
_f=lambda s:_dt.strptime(s,'%Y-%m-%dT%H:%M:%SZ')
def window(c):
    q=req['case_'+c]; rep=_f(q['reported_at'])
    same=[x['case_id'] for x in req.values() if x['reported_at']==q['reported_at']]
    print('=== WINDOW case_'+c,'same reported_at:',same)
    for a in acts:
        t=_f(a['event_time'])
        if rep-_td(minutes=90)<=t<=rep+_td(minutes=130) and a['source_system'] in ('deletion_worker','backup_service','data_catalog','privacy_intake') or (rep-_td(minutes=90)<=t<=rep+_td(minutes=130) and a['event_type']=='SYNC_RECORD' and 'replica' in a['target_system']+a['details']):
            mins=(t-rep).total_seconds()/60
            ment=[]
            for r in R:
                if re.search(r'\b'+re.escape(a['resource_id'])+r'\b',r[2]): ment.append(r[0])
            print('  %+5.0fm'%mins,a['activity_id'],a['event_type'],a['target_system'],a['resource_id'],a['reported_result'],'| in:',sorted(set(ment)))
if __name__=='__main__':
    for c in sys.argv[1:]: window(c)
