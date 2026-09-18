"""Artifact bytes, immutable manifests and server-owned promotion policy."""
from .main import *
FIELDS={
'models':(Model,CV,['name','task','class_map']),
'dataset-versions':(Dataset,CV,['name','git_commit','dvc_hash','dvc_remote_ref','taxonomy','split_manifest_hash','license_ref','validation_evidence_ref','status']),
'model-versions':(Version,CV,['model_id','training_run_id','mlflow_model_name','mlflow_model_version','version_label']),
'config-versions':(Config,CV,['hardware_profile','schema_version','settings'])}
DEFAULTS={'class_map':{'0':'person','1':'helmet','2':'no_helmet'},'input_width':640,'input_height':640,'color_order':'rgb','letterbox':True,'score_threshold':.35,'nms_iou':.5,'inference_fps':5,'queue_capacity':2,'stale_frame_ms':500,'batch_timeout_ms':100,'rtsp_timeout_seconds':10,'rule_min_frames':5,'rule_min_span_seconds':2,'rule_max_gap_seconds':1,'rule_cooldown_seconds':30,'cameras':[]}
def register(path,cls,roles,fields):
    def endpoint(body:dict,req:Request):
        body_exact(body,fields,fields)
        with Session() as db:
            u=user(req,db,roles);old=replay(db,req,u,body)
            if old:return old
            vals=dict(body)
            if cls is Model and (body['task']!='ppe_detection' or body['class_map']!=DEFAULTS['class_map']):fail('taxonomy_mismatch',422)
            if cls is Config:
                if body['schema_version']!=1 or body['settings']!=DEFAULTS:fail('unsupported_config',422)
                vals.update(sha256=sha(body['settings']),created_by=u.user_id)
            if cls is Dataset:
                if body['status'] not in ('draft','validated') or not body['license_ref']:fail('invalid_dataset',422)
                if body['status']=='validated' and not body['validation_evidence_ref']:fail('missing_evidence',422)
            if cls is Version:
                run=get(db,Training,body['training_run_id'])
                if run.status!='succeeded':fail('training_not_succeeded',422)
            r=cls(**vals);db.add(r);db.flush();audit(db,u,'create',r,req);return save_response(db,req,u,body,out(r))
    app.add_api_route('/api/v1/'+path,endpoint,methods=['POST'],name='create_'+path)
for path,(cls,roles,fields) in FIELDS.items():register(path,cls,roles,fields)
@app.post('/api/v1/training-runs')
def register_run(body:dict,req:Request):
    body_exact(body,['dataset_version_id','mlflow_run_id','code_commit','parameters','status','started_at','finished_at'],['dataset_version_id','mlflow_run_id','code_commit','parameters','status','started_at'])
    import httpx
    with Session() as db:
        u=user(req,db,CV);old=replay(db,req,u,body)
        if old:return old
        try:
            r=httpx.get(os.environ.get('MLFLOW_TRACKING_URI','http://localhost:5000')+'/api/2.0/mlflow/runs/get',params={'run_id':body['mlflow_run_id']},timeout=10);r.raise_for_status()
            status=r.json()['run']['info']['status']
        except Exception:fail('registry_unavailable',503)
        if status!={'running':'RUNNING','succeeded':'FINISHED','failed':'FAILED'}.get(body['status']):fail('lineage_mismatch',422)
        vals=dict(body)
        from datetime import datetime
        for k in ('started_at','finished_at'):
            if vals.get(k):vals[k]=datetime.fromisoformat(vals[k].replace('Z','+00:00'))
        row=Training(**vals);db.add(row);db.flush();audit(db,u,'create',row,req);return save_response(db,req,u,body,out(row))
async def upload_artifact(artifact_kind:str,req:Request):
    if artifact_kind not in ('model-artifacts','runtime-artifacts'):fail('not_found',404)
    cls=ModelArtifact if artifact_kind=='model-artifacts' else RuntimeArtifact
    with Session() as db:
        u=user(req,db,CV if cls is ModelArtifact else M)
        form=await req.form();metadata=json.loads(form['metadata']); folder=DATA/'artifacts';folder.mkdir(exist_ok=True)
        temporary=folder/(uid()+'.partial');digestor=hashlib.sha256();size=0
        try:
            with temporary.open('wb') as f:
                while chunk:=await form['file'].read(1024**2):
                    size+=len(chunk)
                    if size>2*1024**3:fail('size_limit',413)
                    digestor.update(chunk);f.write(chunk)
            digest_value=digestor.hexdigest(); idem={**metadata,'sha256':digest_value};old=replay(db,req,u,idem)
            if old:return old
            allowed=['model_version_id','format','precision','hardware_profile','input_shape','class_map','compatibility'] if cls is ModelArtifact else ['version_label','hardware_profile','entrypoint','compatibility']
            body_exact(metadata,allowed,allowed)
            if cls is ModelArtifact:
                if metadata['format'] not in ('onnx','tensorrt','pytorch') or metadata['class_map']!=DEFAULTS['class_map']:fail('invalid_model_artifact',422)
                if metadata['format']=='onnx':
                    import onnxruntime as ort
                    try:ort.InferenceSession(str(temporary),providers=['CPUExecutionProvider'])
                    except Exception:fail('invalid_onnx',422)
            else:
                if metadata['entrypoint']!='worker':fail('unsafe_entrypoint',422)
                safe_tar(temporary)
            temporary.replace(folder/digest_value)
            row=cls(**metadata,sha256=digest_value,size_bytes=size,storage_key=digest_value);db.add(row);db.flush();audit(db,u,'upload',row,req);return save_response(db,req,u,idem,out(row))
        finally:temporary.unlink(missing_ok=True)
@app.post('/api/v1/evaluation-reports')
def evaluation(body:dict,req:Request):
    required=['model_version_id','model_artifact_id','dataset_version_id','hardware_profile','evidence_mode','metrics','gate_policy','result','evidence_ref'];body_exact(body,required,required)
    with Session() as db:
        u=user(req,db,CV);old=replay(db,req,u,body)
        if old:return old
        a=get(db,ModelArtifact,body['model_artifact_id']);ds=get(db,Dataset,body['dataset_version_id'])
        if a.model_version_id!=body['model_version_id'] or a.hardware_profile!=body['hardware_profile'] or ds.status!='validated':fail('evidence_mismatch',422)
        metrics=body['metrics'];mode=body['evidence_mode']
        vals=dict(body)
        if mode=='real':
            import re
            from mlflow import MlflowClient
            from training.quality import measured,gate
            match=re.fullmatch(r'mlflow:([a-f0-9]{32})/evaluation/evidence.json',body['evidence_ref'])
            if not match:fail('invalid_evidence_ref',422)
            try:
                client=MlflowClient(tracking_uri=os.environ.get('MLFLOW_TRACKING_URI','http://localhost:5000'))
                run=client.get_run(match.group(1))
                if run.info.status!='FINISHED':fail('evaluation_not_finished',422)
                import tempfile
                with tempfile.TemporaryDirectory(prefix='visionops-evaluation-') as destination:
                    path=Path(client.download_artifacts(match.group(1),'evaluation/evidence.json',destination))
                    if path.stat().st_size>50*1024**2:fail('evidence_size_limit',413)
                    evidence=json.loads(path.read_text())
                if evidence['model_sha256']!=a.sha256 or evidence['dataset_dvc_hash']!=ds.dvc_hash or evidence['hardware_profile']!=a.hardware_profile:fail('evidence_mismatch',422)
                recomputed=measured(evidence)
            except HTTPException:raise
            except (ValueError,KeyError,TypeError):fail('invalid_evaluation_evidence',422)
            except Exception:fail('registry_unavailable',503)
            # Raw observations drive policy; submitted summary/result cannot lower the gate.
            reasons=gate(recomputed)
            # Full repeated/parity protocol remains mandatory and is never inferred from metrics.
            if evidence.get('warmup_frames',0)<100 or evidence.get('timed_repetitions',0)<3 or evidence.get('frames_per_repetition',0)<1000:reasons.append('benchmark_protocol')
            if not evidence.get('parity_reference_sha256'):reasons.append('parity_reference_missing')
            vals['metrics']=recomputed;vals['result']='failed' if reasons else 'passed';vals['gate_policy']={'policy_id':'ppe-v1','failed_checks':reasons}
        elif mode=='simulated':
            passed=body['evidence_ref'].startswith('mlflow:') and metrics.get('run_kind')=='simulation_fixture'
            vals['result']='passed' if passed else 'blocked';vals['gate_policy']={'policy_id':'simulation-only'}
        else:fail('invalid_evidence_mode',422)
        row=Evaluation(**vals);db.add(row);db.flush();audit(db,u,'evaluate',row,req);return save_response(db,req,u,body,out(row))
@app.post('/api/v1/releases')
def create_release(body:dict,req:Request):
    fields=['model_artifact_id','runtime_artifact_id','config_version_id','evaluation_report_id'];body_exact(body,fields,fields)
    with Session() as db:
        u=user(req,db,CV);old=replay(db,req,u,body)
        if old:return old
        a=get(db,ModelArtifact,body['model_artifact_id']);r=get(db,RuntimeArtifact,body['runtime_artifact_id']);c=get(db,Config,body['config_version_id']);e=get(db,Evaluation,body['evaluation_report_id'])
        if len({a.hardware_profile,r.hardware_profile,c.hardware_profile,e.hardware_profile})!=1 or e.model_artifact_id!=a.model_artifact_id:fail('artifact_mismatch',422)
        rid=uid();manifest=dict(schema_version=1,release_id=rid,hardware_profile=a.hardware_profile,evidence_mode=e.evidence_mode,model_artifact_id=a.model_artifact_id,model_sha256=a.sha256,model_size_bytes=a.size_bytes,runtime_artifact_id=r.runtime_artifact_id,runtime_sha256=r.sha256,runtime_size_bytes=r.size_bytes,config_version_id=c.config_version_id,config_sha256=c.sha256,evaluation_report_id=e.evaluation_report_id,compatibility=r.compatibility,entrypoint='worker')
        row=Release(**body,release_id=rid,hardware_profile=a.hardware_profile,evidence_mode=e.evidence_mode,manifest=manifest,manifest_sha256=sha(manifest));db.add(row);db.flush();audit(db,u,'draft',row,req);return save_response(db,req,u,body,out(row))
@app.post('/api/v1/releases/{release_id}/approve')
def approve(release_id:str,body:dict,req:Request):
    body_exact(body,['signature','key_id','reason'],['signature','key_id','reason'])
    with Session() as db:
        u=user(req,db,M);old=replay(db,req,u,body)
        if old:return old
        r=get(db,Release,release_id)
        if r.status!='draft' or get(db,Evaluation,r.evaluation_report_id).result!='passed':fail('gates_failed')
        verify_signature(r.manifest,body['signature'],body['key_id']);r.signature=body['signature'];r.key_id=body['key_id'];r.approved_by=u.user_id;r.approved_at=now();r.status='approved';audit(db,u,'approve',r,req)
        return save_response(db,req,u,body,out(r),200)

@app.post("/api/v1/model-artifacts")
async def upload_model(req:Request):return await upload_artifact("model-artifacts",req)
@app.post("/api/v1/runtime-artifacts")
async def upload_runtime(req:Request):return await upload_artifact("runtime-artifacts",req)
