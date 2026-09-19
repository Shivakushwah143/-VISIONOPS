import subprocess,shutil,platform,json,sys,os
from pathlib import Path
report={'python':sys.version,'machine':platform.machine(),'kernel':platform.release(),'nvidia_device_nodes':[str(p) for p in Path('/dev').glob('nvidia*')],'commands':{}}
for name,args in [('node',['--version']),('ffmpeg',['-version']),('docker',['version']),('nvidia-smi',[]),('nvcc',['--version']),('gst-launch-1.0',['--version'])]:
    path=shutil.which(name)
    if not path:report['commands'][name]={'status':'BLOCKED','reason':'executable_not_available'};continue
    try:
        r=subprocess.run([path,*args],capture_output=True,text=True,timeout=10);report['commands'][name]={'status':'VERIFIED' if r.returncode==0 else 'BLOCKED','exit_code':r.returncode,'output':(r.stdout+r.stderr)[:2500]}
    except Exception as e:report['commands'][name]={'status':'BLOCKED','reason':type(e).__name__}
# Capability report shared with the edge runtime (fail-soft if onnxruntime is absent).
try:
    from edge.runtimes import detect_capabilities
    report['capability_report']=detect_capabilities()
    report['nvidia_runtime']={'status':'BLOCKED' if not report['capability_report']['TENSORRT']['available'] else 'AVAILABLE',
                              'reason':report['capability_report']['TENSORRT']['reason'],
                              'label':report['capability_report']['TENSORRT']['status'],
                              'gpu_metrics':None}
except Exception as exc:
    report['capability_report']={'status':'BLOCKED','reason':type(exc).__name__}
    report['nvidia_runtime']={'status':'BLOCKED','reason':'No NVIDIA device nodes or NVIDIA/CUDA/DeepStream commands detected','gpu_metrics':None}
report['postgresql_startup']={'status':'BLOCKED','evidence':['Historical baseline: pgserver installation and bundled binaries were observed','Historical baseline: pgserver user creation returned nonzero','runuser -u oai -- id: cannot set groups: Operation not permitted','CapEff=0000000000000000; seccomp enabled; PostgreSQL requires non-root OS identity'],'fallback':'No central SQLite replacement used'}
Path('docs/evidence/environment.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
