"""Executed engineering experiment; not a detector-quality evaluation."""
import argparse,json,tempfile,hashlib,time,uuid,sys,platform
from pathlib import Path
from scripts.engineering_fixture import create
from edge.pipeline import Detector,Pipeline,Source
from edge.state import State
from edge.rules import SafetyRule,associate
p=argparse.ArgumentParser();p.add_argument('--output',default='docs/evidence/component-runtime.json');a=p.parse_args();results={}
with tempfile.TemporaryDirectory(prefix='visionops-evidence-') as tmp:
    folder=create(tmp);model=folder/'simulation.onnx';digest=hashlib.sha256(model.read_bytes()).hexdigest();d=Detector(model,digest);state=State(folder/'state.sqlite');metadata={k:str(uuid.uuid4()) for k in ('camera_id','release_id','model_version_id','config_version_id')}
    pipeline=Pipeline(d,Source(str(folder/'engineering.avi')),metadata,state);start=time.monotonic();pipeline.run();events=state.batch()
    if not events:raise RuntimeError('Synthetic pipeline emitted no rule event')
    if len(events)!=1:raise RuntimeError('Unexpected duplicate rule events')
    results['synthetic_video_onnx_bytetrack_rule_outbox']={'status':'VERIFIED','real_ppe':False,'frames':pipeline.processed,'event_count':len(events),'elapsed_seconds':time.monotonic()-start,'model_sha256':digest,'onnx_latency_ms':pipeline.latencies}
    state.db.close();reopened=State(folder/'state.sqlite')
    if reopened.depth()!=1:raise RuntimeError('outbox_restart_loss')
    eid=events[0]['safety_event_id'];reopened.ack([{'event_id':eid,'status':'duplicate'}])
    if reopened.depth()!=0:raise RuntimeError('duplicate_ack_failed')
    # Close the reopened handle before TemporaryDirectory cleanup; an open SQLite
    # connection makes rmtree fail on Windows (WinError 32).
    reopened.db.close()
    results['outbox_restart_and_duplicate_ack']={'status':'VERIFIED'}
    try:Detector(model,'0'*64)
    except ValueError:results['model_tampering']={'status':'VERIFIED','outcome':'rejected'}
    else:raise RuntimeError('tampered_hash_accepted')
    if associate([0,0,1,1],[('helmet',.9,[.2,.1,.4,.2]),('no_helmet',.9,[.2,.1,.4,.2])])[0]!='unknown':raise RuntimeError('conflicting_heads_not_unknown')
    rule=SafetyRule()
    for i in range(30):
        if rule.observe('s',1,i*.2,'unknown',0,[0,0,1,1]):raise RuntimeError('unknown_created_violation')
    results['unknown_and_conflicting_head_suppression']={'status':'VERIFIED'}
results['environment']={'python':sys.version,'platform':platform.platform(),'fixture_type':'synthetic engineering; constant-output ONNX; no trained PPE evaluation'}
Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(results,indent=2));print(json.dumps(results,indent=2))
