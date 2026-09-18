"""Measured detector evaluation on explicitly labeled, locally supplied images."""
import argparse,json,time,hashlib,math,resource
from pathlib import Path
import numpy as np,cv2
from edge.pipeline import Detector

def iou(a,b):
    x=max(a[0],b[0]);y=max(a[1],b[1]);X=min(a[2],b[2]);Y=min(a[3],b[3]);inter=max(0,X-x)*max(0,Y-y)
    return inter/max((a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-inter,1e-12)
def ap(records,truth,cls,threshold):
    predictions=sorted([(d['confidence'],image,d['bbox']) for image,items in records.items() for d in items if d['class_id']==cls],reverse=True)
    total=sum(sum(d['class_id']==cls for d in items) for items in truth.values());used=set();tp=[];fp=[]
    for confidence,image,box in predictions:
        matches=[(iou(box,d['bbox']),i) for i,d in enumerate(truth[image]) if d['class_id']==cls and (image,i) not in used];best=max(matches,default=(0,-1))
        hit=best[0]>=threshold
        if hit:used.add((image,best[1]))
        tp.append(int(hit));fp.append(int(not hit))
    tp=np.cumsum(tp);fp=np.cumsum(fp);recall=tp/max(total,1);precision=tp/np.maximum(tp+fp,1)
    area=sum(max(precision[recall>=r],default=0) for r in np.linspace(0,1,101))/101 if total else None
    return area,float(precision[-1]) if len(precision) else 0,float(recall[-1]) if len(recall) else 0,total
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--model',required=True);p.add_argument('--sha256',required=True);p.add_argument('--samples',required=True,help='JSON list: image, labels=[{class_id,bbox normalized}], group');p.add_argument('--output',required=True);p.add_argument('--external-baseline',action='store_true');a=p.parse_args();samples=json.loads(Path(a.samples).read_text());detector=Detector(a.model,a.sha256,{11:0,3:1,8:2} if a.external_baseline else None);records={};truth={};latencies=[];started=time.perf_counter()
    for s in samples:
        path=Path(s['image']);frame=cv2.imread(str(path))
        if frame is None:raise ValueError('unreadable_evaluation_image')
        if not s.get('group'):raise ValueError('missing_evaluation_group')
        det,ms=detector.infer(frame,score_threshold=.001);h,w=frame.shape[:2];latencies.append(ms);key=str(path)
        records[key]=[{'class_id':int(c),'confidence':float(score),'bbox':(box/np.array([w,h,w,h])).tolist()} for box,score,c in zip(det.xyxy,det.confidence,det.class_id)];truth[key]=s['labels']
    classes=[]
    for c in range(3):
        aps=[ap(records,truth,c,t)[0] for t in np.arange(.5,.951,.05)];_,precision,recall,support=ap(records,truth,c,.5)
        classes.append({'class_id':c,'precision':precision,'recall':recall,'f1':2*precision*recall/max(precision+recall,1e-12),'ap50':aps[0],'ap50_95':sum(x for x in aps if x is not None)/len(aps) if all(x is not None for x in aps) else None,'support':support})
    values=[c['ap50'] for c in classes];map50=sum(values)/3 if all(x is not None for x in values) else None
    report={'evidence_mode':'real','model_sha256':a.sha256,'dataset_samples_sha256':hashlib.sha256(Path(a.samples).read_bytes()).hexdigest(),'per_class':classes,'map50':map50,'map50_95':sum(c['ap50_95'] for c in classes)/3 if all(c['ap50_95'] is not None for c in classes) else None,'latency_p50_ms':float(np.percentile(latencies,50)),'latency_p95_ms':float(np.percentile(latencies,95,method='higher')),'inference_fps':len(latencies)/(time.perf_counter()-started),'peak_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,'peak_vram_bytes':None,'measurement_duration_seconds':time.perf_counter()-started,'event_precision':None,'event_recall':None,'event_support':None,'result':'blocked','block_reason':'Temporal event evaluation, full benchmark protocol, confusion matrix, and promotion evidence ingestion are not completed by this detector-only command. Confidence-filtered AP uses the configured operating threshold; not a full low-threshold COCO AP qualification.','predictions':records,'ground_truth':truth}
    Path(a.output).write_text(json.dumps(report,indent=2));print('Measured detector report written. Real release promotion remains blocked until full quality evidence is qualified.')
