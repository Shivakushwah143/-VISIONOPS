"""10–50 active identities; each uses actual HTTP, signed slots and SQLite state."""
import argparse,threading,json
from pathlib import Path
from edge.agent import Agent
from scripts.client import Client
p=argparse.ArgumentParser();p.add_argument('--url',default='http://localhost:8080');p.add_argument('--email',required=True);p.add_argument('--device-ids',required=True,help='JSON array of 10–50 pre-registered simulated device UUIDs');p.add_argument('--state',default='var/simulation');p.add_argument('--trust',default='var/trusted_keys');a=p.parse_args();ids=json.loads(Path(a.device_ids).read_text())
if not 10<=len(ids)<=50 or len(set(ids))!=len(ids):p.error('10–50 distinct identities required')
c=Client(a.url,a.email);threads=[]
for id in ids:
    d=c.get('/devices/'+id)['device']
    if d['mode']!='simulated':raise ValueError('simulator cannot impersonate a real device')
    agent=Agent(Path(a.state)/id,a.url,a.trust,'simulated')
    if not agent.state.get('identity'):
        token=c.post('/devices/'+id+'/enrollment-tokens',{'reason':'Local simulator enrollment'})['token'];agent.enroll(token)
    t=threading.Thread(target=agent.run,name=id,daemon=True);t.start();threads.append(t)
print(f'{len(threads)} active simulated identities. Worker observations are synthetic; control-plane traffic and durable delivery are real.')
for t in threads:t.join()
