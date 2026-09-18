"""Fetch the attributed external PPE baseline and verify published bytes."""
import json,hashlib,urllib.request,argparse
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--output',default='var/models/ppe-baseline.onnx');a=p.parse_args();root=Path(__file__).resolve().parents[1];m=json.loads((root/'training/PPE_SOURCE_MANIFEST.json').read_text());target=Path(a.output);target.parent.mkdir(parents=True,exist_ok=True);temp=target.with_suffix('.partial');h=hashlib.sha256()
try:
    with urllib.request.urlopen(m['download_url'],timeout=30) as r,temp.open('wb') as f:
        while chunk:=r.read(1024**2):f.write(chunk);h.update(chunk)
    if h.hexdigest()!=m['published_sha256']:raise ValueError('PPE model SHA256 mismatch')
    from edge.pipeline import Detector
    Detector(temp,h.hexdigest(),{11:0,3:1,8:2})
    temp.replace(target);m.update(local_sha256=h.hexdigest(),local_bytes_verified=True,status='DOWNLOADED_METADATA_VERIFIED');target.with_suffix('.provenance.json').write_text(json.dumps(m,indent=2));print('Verified download. Annotation geometry and quality evaluation remain required before real deployment approval.')
finally:temp.unlink(missing_ok=True)
