"""800 sites / 8,000 inventory records via the same authenticated API."""
import argparse,uuid
from scripts.client import Client
p=argparse.ArgumentParser();p.add_argument('--url',default='http://localhost:8080');p.add_argument('--email',required=True);p.add_argument('--release-id',required=True);p.add_argument('--sites',type=int,default=800);p.add_argument('--per-site',type=int,default=10);a=p.parse_args();c=Client(a.url,a.email)
def all_rows(path):
    rows=[];cursor=None
    while True:
        r=c.http.get(path,params={'limit':200,**({'cursor':cursor} if cursor else {})});r.raise_for_status();b=r.json();rows+=b['data'];cursor=b['page']['next_cursor']
        if not cursor:return rows
sites={s['name']:s for s in all_rows('/sites')};devices={(d['site_id'],d['name']) for d in all_rows('/devices')};created=0
for i in range(a.sites):
    name=f'Simulation site {i+1:04d}';site=sites.get(name) or c.post('/sites',{'name':name,'timezone':'UTC'})
    for j in range(a.per_site):
        name=f'Inventory simulator {i+1:04d}-{j+1:02d}'
        if (site['site_id'],name) in devices:continue
        c.post('/devices',{'site_id':site['site_id'],'name':name,'mode':'simulated','hardware_profile':'cpu_onnx_x86_64','release_id':a.release_id});created+=1
print(f'Created {created} inventory-only simulated device records. No physical-device claim and no agent processes were started.')
