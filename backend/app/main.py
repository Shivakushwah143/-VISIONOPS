import os, json, uuid, secrets, base64, hashlib, time
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.responses import JSONResponse, FileResponse
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select, func, text, tuple_
from sqlalchemy.exc import IntegrityError
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from .db import *
from .security import *
from .schemas import *
REQUESTS=Counter('visionops_http_requests_total','HTTP requests',['method','route','status'])
LATENCY=Histogram('visionops_http_duration_seconds','HTTP request latency',['route'])
EVENTS=Counter('visionops_events_ingested_total','Persisted events',['mode','kind'])
DATA=Path(os.environ.get('DATA_DIR','var')); DATA.mkdir(parents=True,exist_ok=True)
app=FastAPI(title='Industrial VisionOps',version='0.1.0')
@app.exception_handler(HTTPException)
async def http_error(req,e): return JSONResponse({'error':{'code':str(e.detail),'message':str(e.detail),'details':{},'request_id':getattr(req.state,'request_id','')}},status_code=e.status_code)
@app.exception_handler(RequestValidationError)
async def validation_error(req,e): return JSONResponse({'error':{'code':'validation_error','message':'Request fields are invalid','details':{},'request_id':getattr(req.state,'request_id','')}},status_code=422)
@app.exception_handler(IntegrityError)
async def conflict(req,e): return JSONResponse({'error':{'code':'constraint_conflict','message':'Referenced resource or uniqueness constraint failed','details':{},'request_id':getattr(req.state,'request_id','')}},status_code=409)
@app.middleware('http')
async def request_context(req,call_next):
    req.state.request_id=uid(); started=time.monotonic()
    response=await call_next(req)
    route=getattr(req.scope.get('route'),'path','unmatched')
    REQUESTS.labels(req.method,route,str(response.status_code)).inc(); LATENCY.labels(route).observe(time.monotonic()-started)
    response.headers['X-Request-ID']=req.state.request_id
    response.headers['X-Content-Type-Options']='nosniff'
    return response

def out(row): return {'data':public(row) if hasattr(row,'__table__') else row}
def get(db,cls,id):
    r=db.get(cls,str(id))
    if not r: fail('not_found',404)
    return r
def audit(db,u,action,row,req):
    key=list(row.__table__.primary_key)[0].name
    db.add(Audit(actor_type='user',actor_id=u.user_id,action=action,entity_type=row.__tablename__,entity_id=getattr(row,key),request_id=req.state.request_id))
def body_exact(body,allowed,required=()):
    if set(body)-set(allowed) or set(required)-set(body): fail('invalid_fields',422)

def replay(db,req,u,body):
    key=req.headers.get('idempotency-key')
    try: uuid.UUID(key)
    except Exception: fail('idempotency_key_required',422)
    # Serializes concurrent identical keys until this transaction commits.
    lock=int(hashlib.sha256((u.user_id+req.url.path+key).encode()).hexdigest()[:15],16)
    db.execute(text('SELECT pg_advisory_xact_lock(:k)'),{'k':lock})
    r=db.scalar(select(Idempotency).where(Idempotency.user_id==u.user_id,Idempotency.route==req.url.path,Idempotency.key==key))
    if r:
        if r.expires_at<now(): db.delete(r); db.flush(); return None
        if r.request_sha256!=sha(body): fail('idempotency_conflict')
        return JSONResponse(r.response_body,status_code=r.response_status)
def save_response(db,req,u,body,result,status=201):
    result=jsonable_encoder(result)
    db.add(Idempotency(user_id=u.user_id,route=req.url.path,key=req.headers['idempotency-key'],request_sha256=sha(body),response_status=status,response_body=result,expires_at=now()+timedelta(hours=24)))
    db.commit(); return JSONResponse(result,status_code=status)

def assignment(db,d,release,config,reason,actor,campaign=None,increment=True):
    if increment: d.desired_generation+=1
    d.desired_release_id=release; d.desired_config_version_id=config
    db.add(Assignment(device_id=d.device_id,generation=d.desired_generation,release_id=release,config_version_id=config,reason=reason,created_by=actor,deployment_campaign_id=campaign))
def compatible(r,mode,profile):
    if r.status!='approved' or r.evidence_mode!=mode or r.hardware_profile!=profile: fail('incompatible_release',422)
def desired(db,d):
    r=get(db,Release,d.desired_release_id); c=get(db,Config,d.desired_config_version_id)
    permit=True
    if d.active_deployment_campaign_id:
        t=db.scalar(select(Target).where(Target.device_id==d.device_id,Target.deployment_campaign_id==d.active_deployment_campaign_id))
        permit=bool(t and t.permit_expires_at and t.permit_expires_at>now()) or d.actual_release_id==d.desired_release_id
    return dict(device_id=d.device_id,site_id=d.site_id,generation=d.desired_generation,release_id=r.release_id,config_version_id=c.config_version_id,manifest_sha256=r.manifest_sha256,config_sha256=c.sha256,download_permit=permit,issued_at=d.updated_at)
@app.get('/health/live')
def live(): return {'status':'alive'}
@app.get('/health/ready')
def ready():
    try:
        with Session() as db: db.execute(select(func.count()).select_from(Site))
    except Exception: fail('database_unavailable',503)
    return {'status':'ready'}
@app.get('/metrics')
def metrics(): return Response(generate_latest(),media_type=CONTENT_TYPE_LATEST)
@app.post('/api/v1/auth/login')
def login(body:Login,req:Request,response:Response):
    if req.headers.get('origin')!=ORIGIN: fail('origin_denied',403)
    with Session() as db:
        u=db.scalar(select(User).where(User.email==body.email,User.status=='active'))
        try:
            if not u or not passwords.verify(u.password_hash,body.password): raise ValueError()
        except Exception: fail('invalid_credentials',401)
        token=secrets.token_urlsafe(32); csrf=secrets.token_urlsafe(32)
        db.add(UserSession(user_id=u.user_id,token_hash=digest(token),csrf_hash=digest(csrf),expires_at=now()+timedelta(hours=8))); db.commit()
        response.set_cookie('visionops_session',token,httponly=True,secure=ORIGIN.startswith('https:'),samesite='lax',max_age=28800)
        return out({'user':public(u),'csrf_token':csrf})
@app.get('/api/v1/auth/me')
def me(req:Request):
    with Session() as db:
        u=user(req,db)
        # Rotate CSRF only on explicit session lookup; all tabs may re-fetch after 403.
        s=db.scalar(select(UserSession).where(UserSession.token_hash==digest(req.cookies['visionops_session'])))
        csrf=secrets.token_urlsafe(32); s.csrf_hash=digest(csrf); db.commit()
        return out({'user':public(u),'csrf_token':csrf})
@app.post('/api/v1/auth/logout')
def logout(req:Request,response:Response):
    with Session() as db:
        user(req,db); s=db.scalar(select(UserSession).where(UserSession.token_hash==digest(req.cookies['visionops_session']))); db.delete(s); db.commit()
    response.delete_cookie('visionops_session'); return out({'logged_out':True})

def list_route(path,cls,filters=()):
    def endpoint(req:Request,limit:int=50,cursor:str|None=None):
        if not 1<=limit<=200: fail('invalid_limit',422)
        with Session() as db:
            user(req,db,CV if cls in (HardExample,Retraining) else None); pk=list(cls.__table__.primary_key)[0]; q=select(cls); order=getattr(cls,'observed_at',cls.created_at); descending=cls in (Safety,Detection)
            for enum,values in {'mode':['real','simulated'],'evidence_mode':['real','simulated'],'health_status':['unknown','healthy','degraded','unhealthy']}.items():
                if req.query_params.get(enum) and req.query_params[enum] not in values:fail('invalid_filter',422)
            for f in filters:
                if req.query_params.get(f): q=q.where(getattr(cls,f)==req.query_params[f])
            if cursor:
                try:
                    stamp,key=json.loads(base64.urlsafe_b64decode(cursor)); uuid.UUID(key); stamp=datetime.fromisoformat(stamp)
                except Exception: fail('invalid_cursor',422)
                q=q.where(tuple_(order,pk)<(stamp,key) if descending else tuple_(order,pk)>(stamp,key))
            rows=list(db.scalars(q.order_by(order.desc() if descending else order,pk.desc() if descending else pk).limit(limit+1))); more=len(rows)>limit; rows=rows[:limit]
            data=[public(r) for r in rows]
            if cls is Source:
                u=user(req,db)
                if u.role not in OPS:
                    for r in data: r['locator']=urlparse(r['locator']).hostname or Path(r['locator']).name
            return {'data':data,'page':{'limit':limit,'next_cursor':base64.urlsafe_b64encode(json.dumps([getattr(rows[-1],order.key).isoformat(),getattr(rows[-1],pk.name)]).encode()).decode() if more else None}}
    app.add_api_route('/api/v1/'+path,endpoint,methods=['GET'],name='list_'+path)
for path,cls,filters in [('sites',Site,['status']),('devices',Device,['site_id','mode','health_status']),('cameras',Camera,['site_id','device_id']),('video-sources',Source,['camera_id']),('safety-events',Safety,['camera_id','review_status','evidence_mode']),('detections',Detection,['device_id','camera_id']),('models',Model,[]),('model-versions',Version,['model_id']),('releases',Release,['status','hardware_profile']),('deployments',Campaign,['status']),('metrics/summaries',Metric,['device_id','camera_id'])]: list_route(path,cls,filters)
@app.post('/api/v1/sites')
def create_site(body:SiteInput,req:Request):
    try: ZoneInfo(body.timezone)
    except Exception: fail('invalid_timezone',422)
    with Session() as db:
        u=user(req,db,OPS); b=body.model_dump(); old=replay(db,req,u,b)
        if old:return old
        r=Site(**b); db.add(r); db.flush(); audit(db,u,'create',r,req); return save_response(db,req,u,b,out(r))
@app.post('/api/v1/devices')
def create_device(body:DeviceInput,req:Request):
    with Session() as db:
        u=user(req,db,OPS); b=body.model_dump(mode='json'); old=replay(db,req,u,b)
        if old:return old
        r=get(db,Release,b['release_id']); compatible(r,b['mode'],b['hardware_profile']); get(db,Site,b['site_id'])
        d=Device(**{k:v for k,v in b.items() if k!='release_id'},desired_release_id=r.release_id,desired_config_version_id=r.config_version_id)
        db.add(d); db.flush(); assignment(db,d,r.release_id,r.config_version_id,'registration',u.user_id,increment=False); audit(db,u,'create',d,req)
        return save_response(db,req,u,b,out(d))
@app.get('/api/v1/devices/{device_id}')
def detail_device(device_id:str,req:Request):
    with Session() as db:
        user(req,db); d=get(db,Device,device_id)
        h=db.scalar(select(Heartbeat).where(Heartbeat.device_id==device_id).order_by(Heartbeat.received_at.desc()))
        return out({'device':public(d),'last_heartbeat':public(h) if h else None,'desired_state':desired(db,d)})
@app.post('/api/v1/devices/{device_id}/enrollment-tokens')
def issue_token(device_id:str,body:dict,req:Request):
    body_exact(body,['reason'],['reason'])
    with Session() as db:
        u=user(req,db,OPS); d=db.scalar(select(Device).where(Device.device_id==device_id).with_for_update())
        if not d: fail('not_found',404)
        if d.registration_status=='revoked': fail('device_revoked')
        for r in db.scalars(select(Enrollment).where(Enrollment.device_id==device_id,Enrollment.consumed_at==None)): r.consumed_at=now()
        token=secrets.token_urlsafe(32); e=Enrollment(device_id=device_id,token_hash=digest(token),expires_at=now()+timedelta(minutes=10)); db.add(e); db.flush(); audit(db,u,'issue_enrollment',d,req); db.commit()
        return JSONResponse(jsonable_encoder(out({'enrollment_token_id':e.enrollment_token_id,'token':token,'expires_at':e.expires_at})),status_code=201)
@app.post('/api/v1/device-enrollments',status_code=201)
def enroll(body:dict):
    body_exact(body,['token','capabilities'],['token','capabilities'])
    with Session() as db:
        e=db.scalar(select(Enrollment).where(Enrollment.token_hash==digest(body['token'])).with_for_update())
        if not e or e.consumed_at or e.expires_at<now(): fail('expired_or_used_token',401)
        d=get(db,Device,e.device_id)
        if d.registration_status=='revoked': fail('device_revoked',403)
        token=secrets.token_urlsafe(32); d.credential_hash=digest(token); d.registration_status='enrolled'; e.consumed_at=now(); db.commit()
        return out({'device_id':d.device_id,'site_id':d.site_id,'device_credential':token})
@app.patch('/api/v1/devices/{device_id}')
def edit_device(device_id:str,body:dict,req:Request):
    body_exact(body,['name','registration_status'])
    with Session() as db:
        u=user(req,db,OPS); d=get(db,Device,device_id)
        if 'registration_status' in body:
            if body['registration_status']!='revoked': fail('invalid_transition')
            d.registration_status='revoked'; d.credential_hash=None
        if 'name' in body: d.name=SiteInput(name=body['name']).name
        audit(db,u,'update',d,req); db.commit(); return out(d)
@app.get('/api/v1/fleet/summary')
def fleet(req:Request,mode:str|None=None,site_id:str|None=None):
    with Session() as db:
        user(req,db); q=select(Device)
        if mode:
            if mode not in ('real','simulated'):fail('invalid_mode',422)
            q=q.where(Device.mode==mode)
        if site_id:q=q.where(Device.site_id==site_id)
        rows=list(db.scalars(q)); online=[d for d in rows if d.last_heartbeat_at and (now()-d.last_heartbeat_at).total_seconds()<=60]; never=sum(d.last_heartbeat_at is None for d in rows)
        return out(dict(total_records=len(rows),real_records=sum(d.mode=='real' for d in rows),simulated_records=sum(d.mode=='simulated' for d in rows),online=len(online),offline=len(rows)-len(online)-never,never_seen=never,healthy=sum(d.health_status=='healthy' for d in online),degraded=sum(d.health_status in ('degraded','unhealthy') for d in online),desired_actual_mismatch=sum(d.desired_generation!=d.applied_generation or d.desired_release_id!=d.actual_release_id or d.desired_config_version_id!=d.actual_config_version_id for d in rows),generated_at=now()))
@app.get('/api/v1/devices/{device_id}/desired-state')
def get_desired(device_id:str,req:Request):
    with Session() as db:
        d=device(req,db,device_id); state=desired(db,d); tag='"'+sha(jsonable_encoder(state))+'"'
        if req.headers.get('if-none-match')==tag:return Response(status_code=304,headers={'ETag':tag})
        return JSONResponse(jsonable_encoder(out(state)),headers={'ETag':tag})
@app.post('/api/v1/devices/{device_id}/heartbeats')
def heartbeat(device_id:str,body:HeartbeatInput,req:Request):
    if str(body.device_id)!=device_id: fail('device_scope_denied',403)
    with Session() as db:
        d=device(req,db,device_id); db.refresh(d,with_for_update=True)
        boot_id=str(body.boot_id); boot=db.scalar(select(Boot).where(Boot.device_id==device_id,Boot.boot_id==boot_id))
        if boot and boot.retired_at or (d.last_boot_id==boot_id and body.sequence<=d.last_sequence):return out({'accepted':False,'server_time':now(),'desired_generation':d.desired_generation})
        if body.applied_generation>0:
            a=db.scalar(select(Assignment).where(Assignment.device_id==device_id,Assignment.generation==body.applied_generation))
            if not a:fail('unassigned_actual_state',422)
            if a.release_id!=str(body.actual_release_id) or a.config_version_id!=str(body.actual_config_version_id):
                # A local watchdog rollback preserves the committed central generation.
                prior=db.scalar(select(Heartbeat).where(Heartbeat.device_id==device_id,Heartbeat.applied_generation<body.applied_generation,Heartbeat.actual_release_id!=None,Heartbeat.health_status=='healthy').order_by(Heartbeat.received_at.desc()))
                if body.rejected_generation!=body.applied_generation or not prior or prior.actual_release_id!=str(body.actual_release_id) or prior.actual_config_version_id!=str(body.actual_config_version_id):fail('unassigned_actual_state',422)
        elif body.actual_release_id or body.actual_config_version_id:fail('unassigned_actual_state',422)
        for s in body.source_states:
            c=get(db,Camera,s.get('camera_id'))
            if c.device_id!=device_id: fail('camera_scope_denied',403)
        if d.last_boot_id!=boot_id:
            old=db.scalar(select(Boot).where(Boot.device_id==device_id,Boot.boot_id==d.last_boot_id))
            if old:old.retired_at=now()
            if not boot:db.add(Boot(device_id=device_id,boot_id=boot_id))
        d.last_boot_id=boot_id; d.last_sequence=body.sequence; d.last_heartbeat_at=now()
        b=body.model_dump(mode='json',exclude={'metric_summaries'}); b['observed_at']=body.observed_at; b['received_at']=now()
        h=Heartbeat(**b); db.add(h)
        # Old legitimate reports never overwrite a newer committed generation.
        if body.applied_generation>=d.applied_generation:
            for k in ('actual_release_id','actual_config_version_id','applied_generation','agent_state','health_status'):setattr(d,k,b[k])
        for m in body.metric_summaries:
            if m.camera_id and get(db,Camera,m.camera_id).device_id!=device_id:fail('camera_scope_denied',403)
            if not db.get(Metric,str(m.metric_summary_id)):
                vals=m.model_dump(mode='json'); vals.update(window_start=m.window_start,window_end=m.window_end,device_id=device_id,evidence_mode=d.mode,received_at=now()); db.add(Metric(**vals))
        db.commit(); return out({'accepted':True,'server_time':now(),'desired_generation':d.desired_generation})
@app.post('/api/v1/device-events')
async def ingest(req:Request):
    raw=await req.body()
    if len(raw)>1024**2:fail('body_too_large',413)
    body=json.loads(raw); body_exact(body,['events'],['events'])
    if not isinstance(body['events'],list) or len(body['events'])>100:fail('invalid_batch',422)
    with Session() as db:
        d=device(req,db); results=[]
        for e in body['events']:
            eid=e.get('safety_event_id',e.get('detection_event_id','unknown'))
            try:
                with db.begin_nested():
                    parsed=(SafetyInput if e.get('kind')=='safety' else DetectionInput).model_validate(e); cls=Safety if e['kind']=='safety' else Detection
                    camera=get(db,Camera,parsed.camera_id)
                    if camera.device_id!=d.device_id:fail('camera_scope_denied',403)
                    a=db.scalar(select(Assignment).where(Assignment.device_id==d.device_id,Assignment.release_id==str(parsed.release_id),Assignment.config_version_id==str(parsed.config_version_id)))
                    if not a:fail('unassigned_provenance',422)
                    rel=get(db,Release,parsed.release_id); artifact=get(db,ModelArtifact,rel.model_artifact_id)
                    if artifact.model_version_id!=str(parsed.model_version_id):fail('model_provenance_mismatch',422)
                    old=db.get(cls,str(eid)); hash_value=sha(e)
                    if old:
                        if old.payload_sha256!=hash_value:fail('event_id_conflict')
                        status='duplicate'
                    else:
                        vals=parsed.model_dump(mode='json',exclude={'kind'})
                        for k in ('observed_at','window_start','window_end'):
                            if k in vals:vals[k]=getattr(parsed,k)
                        db.add(cls(**vals,device_id=d.device_id,evidence_mode=d.mode,received_at=now(),payload_sha256=hash_value)); db.flush(); status='accepted'
                    results.append({'event_id':str(eid),'status':status})
            except Exception as exc:
                results.append({'event_id':str(eid),'status':'rejected','error_code':str(exc.detail) if isinstance(exc,HTTPException) else 'invalid_event'})
        db.commit()
        for r in results:
            if r['status']=='accepted':EVENTS.labels(d.mode,'event').inc()
        return out({'results':results})
@app.get('/api/v1/safety-events/{event_id}')
def event_detail(event_id:str,req:Request):
    with Session() as db:user(req,db); return out(get(db,Safety,event_id))
@app.patch('/api/v1/safety-events/{event_id}')
def acknowledge(event_id:str,body:dict,req:Request):
    if body!={'review_status':'acknowledged'}:fail('invalid_fields',422)
    with Session() as db:
        u=user(req,db,VIEW); e=get(db,Safety,event_id); e.review_status='acknowledged'; e.acknowledged_by=u.user_id; e.acknowledged_at=now(); audit(db,u,'acknowledge',e,req); db.commit(); return out(e)
@app.post('/api/v1/safety-events/{event_id}/snapshot')
async def upload_snapshot(event_id:str,req:Request):
    form=await req.form(); data=await form['file'].read(512*1024+1)
    if len(data)>512*1024:fail('size_limit',413)
    import cv2,numpy as np
    img=cv2.imdecode(np.frombuffer(data,np.uint8),cv2.IMREAD_COLOR)
    if not data.startswith(b'\xff\xd8') or img is None:fail('invalid_jpeg',415)
    with Session() as db:
        d=device(req,db); e=get(db,Safety,event_id)
        if e.device_id!=d.device_id:fail('device_scope_denied',403)
        key=hashlib.sha256(data).hexdigest()+'.jpg'
        if e.evidence_key and e.evidence_key!=key:fail('different_snapshot')
        folder=DATA/'evidence';folder.mkdir(exist_ok=True);(folder/key).write_bytes(data);e.evidence_key=key;db.commit()
        return out({'safety_event_id':event_id,'evidence_available':True})
@app.get('/api/v1/safety-events/{event_id}/snapshot')
def snapshot(event_id:str,req:Request):
    with Session() as db:
        user(req,db); e=get(db,Safety,event_id)
        if not e.evidence_key:fail('never_uploaded',404)
        if (now()-e.created_at).days>=7:fail('expired',410)
        return FileResponse(DATA/'evidence'/e.evidence_key,media_type='image/jpeg')

def resource_scope(req,db,release_id):
    if req.headers.get('authorization'):
        d=device(req,db)
        if not db.scalar(select(Assignment).where(Assignment.device_id==d.device_id,Assignment.release_id==release_id)):fail('artifact_scope_denied',403)
    else:user(req,db)
@app.get('/api/v1/releases/{release_id}/manifest')
def manifest(release_id:str,req:Request):
    with Session() as db:
        resource_scope(req,db,release_id); r=get(db,Release,release_id)
        if r.status!='approved':fail('release_not_approved',403)
        return out({k:getattr(r,k) for k in ('manifest','manifest_sha256','signature','key_id')})
@app.get('/api/v1/config-versions/{config_id}')
def config(config_id:str,req:Request):
    with Session() as db:
        if req.headers.get('authorization'):
            d=device(req,db)
            if not db.scalar(select(Assignment).where(Assignment.device_id==d.device_id,Assignment.config_version_id==config_id)):fail('config_scope_denied',403)
        else:user(req,db)
        return out(get(db,Config,config_id))
@app.get('/api/v1/{artifact_kind}/{artifact_id}/content')
def download(artifact_kind:str,artifact_id:str,req:Request):
    if artifact_kind not in ('model-artifacts','runtime-artifacts'):fail('not_found',404)
    cls=ModelArtifact if artifact_kind=='model-artifacts' else RuntimeArtifact
    with Session() as db:
        a=get(db,cls,artifact_id)
        if req.headers.get('authorization'):
            d=device(req,db); field=Release.model_artifact_id if cls is ModelArtifact else Release.runtime_artifact_id
            assigned=db.scalar(select(Assignment).join(Release,Assignment.release_id==Release.release_id).where(Assignment.device_id==d.device_id,field==artifact_id))
            if not assigned:fail('artifact_scope_denied',403)
        else:user(req,db)
        return FileResponse(DATA/'artifacts'/a.storage_key,headers={'ETag':'"'+a.sha256+'"'})

def update_sources(db,d,actor):
    if d.active_deployment_campaign_id:fail('campaign_active')
    r=get(db,Release,d.desired_release_id); default=get(db,Config,r.config_version_id)
    settings=dict(default.settings); sources=[]
    for source in db.scalars(select(Source).join(Camera).where(Camera.device_id==d.device_id)):
        vals=public(source); sources.append({k:vals[k] for k in ('camera_id','video_source_id','kind','locator','credential_ref','enabled','loop','source_revision')})
    settings['cameras']=sources
    c=Config(hardware_profile=d.hardware_profile,schema_version=1,settings=settings,sha256=sha(settings),created_by=actor);db.add(c);db.flush()
    assignment(db,d,r.release_id,c.config_version_id,'source_change',actor)
    return c

def validate_locator(kind,locator,loop=False):
    p=urlparse(locator)
    if kind=='rtsp':
        if p.scheme!='rtsp' or not p.hostname or p.username or p.password or loop:fail('invalid_rtsp_source',422)
    else:
        root=Path(os.environ.get('MEDIA_ROOT','/media')).resolve(); path=Path(locator)
        if not path.is_absolute() or not path.resolve().is_relative_to(root):fail('invalid_media_path',422)
@app.post('/api/v1/cameras')
def create_camera(body:CameraInput,req:Request):
    with Session() as db:
        u=user(req,db,OPS); b=body.model_dump(mode='json'); old=replay(db,req,u,b)
        if old:return old
        d=get(db,Device,b['device_id'])
        if d.site_id!=b['site_id']:fail('site_mismatch',422)
        if db.scalar(select(func.count()).select_from(Camera).where(Camera.device_id==d.device_id))>=4:fail('camera_limit',422)
        c=Camera(**b);db.add(c);db.flush();audit(db,u,'create',c,req);return save_response(db,req,u,b,out(c))
@app.post('/api/v1/video-sources')
def create_source(body:SourceInput,req:Request):
    validate_locator(body.kind,body.locator,body.loop)
    with Session() as db:
        u=user(req,db,OPS); b=body.model_dump(mode='json');old=replay(db,req,u,b)
        if old:return old
        c=get(db,Camera,b['camera_id']);d=get(db,Device,c.device_id);db.refresh(d,with_for_update=True)
        s=Source(**b);db.add(s);db.flush();config=update_sources(db,d,u.user_id);audit(db,u,'create',s,req)
        return save_response(db,req,u,b,out({'video_source':public(s),'desired_generation':d.desired_generation,'config_version_id':config.config_version_id}))
@app.patch('/api/v1/video-sources/{source_id}')
def edit_source(source_id:str,body:dict,req:Request):
    body_exact(body,['expected_source_revision','locator','credential_ref','enabled','loop'],['expected_source_revision'])
    with Session() as db:
        u=user(req,db,OPS);s=get(db,Source,source_id);db.refresh(s,with_for_update=True)
        if s.source_revision!=body['expected_source_revision']:fail('revision_conflict')
        for k,v in body.items():
            if k!='expected_source_revision':setattr(s,k,v)
        validate_locator(s.kind,s.locator,s.loop);s.source_revision+=1
        d=get(db,Device,get(db,Camera,s.camera_id).device_id);db.refresh(d,with_for_update=True);c=update_sources(db,d,u.user_id);audit(db,u,'update',s,req);db.commit()
        return out({'video_source':public(s),'desired_generation':d.desired_generation,'config_version_id':c.config_version_id})
# Import route modules after shared functions exist.
from . import lifecycle
from . import campaigns
from . import drift
@app.get('/api/v1/sites/{site_id}')
def site_detail(site_id:str,req:Request):
    with Session() as db:user(req,db);return out(get(db,Site,site_id))
@app.patch('/api/v1/sites/{site_id}')
def site_patch(site_id:str,body:dict,req:Request):
    body_exact(body,['name','status'])
    with Session() as db:
        u=user(req,db,OPS);r=get(db,Site,site_id)
        if 'status' in body:
            if body['status'] not in ('active','archived'):fail('invalid_status',422)
            if body['status']=='archived' and db.scalar(select(Device).where(Device.site_id==site_id,Device.registration_status!='revoked')):fail('active_devices')
            r.status=body['status']
        if 'name' in body:r.name=SiteInput(name=body['name']).name
        audit(db,u,'update_site',r,req);db.commit();return out(r)
@app.patch('/api/v1/cameras/{camera_id}')
def camera_patch(camera_id:str,body:dict,req:Request):
    body_exact(body,['name','status'])
    with Session() as db:
        u=user(req,db,OPS);r=get(db,Camera,camera_id);d=get(db,Device,r.device_id);db.refresh(d,with_for_update=True)
        if d.active_deployment_campaign_id:fail('campaign_active')
        if 'name' in body:r.name=SiteInput(name=body['name']).name
        if 'status' in body:
            if body['status'] not in ('active','disabled'):fail('invalid_status',422)
            r.status=body['status'];s=db.scalar(select(Source).where(Source.camera_id==camera_id))
            if s:s.enabled=r.status=='active';s.source_revision+=1;update_sources(db,d,u.user_id)
        audit(db,u,'update_camera',r,req);db.commit();return out(r)
@app.post('/api/v1/devices/{device_id}/commission')
def recommission(device_id:str,body:dict,req:Request):
    body_exact(body,['release_id','expected_generation','reason'],['release_id','expected_generation','reason'])
    with Session() as db:
        u=user(req,db,M);old=replay(db,req,u,body)
        if old:return old
        d=get(db,Device,device_id);db.refresh(d,with_for_update=True)
        if d.actual_release_id:fail('already_commissioned')
        if d.active_deployment_campaign_id or d.desired_generation!=body['expected_generation']:fail('generation_conflict')
        r=get(db,Release,body['release_id']);compatible(r,d.mode,d.hardware_profile);assignment(db,d,r.release_id,r.config_version_id,body['reason'],u.user_id);audit(db,u,'commission_retry',d,req);return save_response(db,req,u,body,out(d),200)
@app.get('/api/v1/models/{model_id}')
def model_detail(model_id:str,req:Request):
    with Session() as db:
        user(req,db);m=get(db,Model,model_id);versions=list(db.scalars(select(Version).where(Version.model_id==model_id).order_by(Version.created_at.desc()).limit(100)));runs=[get(db,Training,v.training_run_id) for v in versions];datasets={r.dataset_version_id:get(db,Dataset,r.dataset_version_id) for r in runs};ids=[v.model_version_id for v in versions];evaluations=list(db.scalars(select(Evaluation).where(Evaluation.model_version_id.in_(ids))));releases=list(db.scalars(select(Release).join(ModelArtifact,Release.model_artifact_id==ModelArtifact.model_artifact_id).where(ModelArtifact.model_version_id.in_(ids))))
        return out({'model':public(m),'versions':[public(v) for v in versions],'dataset_versions':[public(d) for d in datasets.values()],'training_runs':[public(r) for r in runs],'evaluation_reports':[public(e) for e in evaluations],'releases':[public(r) for r in releases]})
@app.patch('/api/v1/training-runs/{run_id}')
def finish_training(run_id:str,body:dict,req:Request):
    body_exact(body,['status','finished_at'],['status','finished_at'])
    import httpx
    with Session() as db:
        u=user(req,db,CV);r=get(db,Training,run_id)
        if body['status'] not in ('succeeded','failed') or r.status not in ('running',body['status']):fail('invalid_transition')
        try:
            external=httpx.get(os.environ.get('MLFLOW_TRACKING_URI','http://localhost:5000')+'/api/2.0/mlflow/runs/get',params={'run_id':r.mlflow_run_id},timeout=10);external.raise_for_status();status=external.json()['run']['info']['status']
        except Exception:fail('registry_unavailable',503)
        if status!={'succeeded':'FINISHED','failed':'FAILED'}[body['status']]:fail('lineage_mismatch',422)
        r.status=body['status'];r.finished_at=datetime.fromisoformat(body['finished_at'].replace('Z','+00:00'));audit(db,u,'finish_training',r,req);db.commit();return out(r)
@app.post('/api/v1/releases/{release_id}/revoke')
def revoke_release(release_id:str,body:dict,req:Request):
    body_exact(body,['reason'],['reason'])
    with Session() as db:
        u=user(req,db,M);old=replay(db,req,u,body)
        if old:return old
        r=get(db,Release,release_id)
        if db.scalar(select(Device).where((Device.desired_release_id==release_id)|(Device.actual_release_id==release_id))):fail('referenced_active_requires_replacement')
        r.status='revoked';audit(db,u,'revoke',r,req);return save_response(db,req,u,body,out(r),200)
from .telemetry import configure
configure(app)
from prometheus_client import REGISTRY
from .pipeline_metrics import PipelineMetrics
REGISTRY.register(PipelineMetrics())
