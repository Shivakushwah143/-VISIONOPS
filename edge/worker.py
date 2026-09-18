"""Signed runtime bundle entrypoint. Candidate warmup produces no safety events."""
import argparse,json,time,threading,sys
from pathlib import Path
from edge.pipeline import Detector,Source,Pipeline
from edge.state import State
p=argparse.ArgumentParser();p.add_argument('--slot',required=True);p.add_argument('--state',required=True);p.add_argument('--status',required=True);a=p.parse_args();slot=Path(a.slot);manifest=json.loads((slot/'manifest.json').read_text());config=json.loads((slot/'config.json').read_text());state=State(a.state)
detector=Detector(slot/'model',manifest['model_sha256'])
import cv2
frame=cv2.imread(str(slot/'bundle'/'warmup.jpg'))
if frame is None:raise ValueError('warmup_image_invalid')
detector.infer(frame)
started=set();pipelines=[]
while True:
    # Activation commit controls event production; candidates only warm up.
    actual=state.get('actual');committed=actual and actual['release_id']==manifest['release_id'] and not state.get('activation')
    if committed:
        for c in config['cameras']:
            if not c['enabled'] or c['camera_id'] in started:continue
            metadata={'camera_id':c['camera_id'],'release_id':manifest['release_id'],'model_version_id':json.loads((slot/'bundle'/'version.json').read_text())['model_version_id'],'config_version_id':actual['config_version_id']}
            pipeline=Pipeline(detector,Source(c['locator'],c['loop']),metadata,state);threading.Thread(target=pipeline.run,daemon=True).start();pipelines.append(pipeline);started.add(c['camera_id'])
    status={'ready':not pipelines or all(p.source.status=='running' for p in pipelines),'cameras':[{'camera_id':p.metadata['camera_id'],'last_frame_at':p.last_frame.isoformat() if p.last_frame else None,'status':p.source.status,'input_frames':p.source.input_frames,'queue_depth':p.source.queue.qsize(),'inference_frames':p.processed,'latency_ms':p.latencies,'dropped_frames':p.source.dropped,'reconnect_count':p.source.reconnects} for p in pipelines]}
    target=Path(a.status);temp=target.with_suffix('.partial');temp.write_text(json.dumps(status));temp.replace(target);time.sleep(2)
