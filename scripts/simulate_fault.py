import argparse
from pathlib import Path
import json
from edge.state import State
p=argparse.ArgumentParser();p.add_argument('--state',required=True);p.add_argument('--fault',choices=['unhealthy','offline'],required=True);p.add_argument('--recover',action='store_true');a=p.parse_args();root=Path(a.state)
if not (root/'state.sqlite').is_file():p.error('Choose an existing simulator identity state directory')
f=root/('fault-'+a.fault)
if a.recover:f.unlink(missing_ok=True)
else:
    state=State(root/'state.sqlite');journal=state.get('activation');actual=journal['candidate'] if journal else state.get('actual')
    if not actual:p.error('Device has no candidate or committed generation to target')
    f.write_text(json.dumps({'generation':actual['generation'],'release_id':actual['release_id']}))
print('Simulator fault recovered' if a.recover else 'Simulator fault enabled')
