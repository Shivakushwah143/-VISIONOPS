"""Persistent campaign controller: unknown telemetry never passes a gate."""
import math
from .main import *
from .permits import allocate
def timeline(db,c,event,reason,actor='controller',device_id=None):
    db.add(DeploymentEvent(deployment_campaign_id=c.deployment_campaign_id,device_id=device_id,actor_type='system' if actor=='controller' else 'user',actor_id=actor,event_type=event,reason=reason,before_state={},after_state={'status':c.status,'ring':c.current_ring},evidence={}))
def aggregate(db,d,since):
    rows=list(db.scalars(select(Metric).where(Metric.device_id==d.device_id,Metric.received_at>=since,Metric.release_id==d.actual_release_id).order_by(Metric.received_at)))
    samples=[float(x) for r in rows for x in r.values.get('inference_latency_ms',[])]
    fps=[r.values.get('inference_fps') for r in rows if r.values.get('inference_fps') is not None]
    if not rows or not samples or not fps:return None
    if (now()-rows[-1].received_at).total_seconds()>30:return None
    if any((b.window_start-a.window_end).total_seconds()>30 for a,b in zip(rows,rows[1:])):return None
    samples.sort()
    return {'p95':samples[math.ceil(.95*len(samples))-1],'fps':sum(fps)/len(fps),'samples':len(samples),'restarts':sum(r.values.get('restart_count',0) for r in rows),'first':rows[0].window_start.isoformat(),'last':rows[-1].received_at.isoformat(),'duration':(rows[-1].window_end-rows[0].window_start).total_seconds()}
def eligible(db,d,r):
    reasons=[]
    if d.mode!=r.evidence_mode or d.hardware_profile!=r.hardware_profile:reasons.append('incompatible_profile')
    if d.registration_status!='enrolled' or not d.last_heartbeat_at or (now()-d.last_heartbeat_at).total_seconds()>30:reasons.append('offline_or_unknown')
    if d.health_status!='healthy':reasons.append('not_healthy')
    if d.applied_generation!=d.desired_generation or d.actual_release_id!=d.desired_release_id or d.actual_config_version_id!=d.desired_config_version_id:reasons.append('not_converged')
    metrics=aggregate(db,d,now()-timedelta(minutes=6))
    if not metrics or metrics['duration']<300 or metrics['samples']<100:reasons.append('insufficient_baseline')
    return reasons,metrics

def targets(db,c):return list(db.scalars(select(Target).where(Target.deployment_campaign_id==c.deployment_campaign_id).order_by(Target.device_id)))
def assign_ring(db,c):
    r=get(db,Release,c.release_id)
    for t in targets(db,c):
        if t.ring>c.current_ring or t.assigned_generation:continue
        d=get(db,Device,t.device_id); reasons,_=eligible(db,d,r)
        if reasons:fail('eligibility_changed')
        default=get(db,Config,r.config_version_id);previous=get(db,Config,d.desired_config_version_id)
        settings={**default.settings,'cameras':previous.settings['cameras']}
        config=Config(hardware_profile=d.hardware_profile,schema_version=1,settings=settings,sha256=sha(settings),created_by=c.created_by);db.add(config);db.flush()
        assignment(db,d,r.release_id,config.config_version_id,'campaign',c.created_by,c.deployment_campaign_id)
        t.assigned_generation=d.desired_generation;t.assigned_config_version_id=config.config_version_id;t.status='assigned'
        timeline(db,c,'assigned','ring assignment',device_id=d.device_id)
    c.ring_started_at=now();c.observation_started_at=None;c.failure_windows=0

def assess(db,c):
    ts=[t for t in targets(db,c) if t.assigned_generation]
    if not ts:return 'waiting',['no_assignments']
    reasons=[];failed=False;converged=True
    for t in ts:
        d=get(db,Device,t.device_id)
        if not d.last_heartbeat_at or (now()-d.last_heartbeat_at).total_seconds()>30:
            reasons.append('telemetry_stale');converged=False
            if not d.last_heartbeat_at or (now()-d.last_heartbeat_at).total_seconds()>60:failed=True
        if d.applied_generation!=t.assigned_generation or d.actual_release_id!=c.release_id or d.actual_config_version_id!=t.assigned_config_version_id:converged=False;reasons.append('awaiting_convergence')
        if d.health_status in ('unhealthy','degraded'):failed=True;reasons.append('unhealthy')
        elif d.health_status!='healthy':converged=False;reasons.append('health_unknown')
        h=db.scalar(select(Heartbeat).where(Heartbeat.device_id==d.device_id).order_by(Heartbeat.received_at.desc()))
        if h and h.rejected_generation==t.assigned_generation:failed=True;reasons.append('generation_rejected');t.status='failed'
    if not converged:
        c.observation_started_at=None
        if c.ring_started_at and (now()-c.ring_started_at).total_seconds()>600:failed=True;reasons.append('convergence_timeout')
        return ('failed' if failed else 'waiting'),sorted(set(reasons))
    if not c.observation_started_at:c.observation_started_at=now()
    for t in ts:
        d=get(db,Device,t.device_id);m=aggregate(db,d,c.observation_started_at)
        if not m or m['samples']<100 or m['duration']<300:reasons.append('insufficient_observation');continue
        b=t.baseline_metrics
        if m['p95']>min(200,b['p95']*1.2) or m['fps']<max(5,b['fps']*.9) or m['restarts']>0:failed=True;reasons.append('performance_regression')
        t.status='healthy' if not failed else 'failed'
    return ('failed' if failed else 'waiting' if reasons else 'ready'),sorted(set(reasons))
@app.post('/api/v1/deployments')
def create_campaign(body:dict,req:Request):
    body_exact(body,['release_id','target_device_ids','reason'],['release_id','target_device_ids','reason']);ids=body['target_device_ids']
    if len(ids)<10 or len(ids)!=len(set(ids)):fail('invalid_targets',422)
    with Session() as db:
        u=user(req,db,M);old=replay(db,req,u,body)
        if old:return old
        r=get(db,Release,body['release_id'])
        if r.status!='approved':fail('release_not_approved')
        c=Campaign(release_id=r.release_id,mode=r.evidence_mode,target_device_ids=sorted(ids),created_by=u.user_id,reason=body['reason'],policy_snapshot={'rings':[.1,.25,1],'observation_seconds':300,'min_samples':100,'convergence_seconds':600});db.add(c);db.flush();elig=[]
        for i,id in enumerate(sorted(ids)):
            d=get(db,Device,id);reasons,metrics=eligible(db,d,r);elig.append({'device_id':id,'eligible':not reasons,'reasons':reasons})
            ring=0 if i<math.ceil(len(ids)*.1) else 1 if i<math.ceil(len(ids)*.25) else 2
            db.add(Target(deployment_campaign_id=c.deployment_campaign_id,device_id=id,ring=ring,baseline_metrics={}))
        timeline(db,c,'created',body['reason'],u.user_id)
        return save_response(db,req,u,body,out({'campaign':public(c),'eligibility':elig}))
@app.get('/api/v1/deployments/{campaign_id}')
def campaign_detail(campaign_id:str,req:Request):
    with Session() as db:
        user(req,db);c=get(db,Campaign,campaign_id);status,reasons=assess(db,c) if c.status in ('running','paused') else ('waiting',[])
        ts=targets(db,c);counts={s:sum(t.status==s for t in ts) for s in set(t.status for t in ts)}
        return out({'campaign':public(c),'counts':counts,'gate_status':status,'gate_reasons':reasons,'observed_at':now()})
@app.get('/api/v1/deployments/{campaign_id}/targets')
def target_list(campaign_id:str,req:Request):
    with Session() as db:user(req,db);return {'data':[public(t) for t in targets(db,get(db,Campaign,campaign_id))],'page':{'next_cursor':None,'limit':200}}
@app.get('/api/v1/deployments/{campaign_id}/events')
def campaign_events(campaign_id:str,req:Request):
    with Session() as db:
        user(req,db);return {'data':[public(t) for t in db.scalars(select(DeploymentEvent).where(DeploymentEvent.deployment_campaign_id==campaign_id).order_by(DeploymentEvent.created_at.desc()).limit(200))],'page':{'next_cursor':None,'limit':200}}
def campaign_command(campaign_id:str,command:str,body:dict,req:Request):
    body_exact(body,['reason','expected_status','expected_ring'],['reason'])
    with Session() as db:
        u=user(req,db,M);old=replay(db,req,u,body)
        if old:return old
        c=db.scalar(select(Campaign).where(Campaign.deployment_campaign_id==campaign_id).with_for_update())
        if not c:fail('not_found',404)
        ts=targets(db,c);r=get(db,Release,c.release_id)
        for t in ts:db.refresh(get(db,Device,t.device_id),with_for_update=True)
        if command=='start':
            if c.status!='draft' or body.get('expected_status')!='draft':fail('invalid_transition')
            for t in ts:
                d=get(db,Device,t.device_id);reasons,m=eligible(db,d,r)
                if reasons or d.active_deployment_campaign_id:fail('eligibility_changed')
                t.previous_release_id=d.actual_release_id;t.previous_config_version_id=d.actual_config_version_id;t.baseline_metrics=m;d.active_deployment_campaign_id=c.deployment_campaign_id
            c.status='running';assign_ring(db,c)
        elif command=='advance':
            if c.status!='running' or body.get('expected_ring')!=c.current_ring:fail('stale_ring')
            status,_=assess(db,c)
            if status!='ready':fail('gates_not_ready')
            if c.current_ring==2:
                c.status='completed'
                for t in ts:get(db,Device,t.device_id).active_deployment_campaign_id=None
            else:c.current_ring+=1;assign_ring(db,c)
        elif command=='pause':
            if c.status not in ('running','paused'):fail('invalid_transition')
            c.status='paused'
        elif command=='resume':
            if c.status!='paused':fail('invalid_transition')
            status,_=assess(db,c)
            if status=='failed':fail('unresolved_failure')
            c.status='running';c.failure_windows=0
        elif command=='cancel':
            if c.status!='draft':fail('invalid_transition')
            c.status='cancelled'
        elif command=='rollback':
            if c.status not in ('running','paused','completed','rolling_back','rollback_incomplete'):fail('invalid_transition')
            for t in ts:
                if not t.assigned_generation:continue
                d=get(db,Device,t.device_id)
                if d.active_deployment_campaign_id not in (None,c.deployment_campaign_id) or d.desired_generation not in (t.assigned_generation,t.rollback_generation):fail('newer_assignment')
                d.active_deployment_campaign_id=c.deployment_campaign_id
                if t.rollback_generation is None:
                    assignment(db,d,t.previous_release_id,t.previous_config_version_id,'rollback',u.user_id,c.deployment_campaign_id);t.rollback_generation=d.desired_generation
                t.status='rollback_pending';t.permit_expires_at=None
            if c.status not in ('rolling_back','rollback_incomplete'):c.ring_started_at=now()
            c.status='rolling_back'
        else:fail('unknown_command',404)
        timeline(db,c,command,body['reason'],u.user_id);return save_response(db,req,u,body,out(c),200)

def tick():
    with Session() as db:
        if not db.scalar(text('SELECT pg_try_advisory_xact_lock(824712)')):return
        for c in db.scalars(select(Campaign).where(Campaign.status.in_(['running','paused','rolling_back','rollback_incomplete'])).with_for_update()):
            ts=targets(db,c)
            devices={t.device_id:get(db,Device,t.device_id) for t in ts}
            for device_row in devices.values():db.refresh(device_row,with_for_update=True)
            allocate(ts,devices,now(),rollback=c.status in ('rolling_back','rollback_incomplete'),paused=c.status=='paused',deadline=None if c.status in ('rolling_back','rollback_incomplete') else c.ring_started_at+timedelta(minutes=10))
            if c.status in ('rolling_back','rollback_incomplete'):
                pending=[]
                for t in ts:
                    if not t.rollback_generation:continue
                    d=get(db,Device,t.device_id)
                    if d.applied_generation==t.rollback_generation and d.actual_release_id==t.previous_release_id and d.actual_config_version_id==t.previous_config_version_id and d.health_status=='healthy' and d.last_heartbeat_at and (now()-d.last_heartbeat_at).total_seconds()<=30:t.status='rolled_back'
                    else:pending.append(t)
                if not pending:
                    c.status='rolled_back'
                    for t in ts:get(db,Device,t.device_id).active_deployment_campaign_id=None
                    timeline(db,c,'rolled_back','all assigned targets restored')
                elif (now()-c.ring_started_at).total_seconds()>600:c.status='rollback_incomplete'
                continue
            if c.status=='paused':continue
            status,reasons=assess(db,c);c.failure_windows=c.failure_windows+1 if status=='failed' else 0
            if c.failure_windows>=3:c.status='paused';timeline(db,c,'paused',','.join(reasons))
        db.commit()

# Named command routes preserve the frozen OpenAPI paths.
def register_command(command):
    def endpoint(campaign_id:str,body:dict,req:Request):return campaign_command(campaign_id,command,body,req)
    app.add_api_route('/api/v1/deployments/{campaign_id}/'+command,endpoint,methods=['POST'],name='deployment_'+command)
for command in ('start','advance','pause','resume','rollback','cancel'):register_command(command)
