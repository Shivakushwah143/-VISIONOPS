"""Validate license, taxonomy, label geometry and disjoint group assignments."""
import argparse,json,hashlib
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--manifest',required=True);p.add_argument('--root',required=True);p.add_argument('--output',required=True);a=p.parse_args();m=json.loads(Path(a.manifest).read_text());root=Path(a.root).resolve();seen={};hashes={};counts=[0,0,0]
if not m.get('license') or not m.get('source') or m.get('taxonomy')!=['person','helmet','no_helmet']:raise ValueError('source/license/taxonomy required')
for split in ('train','val','test'):
    for item in m['splits'][split]:
        if not item.get('group'):raise ValueError('recording/site group provenance required')
        if item['group'] in seen and seen[item['group']]!=split:raise ValueError('group leakage')
        seen[item['group']]=split
        for field in ('image','label'):
            path=(root/item[field]).resolve()
            if not path.is_relative_to(root):raise ValueError('path traversal')
            hashes[item[field]]=hashlib.sha256(path.read_bytes()).hexdigest()
        for line in (root/item['label']).read_text().splitlines():
            c,x,y,w,h=map(float,line.split())
            if c not in (0,1,2) or not(0<=x<=1 and 0<=y<=1 and 0<w<=1 and 0<h<=1) or x-w/2<0 or y-h/2<0 or x+w/2>1 or y+h/2>1:raise ValueError('invalid YOLO annotation')
            counts[int(c)]+=1
report={'status':'validated','source':m['source'],'license':m['license'],'class_counts':counts,'file_hashes':hashes,'split_manifest_sha256':hashlib.sha256(Path(a.manifest).read_bytes()).hexdigest()};Path(a.output).write_text(json.dumps(report,indent=2));print('Dataset validation completed; no training or evaluation claims.')
