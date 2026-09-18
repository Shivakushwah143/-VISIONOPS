"""CPU OpenCV/FFmpeg decode, ONNX Runtime inference and ByteTrack person tracking."""
import ast,json,time,uuid,threading,queue,hashlib
from pathlib import Path
from datetime import datetime,timezone,timedelta
import cv2,numpy as np,onnxruntime as ort,supervision as sv
from .rules import SafetyRule,associate
# Accepted declared source labels per canonical class. Publishers name the same
# concept differently (helmet/Hardhat, no_helmet/NO-Hardhat); validation compares
# normalized labels from the model's own metadata against these synonym sets.
CANONICAL_LABELS={0:{'person','people'},1:{'helmet','hardhat'},2:{'nohelmet','nohardhat'}}
def normalize_label(value):return ''.join(ch for ch in str(value).lower() if ch.isalnum())
class Detector:
    def __init__(self,path,expected_sha256,class_mapping=None):
        actual=hashlib.sha256(Path(path).read_bytes()).hexdigest()
        if actual!=expected_sha256:raise ValueError('model_hash_mismatch')
        self.session=ort.InferenceSession(str(path),providers=['CPUExecutionProvider'])
        self.mapping={int(k):int(v) for k,v in (class_mapping or {0:0,1:1,2:2}).items()}
        if set(self.mapping.values())!={0,1,2}:raise ValueError('class_mapping_incomplete')
        names=self.session.get_modelmeta().custom_metadata_map.get('names')
        if not names:raise ValueError('model_missing_class_metadata')
        names={int(k):v for k,v in ast.literal_eval(names).items()}
        for source,canonical in self.mapping.items():
            declared=names.get(source)
            if declared is None or normalize_label(declared) not in CANONICAL_LABELS[canonical]:raise ValueError('taxonomy_metadata_mismatch')
        self.sources=tuple(sorted(self.mapping));self.input=self.session.get_inputs()[0]
    def infer(self,frame,score_threshold=.35):
        h,w=frame.shape[:2];scale=min(640/w,640/h);nw,nh=round(w*scale),round(h*scale);left,top=(640-nw)//2,(640-nh)//2
        image=np.full((640,640,3),114,np.uint8);image[top:top+nh,left:left+nw]=cv2.resize(frame,(nw,nh));tensor=cv2.cvtColor(image,cv2.COLOR_BGR2RGB).transpose(2,0,1)[None].astype(np.float32)/255
        started=time.perf_counter();raw=self.session.run(None,{self.input.name:tensor})[0];latency=(time.perf_counter()-started)*1000
        predictions=np.squeeze(raw)
        if predictions.ndim!=2:raise ValueError('unsupported_output_shape')
        if predictions.shape[0]<predictions.shape[1]:predictions=predictions.T
        scores=predictions[:,4:]
        if scores.shape[1]<=max(self.sources):raise ValueError('unsupported_output_shape')
        # Remap/filter the full source head to the canonical subset: score only the
        # mapped source columns, which is equivalent to a 3-class subset export graph
        # without risking ONNX graph surgery on the qualified artifact.
        subset=scores[:,list(self.sources)];chosen=subset.argmax(1);conf=subset.max(1);boxes=[];cs=[];ss=[]
        for p,c,s in zip(predictions,chosen,conf):
            if s<score_threshold:continue
            cx,cy,bw,bh=p[:4];box=np.array([(cx-bw/2-left)/scale,(cy-bh/2-top)/scale,(cx+bw/2-left)/scale,(cy+bh/2-top)/scale]);box[[0,2]]=np.clip(box[[0,2]],0,w);box[[1,3]]=np.clip(box[[1,3]],0,h)
            if box[2]<=box[0] or box[3]<=box[1]:continue
            boxes.append(box);cs.append(self.mapping[self.sources[int(c)]]);ss.append(float(s))
        if not boxes:return sv.Detections.empty(),latency
        detections=sv.Detections(xyxy=np.array(boxes),confidence=np.array(ss),class_id=np.array(cs));return detections.with_nms(threshold=.5),latency
class Source:
    def __init__(self,locator,loop=False):
        self.locator=locator;self.loop=loop;self.queue=queue.Queue(2);self.stop=threading.Event();self.status='connecting';self.dropped=0;self.reconnects=0;self.input_frames=0;self.error=None
    def run(self):
        delay=1
        while not self.stop.is_set():
            cap=cv2.VideoCapture(self.locator,cv2.CAP_FFMPEG,[cv2.CAP_PROP_OPEN_TIMEOUT_MSEC,10000,cv2.CAP_PROP_READ_TIMEOUT_MSEC,10000]);session=str(uuid.uuid4());self.status='running' if cap.isOpened() else 'error';seq=0;fps=cap.get(cv2.CAP_PROP_FPS) or 25;next_frame=time.monotonic()
            while cap.isOpened() and not self.stop.is_set():
                ok,frame=cap.read()
                if not ok:break
                if not self.locator.startswith('rtsp:'):
                    self.stop.wait(max(0,next_frame-time.monotonic()));next_frame+=1/fps
                seq+=1;self.input_frames+=1;item=(session,seq,time.monotonic(),datetime.now(timezone.utc),frame)
                if self.queue.full():
                    try:self.queue.get_nowait();self.dropped+=1
                    except queue.Empty:pass
                self.queue.put_nowait(item)
            cap.release()
            if not self.locator.startswith('rtsp:') and not self.loop:self.status='ended' if seq else 'error';return
            self.status='reconnecting';self.reconnects+=1;self.stop.wait(delay);delay=min(delay*2,30)
class Pipeline:
    def __init__(self,detector,source,metadata,state,preview=None):
        self.detector=detector;self.source=source;self.metadata=metadata;self.state=state;self.preview=preview;self.latencies=[];self.processed=0;self.last_frame=None
    def run(self):
        threading.Thread(target=self.source.run,daemon=True).start();rule=SafetyRule();session=None;tracker=None;last=0
        while not self.source.stop.is_set():
            try:sid,seq,decoded,observed,frame=self.source.queue.get(timeout=1)
            except queue.Empty:
                if self.source.status in ('ended','error'):return
                continue
            age=time.monotonic()-decoded
            if age>.5 or time.monotonic()-last<.2:self.source.dropped+=1;continue
            last=time.monotonic()
            if sid!=session:session=sid;tracker=sv.ByteTrack(frame_rate=5);rule=SafetyRule()
            detections,latency=self.detector.infer(frame);self.latencies.append(latency);self.latencies=self.latencies[-1024:];self.processed+=1;self.last_frame=observed
            persons=tracker.update_with_detections(detections[detections.class_id==0]);h,w=frame.shape[:2];heads=[]
            for box,confidence,c in zip(detections.xyxy,detections.confidence,detections.class_id):
                if c in (1,2):heads.append(('helmet' if c==1 else 'no_helmet',float(confidence),(box/np.array([w,h,w,h])).tolist()))
            tracks=[]
            for box,tid in zip(persons.xyxy,persons.tracker_id):
                tracks.append(tid);bbox=(box/np.array([w,h,w,h])).tolist();label,confidence=associate(bbox,heads);event=rule.observe(sid,tid,decoded,label,confidence,bbox)
                if event:
                    span=event.pop('span_seconds');payload={**self.metadata,**event,'kind':'safety','safety_event_id':str(uuid.uuid4()),'stream_session_id':sid,'event_type':'no_helmet_violation','observed_at':observed.isoformat(),'window_start':(observed-timedelta(seconds=span)).isoformat(),'window_end':observed.isoformat()};self.state.enqueue(payload)
                if self.preview:cv2.rectangle(frame,tuple(box[:2].astype(int)),tuple(box[2:].astype(int)),(0,200,0) if label=='helmet' else (0,100,255),2)
            rule.retain(sid,tracks)
            if self.preview:cv2.imwrite(str(self.preview),frame)
