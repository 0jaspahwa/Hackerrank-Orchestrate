import json,re,sys,os
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
from find import *
def matcher(terms):
    pats=[]
    for t in terms:
        if not t: continue
        tl=t.lower()
        if re.fullmatch(r'[a-z_]+_\d+',tl) or re.fullmatch(r'(tr|rb)-[\w-]+',tl): pats.append(re.compile(r'(?<![\w-])'+re.escape(tl)+r'(?![\w])'))
        else: pats.append(re.compile(re.escape(tl)))
    return lambda s: any(p.search(s.lower()) for p in pats)
def jdicts(o,path=''):
    if isinstance(o,dict):
        if 'resource_id' in o or 'subject_reference' in o or 'customer_reference' in o: yield path,o
        else:
            for k,v in o.items(): yield from jdicts(v,path+'/'+k)
    elif isinstance(o,list):
        for i,v in enumerate(o): yield from jdicts(v,path)
def search(terms,audit_at,show_act=True):
    m=matcher(terms); out=[]
    if show_act:
        for ln in act:
            if m(ln): out.append(('ACT',ln[:260]))
    for fid,cap,p in files:
        late=' LATE' if audit_at and cap>audit_at else ''
        txt=open(p,encoding='utf-8').read()
        if p.endswith('.json'):
            for path,d in jdicts(json.loads(txt)):
                s=json.dumps(d,ensure_ascii=False)
                if m(s): out.append((f'{fid}@{cap}{late} {path}',s[:600]))
        else:
            lines=txt.splitlines()
            tids=set()
            for i,ln in enumerate(lines,1):
                if m(ln):
                    tids|=set(re.findall(r'((?:tr|rb)-[0-9][0-9-]*)',ln))
            for i,ln in enumerate(lines,1):
                tl=re.findall(r'((?:tr|rb)-[0-9][0-9-]*)',ln)
                if m(ln) or (tl and tl[0] in tids):
                    out.append((f'{fid}@{cap}{late} L{i}',ln.strip()[:450]))
    return out
if __name__=='__main__':
    case=sys.argv[1]; extra=[a for a in sys.argv[2:] if a!='-noact']
    q,uids,T=terms_for(case)
    print({k:q[k] for k in q if k!='request_text'})
    for u in uids: print(ids[u]); [print(' ',a) for a in aud if a['user_id']==u]
    for k,l in search(T|set(extra),q['audit_at'],'-noact' not in sys.argv): print(k,'|',l)
