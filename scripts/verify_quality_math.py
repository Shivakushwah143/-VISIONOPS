"""Known-answer arithmetic exercise only; no trained-model quality claim."""
import json
from pathlib import Path
from training.quality import measured,gate
box=[0,0,.4,.4];other=[.6,.6,1,1]
e={'schema_version':1,'evidence_mode':'real','predictions':{'a':[{'class_id':2,'bbox':box,'confidence':.9},{'class_id':2,'bbox':other,'confidence':.8}],'b':[]},'ground_truth':{'a':[{'class_id':2,'bbox':box}],'b':[{'class_id':2,'bbox':box}]},'event_windows':[{'camera_id':'a','session_id':'s','start_seconds':0,'end_seconds':5,'positive':True,'bbox':box},{'camera_id':'a','session_id':'s','start_seconds':6,'end_seconds':10,'positive':True,'bbox':box},{'camera_id':'a','session_id':'s','start_seconds':11,'end_seconds':15,'positive':False,'bbox':box}],'events':[{'camera_id':'a','session_id':'s','observed_seconds':2,'bbox':box},{'camera_id':'a','session_id':'s','observed_seconds':12,'bbox':box}],'inference_latency_ms':[10,20,30,40],'inference_frames':4,'measurement_duration_seconds':1,'peak_rss_bytes':1024,'crashes':0,'dropped_frames':0}
m=measured(e)
for name in ('precision','recall','f1'):
    if m['per_class'][2][name]!=.5:raise RuntimeError(name)
if m['event_precision']!=.5 or m['event_recall']!=.5 or m['latency_p50_ms']!=20 or m['latency_p95_ms']!=40:raise RuntimeError('temporal or percentile calculation')
if not gate(m):raise RuntimeError('insufficient fixture passed promotion')
report={'status':'VERIFIED','scope':'known-answer metric arithmetic only; synthetic inputs never uploaded as real evaluation','checks':['precision','recall','F1','temporal precision/recall','nearest-rank p50/p95','insufficient-evidence rejection']}
Path('docs/evidence/quality-arithmetic.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
