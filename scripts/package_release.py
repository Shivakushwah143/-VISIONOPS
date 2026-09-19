"""Create a source-only ZIP, inspect it, and verify clean extraction."""
import argparse,json,hashlib,zipfile,tempfile,ast,re
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--output',default='../industrial-visionops-platform.zip');a=p.parse_args();root=Path(__file__).resolve().parents[1];output=Path(a.output).resolve()
excluded={'.venv','node_modules','__pycache__','.git','.idea','.vscode','dist','var','.pytest_cache','.mypy_cache','.ruff_cache','build','.cache'}
def include(path):
    rel=path.relative_to(root)
    return path.is_file() and not any(x in excluded for x in rel.parts) and path.name!='.env' and not path.name.endswith(('.pyc','.log','.partial','.key','.pem','.tsbuildinfo'))
files=sorted(p for p in root.rglob('*') if include(p));required=['START_HERE.md','README.md','pyproject.toml','uv.lock','requirements.lock','frontend/package-lock.json','infrastructure/compose.yaml','docs/ARCHITECTURE.md','docs/IMPLEMENTATION_REPORT.md','docs/VERIFICATION_REPORT.md','docs/BENCHMARK_REPORT.md','docs/REQUIREMENTS_TRACEABILITY.md','docs/ASSUMPTIONS_AND_DECISIONS.md','docs/KNOWN_LIMITATIONS.md','docs/CURRENT_VERIFIED_STATE.md','docs/ROLLBACK_STRATEGY.md','docs/JETSON_DEPLOYMENT_TARGET.md','docs/LOCAL_RTSP.md','docs/10K_FLEET_SCALING_REPORT.md']
for name in required:
    file=root/name
    if not file.is_file() or not file.stat().st_size:raise RuntimeError('Missing required file '+name)
for folder in ['backend','frontend','edge','training','simulation','observability','infrastructure','scripts','shared','docs']:
    if not any(p.is_relative_to(root/folder) for p in files):raise RuntimeError('Empty required directory '+folder)
mdfiles=[p for p in files if p.suffix=='.md']
for file in mdfiles:
    text=file.read_text()
    if not text.strip():raise RuntimeError('Empty Markdown '+str(file))
for file in files:
    if file.suffix=='.py':ast.parse(file.read_text(),filename=str(file))
    if file.suffix in ('.py','.md','.json','.yaml','.yml','.toml','.ini','.conf','.txt'):
        content=file.read_text()
        if re.search(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',content):raise RuntimeError('Private key found in '+str(file))
        if ('/workspace'+'/scratch/') in content:raise RuntimeError('Development absolute path in '+str(file))
report={'status':'VERIFIED','meaning':'Archive source content and extraction audit, not P0 functional approval','markdown_files_read':len(mdfiles),'required_files':required,'excluded_categories':sorted(excluded|{'.env','private keys','logs','bytecode'}),'specification_files':22,'functional_release_status':'NOT IMPLEMENTED'}
audit=root/'docs/evidence/package-content-audit.json';audit.write_text(json.dumps(report,indent=2));files=sorted(p for p in root.rglob('*') if include(p))
with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
    for file in files:z.write(file,arcname=Path('visionops')/file.relative_to(root))
with zipfile.ZipFile(output) as z:
    if z.testzip():raise RuntimeError('ZIP CRC failure')
    names=z.namelist()
    for name in required:
        if 'visionops/'+name not in names:raise RuntimeError('Missing archive path '+name)
    if any(not n.startswith('visionops/') or '..' in Path(n).parts for n in names):raise RuntimeError('Unsafe archive path')
    with tempfile.TemporaryDirectory(prefix='visionops-clean-extract-') as folder:
        z.extractall(folder);extracted=Path(folder)/'visionops'
        for file in files:
            rel=file.relative_to(root)
            if hashlib.sha256(file.read_bytes()).digest()!=hashlib.sha256((extracted/rel).read_bytes()).digest():raise RuntimeError('Extraction mismatch '+str(rel))
report.update(zip_file=output.name,zip_sha256=hashlib.sha256(output.read_bytes()).hexdigest(),archive_files=len(files),zip_bytes=output.stat().st_size,clean_extraction='VERIFIED')
output.with_name('archive-audit.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
