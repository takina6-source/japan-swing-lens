import argparse,json,tarfile,hashlib,datetime
from pathlib import Path
parser=argparse.ArgumentParser(description='Offline same-generation Pages capacity measurement; no publish actions')
parser.add_argument('--pages', type=Path, required=True)
parser.add_argument('--pages-tar', type=Path, required=True)
parser.add_argument('--backup-manifest', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--request', type=Path, default=Path('docs/state-storage/BACKUP_REQUEST.json'))
args=parser.parse_args()
p=args.pages; m=json.loads(args.backup_manifest.read_text())
request=json.loads(args.request.read_text())
assert hashlib.sha256(args.pages_tar.read_bytes()).hexdigest()==request['artifact_tar_sha256']
for kind,expected in request['state_sha256'].items():
    assert hashlib.sha256((p/kind/'state.json').read_bytes()).hexdigest()==expected
    assert m['streams'][kind]['state']['sha256']==expected
with tarfile.open(args.pages_tar) as t:
 files=[{'path':v.name.removeprefix('./'),'bytes':v.size} for v in t if v.isfile()]
bytes_all=sum(x['bytes'] for x in files); logical=sum(v['state']['bytes'] for v in m['streams'].values()); packed=sum(v['compressed_bytes'] for v in m['streams'].values());manifest_bytes=sum(len(json.dumps(v['state'],ensure_ascii=False,indent=2).encode()) for v in m['streams'].values());after=bytes_all-logical+packed+manifest_bytes
rows=json.loads((p/'research/state.json').read_bytes())['research_telemetry']; values=sorted((r['run_date'],r['payload']['storage']['research_state_bytes']) for r in rows)
end=datetime.date.fromisoformat(values[-1][0]);trends={}
for days in (7,30):
 target=end-datetime.timedelta(days=days); before=[r for r in values if datetime.date.fromisoformat(r[0])<=target]
 start=before[-1] if before else values[0]; observed=(end-datetime.date.fromisoformat(start[0])).days;delta=values[-1][1]-start[1]
 # Weekday counts are explicitly a proxy, not actual JP market sessions.
 weekday=sum((datetime.date.fromisoformat(start[0])+datetime.timedelta(days=i)).weekday()<5 for i in range(1,observed+1))
 trends[str(days)]={'start_run_date':start[0],'end_run_date':values[-1][0],'calendar_days':observed,'observations':sum(start[0]<=r[0]<=values[-1][0] for r in values),'delta_bytes':delta,'bytes_per_weekday_proxy':delta/weekday if weekday else None,'weekdays':weekday,'confidence':'LOW: run-date Research logical estimate only; not Pages or compressed history'}
ratio=packed/logical
# Conservative scenarios in MiB/market session, not fitted observations.
scenarios=[]
for total_growth in (2,5,10):
 for state_share in (0.5,1.0):
  compressed_growth=total_growth*((1-state_share)+state_share*ratio)
  scenarios.append({'uncompressed_growth_mib_per_session_assumed':total_growth,'state_growth_share_assumed':state_share,'compressed_growth_mib_per_session':compressed_growth,'uncompressed_sessions_to_1gb':(1000000000/1048576-bytes_all/1048576)/total_growth,'compressed_sessions_to_1gb':(1000000000/1048576-after/1048576)/compressed_growth,'compressed_after_70_sessions_mib':after/1048576+70*compressed_growth,'compressed_after_180_sessions_mib':after/1048576+180*compressed_growth})
result={'source_run':request['run_id'],'commit':request['source_commit'],'artifact_id':request['artifact_id'],'artifact_tar_sha256':hashlib.sha256(args.pages_tar.read_bytes()).hexdigest(),'file_count':len(files),'pages_before_bytes':bytes_all,'states_logical_bytes':logical,'states_compressed_bytes':packed,'pages_after_simulated_bytes':after,'manifest_overhead_bytes':manifest_bytes,'state_reduction_pct':100*(1-packed/logical),'pages_reduction_pct':100*(1-after/bytes_all),'remaining_to_1gb_bytes_before':1000000000-bytes_all,'remaining_after_bytes':1000000000-after,'trends':trends,'scenarios':scenarios,'top_files':sorted(files,key=lambda v:v['bytes'],reverse=True)[:25],'published_storage_metrics':json.loads((p/'research/storage_metrics.json').read_bytes()),'pages_growth_confidence':'UNAVAILABLE: only one frozen complete Pages snapshot; do not label telemetry as actual Pages growth'}
args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2))
print(json.dumps(result,ensure_ascii=False,indent=2))
