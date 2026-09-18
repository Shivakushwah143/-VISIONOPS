"""Recompute detector and temporal metrics from immutable raw evaluation evidence."""
import math
import numpy as np
from .evaluate import ap,iou

def measured(evidence):
    if evidence.get('schema_version')!=1 or evidence.get('evidence_mode')!='real':raise ValueError('real_evidence_required')
    predictions=evidence['predictions'];truth=evidence['ground_truth']
    if not truth or set(predictions)!=set(truth):raise ValueError('image_set_mismatch')
    for records in (predictions,truth):
        for boxes in records.values():
            for d in boxes:
                x,y,X,Y=d['bbox']
                if d['class_id'] not in (0,1,2) or not (0<=x<X<=1 and 0<=y<Y<=1):raise ValueError('invalid_box')
                if 'confidence' in d and not 0<=d['confidence']<=1:raise ValueError('invalid_confidence')
    operating={image:[d for d in boxes if d['confidence']>=.35] for image,boxes in predictions.items()}
    classes=[]
    for cls in range(3):
        aps=[ap(predictions,truth,cls,threshold)[0] for threshold in np.linspace(.5,.95,10)]
        _,precision,recall,support=ap(operating,truth,cls,.5)
        classes.append({'class_id':cls,'precision':precision,'recall':recall,'f1':2*precision*recall/max(precision+recall,1e-12),'ap50':aps[0],'ap50_95':sum(aps)/10 if all(v is not None for v in aps) else None,'support':support})
    confusion=[[0]*4 for _ in range(4)]
    for image,labels in truth.items():
        used=set()
        for detection in sorted(operating[image],key=lambda d:d['confidence'],reverse=True):
            overlap,index=max(((iou(detection['bbox'],label['bbox']),i) for i,label in enumerate(labels) if i not in used),default=(0,-1))
            if overlap>=.5:used.add(index);confusion[labels[index]['class_id']][detection['class_id']]+=1
            else:confusion[3][detection['class_id']]+=1
        for i,label in enumerate(labels):
            if i not in used:confusion[label['class_id']][3]+=1
    windows=evidence['event_windows'];events=evidence['events'];matched=set();tp=fp=0
    for event in sorted(events,key=lambda e:e['observed_seconds']):
        eligible=[i for i,w in enumerate(windows) if w['positive'] and i not in matched and w['camera_id']==event['camera_id'] and w['session_id']==event['session_id'] and w['start_seconds']<=event['observed_seconds']<=w['end_seconds'] and iou(w['bbox'],event['bbox'])>=.5]
        if eligible:matched.add(eligible[0]);tp+=1
        else:fp+=1
    positives=sum(w['positive'] for w in windows);negatives=len(windows)-positives
    latency=evidence['inference_latency_ms']
    if not latency or any(not isinstance(v,(float,int)) or not math.isfinite(v) or v<0 for v in latency):raise ValueError('invalid_latency')
    latency=sorted(latency);duration=evidence['measurement_duration_seconds']
    if duration<=0:raise ValueError('invalid_duration')
    metrics={'per_class':classes,'map50':sum(c['ap50'] for c in classes)/3 if all(c['ap50'] is not None for c in classes) else None,'map50_95':sum(c['ap50_95'] for c in classes)/3 if all(c['ap50_95'] is not None for c in classes) else None,'confusion_matrix':confusion,'event_precision':tp/(tp+fp) if tp+fp else 0.,'event_recall':tp/positives if positives else None,'event_support':positives,'negative_event_windows':negatives,'session_count':len({w['session_id'] for w in windows}),'latency_p50_ms':latency[math.ceil(len(latency)*.5)-1],'latency_p95_ms':latency[math.ceil(len(latency)*.95)-1],'inference_fps':evidence['inference_frames']/duration,'measurement_duration_seconds':duration,'peak_rss_bytes':evidence['peak_rss_bytes'],'peak_vram_bytes':evidence.get('peak_vram_bytes'),'crashes':evidence['crashes'],'dropped_frames':evidence['dropped_frames']}
    return metrics

def gate(metrics):
    reasons=[];bare=metrics['per_class'][2]
    checks={'no_helmet_precision':bare['precision']>=.8,'no_helmet_recall':bare['recall']>=.9,'no_helmet_support':bare['support']>=100,'map50':metrics['map50'] is not None and metrics['map50']>=.7,'map50_95':metrics['map50_95'] is not None and metrics['map50_95']>=.4,'event_precision':metrics['event_precision']>=.8,'event_recall':metrics['event_recall'] is not None and metrics['event_recall']>=.9,'event_support':metrics['event_support']>=50,'negative_windows':metrics['negative_event_windows']>=50,'sessions':metrics['session_count']>=3,'p95_cpu':metrics['latency_p95_ms']<=200,'fps_cpu':metrics['inference_fps']>=5,'rss_cpu':0<metrics['peak_rss_bytes']<=4*1024**3,'crashes':metrics['crashes']==0,'sustained_duration':metrics['measurement_duration_seconds']>=1800}
    return [name for name,passed in checks.items() if not passed]
