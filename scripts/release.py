"""Offline Ed25519 key management and explicit simulation-only release creation."""
import argparse,base64,json,hashlib,tarfile,tempfile,uuid,os
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from scripts.client import Client
from scripts.engineering_fixture import create
p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True)
k=sub.add_parser('keygen');k.add_argument('--directory',default='var/keys');k.add_argument('--key-id',default='demo')
s=sub.add_parser('simulation');s.add_argument('--url',default='http://localhost:8080');s.add_argument('--email',required=True);s.add_argument('--tracking-uri',required=True);s.add_argument('--private-key',required=True);s.add_argument('--key-id',default='demo');s.add_argument('--version',required=True);s.add_argument('--output',default='var/simulation-release.json')
a=p.parse_args()
if a.command=='keygen':
    if not a.key_id.replace('-','').replace('_','').isalnum():p.error('invalid key id')
    folder=Path(a.directory);folder.mkdir(parents=True,exist_ok=True);private=folder/(a.key_id+'.key');public=folder/(a.key_id+'.pub')
    if private.exists() or public.exists():raise SystemExit('Refusing to overwrite existing keys')
    key=Ed25519PrivateKey.generate();private.write_bytes(key.private_bytes(serialization.Encoding.Raw,serialization.PrivateFormat.Raw,serialization.NoEncryption()));private.chmod(0o600);public.write_text(base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)).decode());print('Key generated. Install only the .pub file into backend and agent trusted-key directories.');raise SystemExit()
import mlflow
from mlflow import MlflowClient
c=Client(a.url,a.email);mlflow.set_tracking_uri(a.tracking_uri);mlflow.set_experiment('visionops-simulation');profile='cpu_onnx_x86_64';classmap={'0':'person','1':'helmet','2':'no_helmet'}
with tempfile.TemporaryDirectory(prefix='visionops-sim-release-') as temp:
    folder=create(temp)
    with mlflow.start_run(tags={'run_kind':'simulation_fixture'}) as run:
        mlflow.log_artifact(str(folder/'simulation.onnx'),'fixture');mlflow.log_dict({'run_kind':'simulation_fixture','not_trained':True,'taxonomy':classmap},'fixture/provenance.json')
        run_id=run.info.run_id;artifact_uri=run.info.artifact_uri
    registry=MlflowClient();name='visionops-simulation'
    try:registry.get_registered_model(name)
    except Exception:registry.create_registered_model(name)
    external=registry.create_model_version(name,artifact_uri+'/fixture/simulation.onnx',run_id)
    digest=hashlib.sha256((folder/'simulation.onnx').read_bytes()).hexdigest();dataset=c.post('/dataset-versions',{'name':'Explicit synthetic engineering fixture '+a.version,'git_commit':'uncommitted-source-release','dvc_hash':digest,'dvc_remote_ref':'local:simulation_fixture','taxonomy':classmap,'split_manifest_hash':digest,'license_ref':'CC0-1.0; generated engineering geometry','validation_evidence_ref':'mlflow:'+run_id+'/fixture/provenance.json','status':'validated'})
    from datetime import datetime,timezone
    stamp=datetime.now(timezone.utc).isoformat();training=c.post('/training-runs',{'dataset_version_id':dataset['dataset_version_id'],'mlflow_run_id':run_id,'code_commit':'uncommitted-source-release','parameters':{'run_kind':'simulation_fixture','training_performed':False},'status':'succeeded','started_at':stamp,'finished_at':stamp})
    models=c.get('/models');model=next((m for m in models if m['name']==name),None) or c.post('/models',{'name':name,'task':'ppe_detection','class_map':classmap})
    version=c.post('/model-versions',{'model_id':model['model_id'],'training_run_id':training['training_run_id'],'mlflow_model_name':name,'mlflow_model_version':str(external.version),'version_label':a.version})
    with (folder/'simulation.onnx').open('rb') as f:artifact=c.post('/model-artifacts',files={'file':('simulation.onnx',f,'application/octet-stream')},data={'metadata':json.dumps({'model_version_id':version['model_version_id'],'format':'onnx','precision':'fp32','hardware_profile':profile,'input_shape':[1,3,640,640],'class_map':classmap,'compatibility':{'run_kind':'simulation_fixture'}})})
    from shutil import copyfile
    root=Path(__file__).resolve().parents[1];copyfile(root/'edge/worker.py',folder/'worker.py');(folder/'version.json').write_text(json.dumps({'application_version':a.version,'model_version_id':version['model_version_id']}))
    bundle=folder/'runtime.tar'
    with tarfile.open(bundle,'w') as tar:
        for file in ['worker.py','version.json','warmup.jpg']:tar.add(folder/file,arcname=file)
    with bundle.open('rb') as f:runtime=c.post('/runtime-artifacts',files={'file':('runtime.tar',f,'application/x-tar')},data={'metadata':json.dumps({'version_label':a.version,'hardware_profile':profile,'entrypoint':'worker','compatibility':{'run_kind':'simulation_fixture'}})})
    defaults=json.loads((root/'shared/contracts/default-settings.json').read_text());config=c.post('/config-versions',{'hardware_profile':profile,'schema_version':1,'settings':defaults})
    report=c.post('/evaluation-reports',{'model_version_id':version['model_version_id'],'model_artifact_id':artifact['model_artifact_id'],'dataset_version_id':dataset['dataset_version_id'],'hardware_profile':profile,'evidence_mode':'simulated','metrics':{'run_kind':'simulation_fixture'},'gate_policy':{'policy_id':'simulation-only'},'result':'passed','evidence_ref':'mlflow:'+run_id+'/fixture/provenance.json'})
    release=c.post('/releases',{'model_artifact_id':artifact['model_artifact_id'],'runtime_artifact_id':runtime['runtime_artifact_id'],'config_version_id':config['config_version_id'],'evaluation_report_id':report['evaluation_report_id']})
    key=Ed25519PrivateKey.from_private_bytes(Path(a.private_key).read_bytes());signature=base64.b64encode(key.sign(json.dumps(release['manifest'],sort_keys=True,separators=(',',':')).encode())).decode();approved=c.post('/releases/'+release['release_id']+'/approve',{'signature':signature,'key_id':a.key_id,'reason':'Explicit simulation fixture; never deploy to real devices'})
    Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(approved,indent=2));print('Approved simulation-only release '+approved['release_id'])
