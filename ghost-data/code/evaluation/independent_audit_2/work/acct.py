import sys,re
sys.argv=[sys.argv[0]]+[]  # suppress
import dossier as D
from datetime import timedelta as td
def ana(c, extra=()):
    q=D.req['case_'+c]; rep=D._f(q['reported_at']); us=D.resolve(q['email']); I=D.ident(us)
    hits=[r for r in D.R if D.match(r[2],I)]
    rids=set()
    for r in hits: rids|=set(re.findall(r'\b((?:search|analytics|support|export|data_flow|backup|tombstone|legal_hold)_\d+)\b',r[2]))
    rids|=set(extra)
    ev=sorted([a for a in D.acts if a['resource_id'] in rids and a['source_system']!='data_catalog' and rep-td(minutes=40)<=D._f(a['event_time'])<=rep+td(minutes=200)],key=lambda a:a['event_time'])
    cands=[a for a in D.acts if a['event_type']=='DELETE_RECORD' and a['target_system']=='account_service' and rep-td(minutes=95)<=D._f(a['event_time'])<=rep+td(minutes=20)]
    print('case_'+c,q['reported_status'],'events:',[(a['event_type'][:8],a['resource_id'],a['event_time'][11:]) for a in ev])
    for cd in cands:
        t0=D._f(cd['event_time'])
        offs=[int((D._f(a['event_time'])-t0).total_seconds())%60 for a in ev]
        print('   ',cd['resource_id'],cd['event_time'][11:],cd['reported_result'],'mods',offs, '<== first match' if offs and offs[0] in (40,41,51,52) else '')
for c in ['432','511','545','543','492','420','227','443','211','355','532','52','33','162','505','206','326','491','291','67']: ana(c)
