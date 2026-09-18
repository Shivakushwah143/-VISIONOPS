"""Generate local credentials; refuse to overwrite existing configuration."""
import secrets
from pathlib import Path
root=Path(__file__).resolve().parents[1]
p=root/'.env'
if p.exists():raise SystemExit('.env already exists; preserve or explicitly remove it before regenerating')
pwd=secrets.token_urlsafe(32)
s=(root/'.env.example').read_text().replace('replace-with-generated-random-value',pwd).replace('replace-with-at-least-32-random-characters',secrets.token_urlsafe(48))
s=s.replace('GRAFANA_ADMIN_PASSWORD='+pwd,'GRAFANA_ADMIN_PASSWORD='+secrets.token_urlsafe(32))
p.write_text(s);p.chmod(0o600)
for folder in ('var/media','var/edge','var/trusted_keys'): (root/folder).mkdir(parents=True,exist_ok=True)
print('Created local environment. Run Docker Compose from START_HERE.md.')
