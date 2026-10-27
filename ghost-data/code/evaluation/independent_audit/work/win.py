import csv,sys
sys.path.insert(0,__import__('os').path.dirname(__file__))
from find import reqs
q=reqs[sys.argv[1]]
for r in csv.DictReader(open('dataset/system_activity.csv',encoding='utf-8')):
    if q['requested_at']<=r['event_time']<=q['audit_at'] and r['source_system'] in ('deletion_worker','privacy_intake','data_catalog','backup_service','integration_service') or (r['event_type']=='SYNC_RECORD' and r['target_system'] in('account_replication_cluster','account_failover_replica') and q['requested_at']<=r['event_time']<=q['audit_at']):
        print(r['activity_id'],r['event_time'],r['event_type'],r['target_system'],r['resource_id'],r['reported_result'],r['details'][:90])
