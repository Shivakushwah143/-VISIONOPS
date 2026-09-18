"""Exercise real services; record blockers without substituting fake storage."""
import subprocess,os,time,json,tempfile,urllib.request,urllib.error,sys,secrets
from pathlib import Path
root=Path(__file__).resolve().parents[1];report={};env=dict(os.environ,TOKEN_PEPPER=secrets.token_urlsafe(48),APP_ORIGIN='http://localhost:5173')
def fetch(url):
    try:
        with urllib.request.urlopen(url,timeout=3) as r:return r.status,r.read().decode()
    except urllib.error.HTTPError as e:return e.code,e.read().decode()
with tempfile.TemporaryFile() as output:
    process=subprocess.Popen([sys.executable,'-m','uvicorn','backend.app.main:app','--host','127.0.0.1','--port','18000'],cwd=root,env=env,stdout=output,stderr=output)
    try:
        for _ in range(60):
            try:code,body=fetch('http://127.0.0.1:18000/health/live');break
            except Exception:time.sleep(.25)
        else:raise RuntimeError('backend_did_not_start')
        report['backend_start_and_liveness']={'status':'VERIFIED','http_status':code,'body':json.loads(body)}
        code,body=fetch('http://127.0.0.1:18000/health/ready');report['postgresql_readiness']={'status':'VERIFIED' if code==200 else 'BLOCKED','http_status':code,'body':json.loads(body)}
        code,body=fetch('http://127.0.0.1:18000/metrics');report['prometheus_exposition']={'status':'VERIFIED' if code==200 and 'visionops_http_requests_total' in body else 'NOT IMPLEMENTED','observed_request_metric':'visionops_http_requests_total' in body}
        code,body=fetch('http://127.0.0.1:18000/openapi.json');schema=json.loads(body);(root/'shared/contracts/openapi.json').write_text(json.dumps(schema,indent=2));report['openapi']={'status':'VERIFIED','paths':len(schema['paths'])}
    finally:process.terminate();process.wait(timeout=15)
with tempfile.TemporaryFile() as output:
    process=subprocess.Popen(['npm','run','dev','--','--port','15173'],cwd=root/'frontend',stdout=output,stderr=output)
    try:
        for _ in range(80):
            try:code,body=fetch('http://127.0.0.1:15173/');break
            except Exception:time.sleep(.25)
        else:
            output.seek(0);raise RuntimeError('frontend_did_not_start: '+output.read().decode()[-2000:])
        report['frontend_http_start']={'status':'VERIFIED' if code==200 and '/src/main.tsx' in body else 'NOT IMPLEMENTED','http_status':code,'browser_interaction':'NOT IMPLEMENTED'}
    finally:process.terminate();process.wait(timeout=15)
(root/'docs/evidence/service-runtime.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
