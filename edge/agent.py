"""Outbound agent with durable identity/outbox, signed staging and local rollback."""
import argparse,base64,hashlib,json,os,random,sys,tarfile,time,uuid,subprocess,platform
from pathlib import Path
from datetime import datetime,timezone,timedelta
import httpx,psutil
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from .state import State
from .watchdog import Watchdog

def utc():return datetime.now(timezone.utc)
def canonical(v):return json.dumps(v,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def sha(v):return hashlib.sha256(canonical(v)).hexdigest()
class Agent:
    def __init__(self,root,url,trust,mode='real'):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True,mode=0o700);os.chmod(self.root,0o700);self.state=State(self.root/'state.sqlite');self.url=url.rstrip('/');self.trust=Path(trust);self.mode=mode
        if not (url.startswith('https://') or url.startswith('http://localhost:') or url.startswith('http://127.0.0.1:') or os.environ.get('ALLOW_PRIVATE_HTTP')=='1'):raise ValueError('HTTPS required')
        self.client=httpx.Client(base_url=self.url+'/api/v1',timeout=15);self.boot=str(uuid.uuid4());self.sequence=0;self.worker=None;self.etag=None;self.error=None;self.phase='idle';self.metrics=[];self.last_window=utc();self.last_tick=time.monotonic();self.previous_frames={};self.previous_input_frames={};self.watchdog=Watchdog(self.state);self.worker_started_at=0;self.recovery_until=None;self.supervisor_disabled=self.state.get('supervisor_disabled',False)
        identity=self.state.get('identity')
        if identity:self.client.headers['Authorization']='Bearer '+identity['device_credential']
        # An interrupted activation never commits the candidate on restart.
        journal=self.state.get('activation')
        if journal:
            self.state.put_many({'actual':journal['previous'],'rejected_generation':journal['candidate']['generation'],'activation':None})
    def enroll(self,token):
        r=self.client.post('/device-enrollments',json={'token':token,'capabilities':{'agent_version':'0.1.0','mode':self.mode}});r.raise_for_status();identity=r.json()['data'];self.state.put('identity',identity);self.client.headers['Authorization']='Bearer '+identity['device_credential'];os.chmod(self.root/'state.sqlite',0o600)
    def get(self,path):
        r=self.client.get(path);r.raise_for_status();return r.json()['data']
    def artifact(self,kind,id,digest,size,folder):
        path=folder/kind
        cache=self.root/'artifact-cache';cache.mkdir(exist_ok=True);cached=cache/digest
        if cached.exists() and cached.stat().st_size==size and hashlib.sha256(cached.read_bytes()).hexdigest()==digest:
            if not path.exists():os.link(cached,path)
            return path
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest()==digest:return path
        temporary=folder/(kind+'.partial');h=hashlib.sha256();total=0
        with self.client.stream('GET',f'/{kind}-artifacts/{id}/content') as r:
            r.raise_for_status()
            with temporary.open('wb') as f:
                for chunk in r.iter_bytes():
                    total+=len(chunk)
                    if total>size or total>2*1024**3:raise ValueError('artifact_size')
                    h.update(chunk);f.write(chunk)
                f.flush();os.fsync(f.fileno())
        if total!=size or h.hexdigest()!=digest:temporary.unlink();raise ValueError('artifact_hash')
        temporary.replace(path)
        if not cached.exists():os.link(path,cached)
        return path
    def prepare(self,desired):
        signed=self.get('/releases/'+desired['release_id']+'/manifest');manifest=signed['manifest']
        if sha(manifest)!=desired['manifest_sha256'] or manifest['evidence_mode']!=self.mode:raise ValueError('manifest_scope')
        if manifest['hardware_profile']!='cpu_onnx_x86_64' or platform.machine() not in ('x86_64','AMD64'):raise ValueError('incompatible_hardware_profile')
        kid=signed['key_id']
        if not kid.replace('-','').replace('_','').isalnum():raise ValueError('key_id')
        Ed25519PublicKey.from_public_bytes(base64.b64decode((self.trust/(kid+'.pub')).read_text())).verify(base64.b64decode(signed['signature']),canonical(manifest))
        config=self.get('/config-versions/'+desired['config_version_id']);default=self.get('/config-versions/'+manifest['config_version_id'])
        if sha(config['settings'])!=desired['config_sha256'] or sha(default['settings'])!=manifest['config_sha256']:raise ValueError('config_hash')
        if {k:v for k,v in config['settings'].items() if k!='cameras'}!={k:v for k,v in default['settings'].items() if k!='cameras'}:raise ValueError('unsigned_policy_override')
        slot=self.root/'slots'/(desired['release_id']+'-'+desired['config_version_id']);slot.mkdir(parents=True,exist_ok=True)
        self.phase='fetching';model=self.artifact('model',manifest['model_artifact_id'],manifest['model_sha256'],manifest['model_size_bytes'],slot);runtime=self.artifact('runtime',manifest['runtime_artifact_id'],manifest['runtime_sha256'],manifest['runtime_size_bytes'],slot)
        self.phase='validating';dest=slot/'bundle';dest.mkdir(exist_ok=True)
        with tarfile.open(runtime) as tar:
            members=tar.getmembers()
            if len(members)>10000 or sum(m.size for m in members)>2*1024**3:raise ValueError('archive_limits')
            for m in members:
                p=Path(m.name)
                if p.is_absolute() or '..' in p.parts or '\\' in m.name or not(m.isfile() or m.isdir()):raise ValueError('unsafe_archive')
            tar.extractall(dest,filter='data')
        if not(dest/'worker.py').is_file():raise ValueError('missing_worker')
        if not(dest/'warmup.jpg').is_file():raise ValueError('missing_signed_warmup')
        (slot/'config.json').write_bytes(canonical(config['settings']));(slot/'manifest.json').write_bytes(canonical(manifest))
        return {**desired,'slot':str(slot.resolve()),'model_sha256':manifest['model_sha256'],'application_version':json.loads((dest/'version.json').read_text())['application_version'],'model_version':manifest['model_artifact_id'],'artifact_version':manifest['runtime_artifact_id']}
    def start_worker(self,actual):
        self.worker_started_at=time.time()
        if self.mode=='simulated':return
        slot=Path(actual['slot']);status=self.root/'worker-status.json';status.unlink(missing_ok=True)
        source_root=str(Path(__file__).resolve().parents[1]);environment=dict(os.environ);environment['PYTHONPATH']=source_root+os.pathsep+environment.get('PYTHONPATH','')
        self.worker=subprocess.Popen([sys.executable,str(slot/'bundle'/'worker.py'),'--slot',str(slot),'--state',str(self.root/'state.sqlite'),'--status',str(status)],stdin=subprocess.DEVNULL,env=environment,cwd=source_root)
    def stop_worker(self):
        if self.worker and self.worker.poll() is None:
            self.worker.terminate()
            try:self.worker.wait(timeout=10)
            except subprocess.TimeoutExpired:self.worker.kill();self.worker.wait()
        self.worker=None
    def worker_health(self):
        if self.mode=='simulated':
            fault=self.root/'fault-unhealthy'
            if not fault.exists():return True
            try:
                target=json.loads(fault.read_text());candidate=self.state.get('activation');active=candidate['candidate'] if candidate else self.state.get('actual')
                return not active or active['generation']!=target['generation'] or active['release_id']!=target['release_id']
            except (ValueError,KeyError):return False
        path=self.root/'worker-status.json'
        if not self.worker or self.worker.poll() is not None or not path.exists():return False
        try:return time.time()-path.stat().st_mtime<30 and json.loads(path.read_text()).get('ready') is True
        except Exception:return False
    def reconcile(self):
        identity=self.state.get('identity');d=self.get('/devices/'+identity['device_id']+'/desired-state');actual=self.state.get('actual')
        if actual and actual['generation']==d['generation']:return
        if d['generation']==self.state.get('rejected_generation') or not d['download_permit']:return
        previous=actual
        try:
            candidate=self.prepare(d)
            if self.get('/devices/'+identity['device_id']+'/desired-state')['generation']!=d['generation']:return
            self.state.put('activation',{'previous':previous,'candidate':candidate});self.phase='activating';self.stop_worker();self.start_worker(candidate);self.phase='observing'
            until=time.monotonic()+60
            while time.monotonic()<until:
                self.deliver();self.heartbeat();time.sleep(5)
                if not self.worker_health():raise ValueError('candidate_unhealthy')
            if self.get('/devices/'+identity['device_id']+'/desired-state')['generation']!=d['generation']:raise ValueError('candidate_superseded')
            self.state.put_many({'actual':candidate,'last_good':previous,'activation':None,'rejected_generation':None,'supervisor_disabled':False});self.watchdog.reset();self.supervisor_disabled=False;self.phase='healthy';self.error=None
        except Exception as exc:
            self.error=type(exc).__name__;self.phase='rolling_back';self.stop_worker();self.state.put('actual',previous);self.state.put('rejected_generation',d['generation']);self.state.put('activation',None)
            if previous:self.start_worker(previous)
            self.phase='degraded'
    def process_alive(self):
        if self.mode=='simulated':return self.worker_health()
        if not self.worker or self.worker.poll() is not None:return False
        status=self.root/'worker-status.json'
        # Camera outage / file EOF is source health, not a process crash.
        return time.time()-self.worker_started_at<30 or (status.exists() and time.time()-status.stat().st_mtime<30)
    def local_rollback(self):
        failed=self.state.get('actual');previous=self.state.get('last_good')
        self.stop_worker();self.phase='rolling_back';self.error='worker_crash_loop'
        if not previous:
            self.supervisor_disabled=True;self.phase='degraded';self.state.put_many({'supervisor_disabled':True,'rejected_generation':failed['generation']});return
        restored={**previous,'generation':failed['generation']}
        self.state.put_many({'actual':restored,'rejected_generation':failed['generation'],'recovery_pending':True})
        self.start_worker(restored);self.recovery_until=time.time()+60
    def supervise(self):
        actual=self.state.get('actual')
        if not actual or self.supervisor_disabled or self.state.get('activation'):return
        if self.state.get('recovery_pending'):
            if self.recovery_until is None:self.recovery_until=time.time()+60
            if not self.process_alive():
                self.stop_worker();self.supervisor_disabled=True;self.phase='degraded';self.error='rollback_worker_failed';self.state.put_many({'supervisor_disabled':True,'recovery_pending':False});return
            if time.time()>=self.recovery_until:
                self.state.put('recovery_pending',False);self.phase='healthy'
            return
        pending=self.state.get('watchdog',{})
        if self.mode=='simulated' and pending.get('action')=='restart' and time.time()<pending.get('retry_at',0):return
        if self.worker is None and pending.get('action')=='restart' and self.mode!='simulated':
            if time.time()>=pending['retry_at']:self.start_worker(actual)
            return
        if self.process_alive():return
        self.stop_worker();action,_=self.watchdog.failure(actual['generation']);self.error='worker_exited'
        if action=='rollback':self.local_rollback()
        else:self.phase='degraded'
    def heartbeat(self):
        identity=self.state.get('identity');actual=self.state.get('actual');self.sequence+=1;stamp=utc();healthy=self.worker_health();metric=[]
        if self.mode=='simulated' and actual:
            # Explicit synthetic worker observations; duration is measured, never labeled real CV.
            elapsed=time.monotonic()-self.last_tick;self.last_tick=time.monotonic();samples=[]
            for _ in range(min(75,max(1,int(elapsed*5)))):
                begin=time.perf_counter();hashlib.sha256(b'simulated-worker-observation').digest();samples.append((time.perf_counter()-begin)*1000)
            metric=[{'metric_summary_id':str(uuid.uuid4()),'camera_id':None,'release_id':actual['release_id'],'window_start':self.last_window.isoformat(),'window_end':stamp.isoformat(),'sample_count':len(samples),'values':{'inference_latency_ms':samples,'inference_fps':0 if not healthy else 5,'restart_count':0,'observation_kind':'simulated_worker','cpu_percent':psutil.cpu_percent(),'rss_bytes':psutil.Process().memory_info().rss,'gpu_utilization_ratio':None}}]
        if self.mode=='real' and actual:
            status_path=self.root/'worker-status.json'
            if status_path.exists():
                status=json.loads(status_path.read_text());cameras=status.get('cameras',[])
                for camera in cameras[:4]:
                    count=camera.get('inference_frames',0);previous=self.previous_frames.get(camera.get('camera_id'),0);new_count=max(0,count-previous);samples=camera.get('latency_ms',[])[-min(1024,new_count):] if new_count else [];duration=max((stamp-self.last_window).total_seconds(),.001)
                    metric.append({'metric_summary_id':str(uuid.uuid4()),'camera_id':camera.get('camera_id'),'release_id':actual['release_id'],'window_start':self.last_window.isoformat(),'window_end':stamp.isoformat(),'sample_count':len(samples),'values':{'inference_latency_ms':samples,'inference_fps':new_count/duration,'input_fps':max(0,camera.get('input_frames',0)-self.previous_input_frames.get(camera.get('camera_id'),0))/duration,'queue_depth':camera.get('queue_depth',0),'input_frames_total':camera.get('input_frames',0),'inference_frames_total':count,'outbox_depth':self.state.depth(),'dropped_frames':camera.get('dropped_frames',0),'reconnect_count':camera.get('reconnect_count',0),'cpu_percent':psutil.cpu_percent(),'rss_bytes':psutil.Process().memory_info().rss,'gpu_utilization_ratio':None}})
                    self.previous_frames[camera.get('camera_id')]=count;self.previous_input_frames[camera.get('camera_id')]=camera.get('input_frames',0)
        self.last_window=stamp
        payload={'device_id':identity['device_id'],'boot_id':self.boot,'sequence':self.sequence,'observed_at':stamp.isoformat(),'actual_release_id':actual['release_id'] if actual else None,'actual_config_version_id':actual['config_version_id'] if actual else None,'applied_generation':actual['generation'] if actual else 0,'agent_state':self.phase,'health_status':('unknown' if self.mode=='real' and not metric else 'healthy' if actual and healthy else 'unhealthy' if actual else 'unknown'),'source_states':([{'camera_id':c['camera_id'],'status':c['status'],'last_frame_at':c.get('last_frame_at')} for c in cameras] if self.mode=='real' and actual and 'cameras' in locals() else []),'capabilities':{'mode':self.mode,'agent_version':'0.1.0','application_version':actual.get('application_version') if actual else None,'model_version':actual.get('model_version') if actual else None,'artifact_version':actual.get('artifact_version') if actual else None},'metric_summaries':metric,'rejected_generation':self.state.get('rejected_generation'),'last_error_code':self.error}
        r=self.client.post('/devices/'+identity['device_id']+'/heartbeats',json=payload);r.raise_for_status()
    def deliver(self):
        batch=self.state.batch()
        if batch:
            r=self.client.post('/device-events',json={'events':batch});r.raise_for_status();self.state.ack(r.json()['data']['results'])
    def run(self):
        actual=self.state.get('actual')
        if actual and not self.supervisor_disabled:self.start_worker(actual)
        deadline=0
        try:
            while True:
                self.supervise()
                try:
                    if (self.root/'fault-offline').exists():time.sleep(1);continue
                    self.deliver()
                    if time.monotonic()>=deadline:self.heartbeat();self.reconcile();deadline=time.monotonic()+random.uniform(12,18)
                except (httpx.HTTPError,OSError,ValueError) as exc:self.error=type(exc).__name__
                time.sleep(1)
        finally:self.stop_worker()
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--state',required=True);p.add_argument('--url',default='http://localhost:8000');p.add_argument('--trust',default='var/trusted_keys');p.add_argument('--mode',choices=['real','simulated'],default='real');p.add_argument('--enrollment-token');a=p.parse_args();agent=Agent(a.state,a.url,a.trust,a.mode)
    if a.enrollment_token:agent.enroll(a.enrollment_token)
    if not agent.state.get('identity'):p.error('enrollment required')
    agent.run()
