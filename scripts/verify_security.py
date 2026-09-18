import os,tempfile,base64,tarfile,io,json,uuid
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
os.environ.setdefault('TOKEN_PEPPER','runtime-security-check-not-a-release-secret')
from backend.app import security
from fastapi import HTTPException
report={}
with tempfile.TemporaryDirectory(prefix='visionops-security-') as temp:
    root=Path(temp);security.TRUST=root;key=Ed25519PrivateKey.generate();(root/'ephemeral.pub').write_text(base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)).decode());manifest={'release_id':str(uuid.uuid4()),'schema_version':1};signature=base64.b64encode(key.sign(security.canonical(manifest))).decode();security.verify_signature(manifest,signature,'ephemeral');report['valid_ed25519_signature']='VERIFIED'
    try:security.verify_signature({**manifest,'schema_version':2},signature,'ephemeral')
    except HTTPException as e:
        if e.status_code!=422:raise
        report['tampered_manifest_rejected']='VERIFIED'
    else:raise RuntimeError('tampered manifest accepted')
    archive=root/'traversal.tar'
    with tarfile.open(archive,'w') as tar:
        member=tarfile.TarInfo('../outside.txt');member.size=4;tar.addfile(member,io.BytesIO(b'fail'))
    try:security.safe_tar(archive)
    except HTTPException:report['archive_path_traversal_rejected']='VERIFIED'
    else:raise RuntimeError('path traversal accepted')
    with tarfile.open(archive,'w') as tar:
        member=tarfile.TarInfo('escape');member.type=tarfile.SYMTYPE;member.linkname='/etc/passwd';tar.addfile(member)
    try:security.safe_tar(archive)
    except HTTPException:report['archive_symlink_rejected']='VERIFIED'
    else:raise RuntimeError('symlink accepted')
Path('docs/evidence/security-runtime.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
