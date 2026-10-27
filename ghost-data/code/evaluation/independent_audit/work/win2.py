import csv,sys,datetime as dt
sys.path.insert(0,__import__('os').path.dirname(__file__))
from find import reqs
q=reqs[sys.argv[1]]
P=lambda s:dt.datetime.fromisoformat(s.replace('Z','+00:00'))
ra=P(q['reported_at'])
for r in csv.DictReader(open('dataset/system_activity.csv',encoding='utf-8')):
    t=P(r['event_time'])
    if abs((t-ra).total_seconds())<=3.5*3600 or r['event_type'] in('LINK_RESOURCE_IDENTITY',) and False:
        print(r['activity_id'],r['event_time'],r['event_type'],r['target_system'],r['resource_id'],r['reported_result'],r['details'][:110])
