import os, hashlib, secrets, base64, json, tarfile
from pathlib import Path
from datetime import timedelta
from fastapi import HTTPException
from sqlalchemy import select
from argon2 import PasswordHasher
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from .db import *
passwords=PasswordHasher()
ORIGIN=os.environ.get('APP_ORIGIN','http://localhost:5173')
PEPPER=os.environ.get('TOKEN_PEPPER','')
if len(PEPPER)<32: raise RuntimeError('TOKEN_PEPPER must be at least 32 characters')
TRUST=Path(os.environ.get('TRUSTED_KEYS_DIR','var/trusted_keys'))
def digest(s): return hashlib.sha256((PEPPER+s).encode()).hexdigest()
def canonical(obj): return json.dumps(obj,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def sha(obj): return hashlib.sha256(canonical(obj)).hexdigest()
def fail(code,status=409): raise HTTPException(status,code)
def user(request,db,roles=None):
    token=request.cookies.get('visionops_session','')
    if not token:fail('authentication_required',401)
    sess=db.scalar(select(UserSession).where(UserSession.token_hash==digest(token),UserSession.expires_at>now()))
    u=db.get(User,sess.user_id) if sess else None
    if not u or u.status!='active': fail('authentication_required',401)
    if roles and u.role not in roles: fail('role_denied',403)
    if request.method not in ('GET','HEAD'):
        if request.headers.get('origin')!=ORIGIN or not secrets.compare_digest(sess.csrf_hash,digest(request.headers.get('x-csrf-token',''))): fail('csrf_denied',403)
    return u
OPS={'fleet_operator','mlops_engineer'}
CV={'cv_engineer','mlops_engineer'}
M={'mlops_engineer'}
VIEW={'safety_viewer','fleet_operator','mlops_engineer'}
def device(request,db,device_id=None):
    token=request.headers.get('authorization','').removeprefix('Bearer ')
    d=db.scalar(select(Device).where(Device.credential_hash==digest(token),Device.registration_status=='enrolled')) if token else None
    if not d or (device_id and d.device_id!=device_id): fail('device_scope_denied',401)
    return d

def verify_signature(manifest,signature,key_id):
    if not key_id.replace('-','').replace('_','').isalnum(): fail('invalid_key_id',422)
    try:
        key=Ed25519PublicKey.from_public_bytes(base64.b64decode((TRUST/(key_id+'.pub')).read_text()))
        key.verify(base64.b64decode(signature,validate=True),canonical(manifest))
    except Exception: fail('signature_invalid',422)

def safe_tar(path):
    with tarfile.open(path) as archive:
        members=archive.getmembers()
        if len(members)>10000 or sum(m.size for m in members)>2*1024**3: fail('archive_limits',422)
        for m in members:
            p=Path(m.name)
            if p.is_absolute() or '..' in p.parts or '\\' in m.name or not (m.isfile() or m.isdir()): fail('unsafe_archive',422)
