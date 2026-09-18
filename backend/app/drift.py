from .main import *
from training.drift import js
for p,c,f in [('drift-signals',Drift,['status']),('hard-examples',HardExample,['drift_signal_id','status']),('retraining-requests',Retraining,['status'])]:list_route(p,c,f)
@app.post('/api/v1/drift-signals')
def create_drift(body:dict,req:Request):
    fields=['camera_id','model_version_id','reference_dataset_version_id','window_start','window_end','sample_count','method','score','threshold','status','reference_histogram','current_histogram','reference_window_start','reference_window_end'];body_exact(body,fields,fields)
    with Session() as db:
        u=user(req,db,CV);old=replay(db,req,u,body)
        if old:return old
        if body['method']!='js_confidence_class_v1' or body['threshold']!=.1:fail('invalid_drift_policy',422)
        vals=dict(body)
        for k in ('window_start','window_end','reference_window_start','reference_window_end'):vals[k]=datetime.fromisoformat(vals[k].replace('Z','+00:00'))
        if (vals['window_end']-vals['window_start']).total_seconds()!=86400 or (vals['reference_window_end']-vals['reference_window_start']).total_seconds()!=86400:fail('window_must_be_24_hours',422)
        reference,current=body['reference_histogram'],body['current_histogram'];score=js(reference,current)
        previous=db.scalar(select(Drift).where(Drift.camera_id==body['camera_id'],Drift.model_version_id==body['model_version_id'],Drift.window_end==vals['window_start']))
        enough=min(sum(reference),sum(current))>=500 and body['sample_count']==sum(current)
        vals['score']=score if enough else None;vals['status']='pending_review' if enough and score>.1 and previous and previous.score is not None and previous.score>.1 else 'insufficient_data'
        row=Drift(**vals);db.add(row);db.flush();audit(db,u,'drift_screen',row,req);return save_response(db,req,u,body,out(row))
@app.patch('/api/v1/drift-signals/{signal_id}')
def review_drift(signal_id:str,body:dict,req:Request):
    body_exact(body,['status','reason'],['status','reason'])
    with Session() as db:
        u=user(req,db,CV);r=get(db,Drift,signal_id)
        if r.status!='pending_review' or body['status'] not in ('accepted','dismissed') or not body['reason']:fail('invalid_review_transition')
        r.status=body['status'];r.reason=body['reason'];audit(db,u,'review_drift',r,req);db.commit();return out(r)
@app.post('/api/v1/hard-examples')
async def capture(req:Request):
    with Session() as db:
        u=None;d=None
        if req.headers.get('authorization'):d=device(req,db)
        else:u=user(req,db,CV)
        form=await req.form();m=json.loads(form['metadata']);fields=['camera_id','safety_event_id','drift_signal_id','observed_at','release_id','confidence','capture_reason'];body_exact(m,fields,['camera_id','observed_at','release_id','capture_reason']);c=get(db,Camera,m['camera_id'])
        if d and c.device_id!=d.device_id:fail('camera_scope_denied',403)
        if not db.scalar(select(Assignment).where(Assignment.device_id==c.device_id,Assignment.release_id==m['release_id'])):fail('unassigned_provenance',422)
        data=await form['file'].read(512*1024+1)
        if len(data)>512*1024:fail('size_limit',413)
        import cv2,numpy as np
        if not data.startswith(b'\xff\xd8') or cv2.imdecode(np.frombuffer(data,np.uint8),cv2.IMREAD_COLOR) is None:fail('invalid_jpeg',415)
        if m.get('safety_event_id') and get(db,Safety,m['safety_event_id']).camera_id!=c.camera_id:fail('evidence_scope',422)
        key=hashlib.sha256(data).hexdigest()+'.jpg';idem={**m,'sha256':key}
        if u:
            old=replay(db,req,u,idem)
            if old:return old
        folder=DATA/'evidence';folder.mkdir(exist_ok=True);(folder/key).write_bytes(data);m['observed_at']=datetime.fromisoformat(m['observed_at'].replace('Z','+00:00'));r=HardExample(**m,device_id=c.device_id,evidence_key=key);db.add(r);db.flush()
        if u:return save_response(db,req,u,idem,out(r))
        db.commit();return JSONResponse(jsonable_encoder(out(r)),status_code=201)
@app.get('/api/v1/hard-examples/{example_id}/snapshot')
def hard_snapshot(example_id:str,req:Request):
    with Session() as db:user(req,db,CV);r=get(db,HardExample,example_id);return FileResponse(DATA/'evidence'/r.evidence_key,media_type='image/jpeg')
@app.patch('/api/v1/hard-examples/{example_id}')
def label(example_id:str,body:dict,req:Request):
    body_exact(body,['status','labels','review_reason','exported_dataset_version_id'],['status'])
    with Session() as db:
        u=user(req,db,CV);r=get(db,HardExample,example_id)
        if r.status=='captured' and body['status'] in ('accepted','rejected'):
            if body['status']=='rejected' and not body.get('review_reason'):fail('reason_required',422)
            if body['status']=='accepted':
                if 'labels' not in body:fail('labels_required',422)
                for label in body['labels']:
                    body_exact(label,['class_id','bbox'],['class_id','bbox']);x,y,X,Y=label['bbox']
                    if label['class_id'] not in (0,1,2) or not(0<=x<X<=1 and 0<=y<Y<=1):fail('invalid_label',422)
        elif r.status=='accepted' and body['status']=='exported':get(db,Dataset,body.get('exported_dataset_version_id'))
        else:fail('invalid_transition')
        for k,v in body.items():setattr(r,k,v)
        r.reviewed_by=u.user_id;audit(db,u,'label',r,req);db.commit();return out(r)
@app.post('/api/v1/retraining-requests')
def request_training(body:dict,req:Request):
    body_exact(body,['drift_signal_id','hard_example_ids','reason'],['drift_signal_id','hard_example_ids','reason'])
    with Session() as db:
        u=user(req,db,CV);old=replay(db,req,u,body)
        if old:return old
        signal=get(db,Drift,body['drift_signal_id'])
        if signal.status!='accepted' or not body['hard_example_ids']:fail('unaccepted_examples',422)
        for id in body['hard_example_ids']:
            r=get(db,HardExample,id)
            if r.status not in ('accepted','exported') or r.drift_signal_id!=signal.drift_signal_id:fail('unaccepted_examples',422)
        r=Retraining(**body,requested_by=u.user_id);db.add(r);db.flush();audit(db,u,'request_retraining',r,req);return save_response(db,req,u,body,out(r))
@app.patch('/api/v1/retraining-requests/{request_id}')
def link_training(request_id:str,body:dict,req:Request):
    body_exact(body,['status','training_run_id'],['status'])
    with Session() as db:
        u=user(req,db,CV);r=get(db,Retraining,request_id)
        allowed={'requested':['running','cancelled'],'running':['completed','failed']}
        if body['status'] not in allowed.get(r.status,[]):fail('invalid_transition')
        if body['status'] in ('running','completed'):
            run=get(db,Training,body.get('training_run_id') or r.training_run_id)
            if body['status']=='completed' and run.status!='succeeded':fail('training_not_succeeded')
            for id in r.hard_example_ids:
                if get(db,HardExample,id).exported_dataset_version_id!=run.dataset_version_id:fail('dataset_lineage_mismatch',422)
            r.training_run_id=run.training_run_id
        r.status=body['status'];audit(db,u,'link_retraining',r,req);db.commit();return out(r)
