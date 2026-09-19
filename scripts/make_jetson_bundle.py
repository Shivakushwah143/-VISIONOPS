"""Package the whole physical-Jetson qualification into one uploadable archive.

    python -m scripts.make_jetson_bundle \
        --onnx var/model/hansung-p3.onnx \
        --contract-record var/model/hansung-p3.json \
        --video var/media/ppe-2.mp4 --frames 0,25,50,100,150,200

writes

    var/bundle/visionops-jetson-validation.zip

which on a real Jetson is unpacked and run with one command:

    unzip visionops-jetson-validation.zip && cd visionops-jetson-validation && ./run_jetson_validation.sh

Two things this deliberately refuses to do.

1. It ships no TensorRT engine. The engine in the T4 evidence would build, load and
   then produce wrong output on Orin, because a plan is compiled against one GPU
   architecture, one TensorRT version and one driver. Engines are built on the Jetson
   by `scripts/verify_jetson_tensorrt.py`; this bundle carries the ONNX lineage that
   makes that build possible. `audit()` fails the bundle if any `*.engine`/`*.plan`
   slipped in.

2. It asserts every declared SHA-256 by reopening the finished archive, and it
   secret-scans the payload. A bundle that cannot be audited is not shipped, because
   the failure would otherwise surface as a confusing error on rented hardware.
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BUNDLE_VERSION = '1.0.0'
EXCLUDED_PARTS = {'.git', '.venv', 'var', 'node_modules', '__pycache__', '.mypy_cache',
                  '.pytest_cache', 'dist', 'build', '.next'}
ENGINE_SUFFIXES = ('.engine', '.plan', '.trt', '.timing')

EXPECTED_FP32_SHA256 = 'b239aa7e881fc590633944cf37df46ed477f3a819d09ec7a7bdb19d052ca2422'
EXPECTED_FP16_SHA256 = 'a74ec6b384c93b2d0bd5d99b3fcc1399712ffee479e9b490c2cda276e1cbea58'

# Files an isolated run imports or executes. A missing entry is a bundle that fails on
# the Jetson, so each one is asserted rather than trusted to `git ls-files`.
REQUIRED_CODE = [
    'scripts/verify_physical_jetson.py',
    'scripts/detect_jetson_environment.py',
    'scripts/collect_jetson_telemetry.py',
    'scripts/verify_jetson_tensorrt.py',
    'scripts/verify_jetson_video_pipeline.py',
    'scripts/verify_model_contract.py',
    'scripts/verify_edge_platform.py',
    'scripts/convert_fp16_onnx.py',
    'shared/model_contract.py',
    'shared/hardware_profiles.py',
    'edge/runtimes.py',
    'edge/pipeline.py',
    'edge/video.py',
    'edge/temporal.py',
    'edge/hardware_telemetry.py',
    'requirements-jetson.txt',
]

# Files that must never travel. `.env*` and key material are matched by name here and
# by content below; `var/` never enters the archive at all.
FORBIDDEN_NAMES = re.compile(r'(^|/)(\.env(\.|$)|id_rsa|id_ed25519|credentials|\.npmrc|'
                             r'\.pypirc|service-account.*\.json|.*\.pem|.*\.key)$', re.IGNORECASE)
SECRET_PATTERNS = [
    ('aws_access_key_id', re.compile(r'AKIA[0-9A-Z]{16}')),
    ('private_key_block', re.compile(r'-----BEGIN (RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----')),
    ('postgres_url_with_password', re.compile(r'postgres(ql)?://[^\s"\']+:[^\s"\'@]+@')),
    ('generic_bearer_token', re.compile(r'(?i)bearer\s+[A-Za-z0-9\-\._~\+/]{20,}={0,2}')),
    ('slack_or_github_token', re.compile(r'(xox[baprs]-[A-Za-z0-9-]{10,}|gh[pousr]_[A-Za-z0-9]{20,})')),
]


def sha256_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path):
    return sha256_bytes(Path(path).read_bytes())


def repository_files():
    """Tracked files plus new-but-unignored ones.

    `--others --exclude-standard` matters: the Jetson scripts and the runtime they
    exercise are frequently newer than the last commit, and listing only `--cached`
    ships a bundle that omits exactly those files. It shipped an unusable GPU bundle
    once. Ignored paths (`var/`, `.venv/`) stay out.
    """
    result = subprocess.run(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
                            cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0 or not result.stdout:
        return sorted(str(path.relative_to(ROOT)).replace('\\', '/')
                      for path in ROOT.rglob('*')
                      if path.is_file() and not EXCLUDED_PARTS & set(path.parts))
    return sorted({name for name in result.stdout.split('\0') if name
                   and not EXCLUDED_PARTS & set(Path(name).parts)})


def git_state():
    try:
        commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True,
                                text=True, timeout=20).stdout.strip()
        dirty = subprocess.run(['git', 'status', '--porcelain'], cwd=ROOT, capture_output=True,
                               text=True, timeout=20).stdout.strip()
        return commit or None, ('dirty' if dirty else 'clean'), len(dirty.splitlines())
    except Exception:  # noqa: BLE001
        return None, 'unknown', None


def extract_frames(video, index_list):
    import cv2
    capture = cv2.VideoCapture(str(video))
    available = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    frames = []
    for index in index_list:
        if index >= available:
            continue
        capture.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = capture.read()
        if not ok:
            continue
        ok, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
        if ok:
            frames.append((f'samples/frame-{index:04d}.jpg', buffer.tobytes(), list(frame.shape)))
    capture.release()
    return frames, available, fps


def scan_secrets(entries):
    findings = []
    for name, payload in entries:
        if FORBIDDEN_NAMES.search(name):
            findings.append({'file': name, 'rule': 'forbidden_filename'})
            continue
        if len(payload) > 2_000_000 or b'\x00' in payload[:4096]:
            continue
        try:
            text = payload.decode('utf-8', errors='ignore')
        except Exception:  # noqa: BLE001
            continue
        for rule, pattern in SECRET_PATTERNS:
            if pattern.search(text):
                findings.append({'file': name, 'rule': rule})
    return findings


def audit(archive, declared):
    """Reopen the finished archive and re-derive every claim from the bytes."""
    problems = []
    with zipfile.ZipFile(archive) as handle:
        if handle.testzip() is not None:
            problems.append({'check': 'zip_integrity', 'detail': 'CRC failure inside the archive'})
        names = handle.namelist()
        payloads = {name: handle.read(name) for name in names}
    for record in declared:
        name = record['archive_path']
        if name not in payloads:
            problems.append({'check': 'declared_file_present', 'detail': name})
            continue
        if sha256_bytes(payloads[name]) != record['sha256']:
            problems.append({'check': 'sha256', 'detail': name})
        if len(payloads[name]) != record['bytes']:
            problems.append({'check': 'size', 'detail': name})
    engines = [name for name in names if name.lower().endswith(ENGINE_SUFFIXES)]
    if engines:
        problems.append({'check': 'no_engine_plan_shipped', 'detail': engines})
    leaks = scan_secrets([(name, payload) for name, payload in payloads.items()])
    if leaks:
        problems.append({'check': 'secret_scan', 'detail': leaks})
    return {'problems': problems, 'file_count': len(names),
            'declared_verified': len(declared) - sum(
                1 for problem in problems if problem['check'] in ('sha256', 'size', 'declared_file_present')),
            'declared_total': len(declared),
            'engines_found': engines, 'secret_findings': leaks,
            # `code/.env.example` is intentionally present and matches none of the forbidden
            # patterns: it is a placeholder template carrying no value, and it documents
            # VISIONOPS_VIDEO_BACKEND / VISIONOPS_RTSP_URL for the operator. This field records
            # that the scan saw it and cleared it, so its presence is a decision, not a leak.
            'env_template': {'present': 'code/.env.example' in payloads,
                             'cleared_by_scan': 'code/.env.example' not in {leak['file'] for leak in leaks},
                             'values_are_placeholders': 'replace-with-generated-random-value'}}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Build the physical-Jetson validation bundle.')
    parser.add_argument('--onnx', default='var/model/hansung-p3.onnx')
    parser.add_argument('--contract-record', default='var/model/hansung-p3.json')
    parser.add_argument('--fp16-onnx', default=None,
                        help='optional mixed-FP16 ONNX; when absent the bundle ships the '
                             'reproducible conversion path instead')
    parser.add_argument('--video', default='var/media/ppe-2.mp4')
    parser.add_argument('--frames', default='0,25,50,100,150,200')
    parser.add_argument('--out', default='var/bundle')
    parser.add_argument('--manifest', default='docs/evidence/jetson/bundle-manifest.json')
    parser.add_argument('--code-only', action='store_true',
                        help='omit the ONNX artifacts (when the model is fetched on-device)')
    arguments = parser.parse_args(argv)

    onnx = Path(arguments.onnx)
    contract = Path(arguments.contract_record)
    video = Path(arguments.video)
    fp16 = Path(arguments.fp16_onnx) if arguments.fp16_onnx else None
    for required in (onnx, contract):
        if not required.is_file():
            raise SystemExit(f'missing required input: {required}')
    if not video.is_file():
        raise SystemExit(f'missing evaluation video: {video}')
    if fp16 and not fp16.is_file():
        raise SystemExit(f'--fp16-onnx given but not found: {fp16}')

    commit, working_tree, dirty_lines = git_state()
    frames, available_frames, source_fps = extract_frames(
        video, [int(value) for value in arguments.frames.split(',') if value.strip()])

    entries = []          # (archive_path, payload, role)
    for name in repository_files():
        path = ROOT / name
        if path.is_file():
            entries.append((f'code/{name}', path.read_bytes(), 'repository'))
    for archive_name, source, role in (('requirements/requirements-jetson.txt',
                                        ROOT / 'requirements-jetson.txt', 'requirements'),
                                       ('run_jetson_validation.sh',
                                        ROOT / 'run_jetson_validation.sh', 'entrypoint'),
                                       ('RUN_ON_JETSON.md', ROOT / 'RUN_ON_JETSON.md',
                                        'documentation')):
        if source.is_file():
            entries.append((archive_name, source.read_bytes(), role))

    model_records = []
    if not arguments.code_only:
        entries.append(('model/' + onnx.name, onnx.read_bytes(), 'model_fp32'))
        model_records.append({'role': 'fp32_onnx', 'archive_path': f'model/{onnx.name}',
                              'bytes': onnx.stat().st_size, 'sha256': sha256_file(onnx),
                              'matches_qualified_lineage': sha256_file(onnx) == EXPECTED_FP32_SHA256})
        entries.append(('model/' + contract.name, contract.read_bytes(), 'model_contract'))
        model_records.append({'role': 'contract_record', 'archive_path': f'model/{contract.name}',
                              'bytes': contract.stat().st_size, 'sha256': sha256_file(contract),
                              'matches_qualified_lineage': None})
        if fp16:
            entries.append(('model/' + fp16.name, fp16.read_bytes(), 'model_fp16'))
            model_records.append({'role': 'mixed_fp16_onnx', 'archive_path': f'model/{fp16.name}',
                                  'bytes': fp16.stat().st_size, 'sha256': sha256_file(fp16),
                                  'matches_qualified_lineage': sha256_file(fp16) == EXPECTED_FP16_SHA256})
    # Ship the clip under a canonical name. The local evaluation video is `ppe-2.mp4`
    # (a copy of a tracked test clip), while RUN_ON_JETSON.md, run_jetson_validation.sh and
    # resolve_layout all address `samples/ppe.mp4`. Shipping the original name produced a
    # bundle whose video phase silently had no source on the device - a failure that would
    # only have appeared on rented hardware.
    video_archive_name = 'samples/ppe.mp4'
    entries.append((video_archive_name, video.read_bytes(), 'evaluation_video'))
    for name, payload, _shape in frames:
        entries.append((name, payload, 'evaluation_sample'))

    missing_code = [name for name in REQUIRED_CODE if f'code/{name}' not in {n for n, _p, _r in entries}]
    if missing_code:
        raise SystemExit('bundle would be unusable; missing required files: ' + ', '.join(missing_code))

    declared = [{'archive_path': name, 'bytes': len(payload), 'sha256': sha256_bytes(payload),
                 'role': role} for name, payload, role in entries]
    declared_samples = [record for record in declared if record['role'] in
                        ('evaluation_sample', 'evaluation_video', 'model_fp32', 'model_fp16',
                         'model_contract', 'requirements', 'entrypoint', 'documentation')]
    contract_version = None
    try:
        payload = json.loads(contract.read_text())
        contract_version = payload.get('contract_version') or payload.get('class_mapping_version') \
            or payload.get('model_version')
    except Exception:  # noqa: BLE001
        contract_version = None

    manifest = {
        'bundle_version': BUNDLE_VERSION,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'git_commit': commit, 'working_tree': working_tree,
        'uncommitted_file_count': dirty_lines,
        'target': {'expected_architecture': 'aarch64', 'physical_hardware_required': True,
                   'reason': 'a TensorRT engine is specific to the GPU, TensorRT version and driver that '
                             'built it; qualification is only meaningful on the device itself'},
        'entrypoint': {'script': 'run_jetson_validation.sh',
                       'documentation': 'RUN_ON_JETSON.md',
                       'module': 'code/scripts/verify_physical_jetson.py'},
        'model': {
            'fp32_onnx': {'sha256': sha256_file(onnx), 'bytes': onnx.stat().st_size,
                          'expected_qualified_sha256': EXPECTED_FP32_SHA256,
                          'matches_qualified_lineage': sha256_file(onnx) == EXPECTED_FP32_SHA256,
                          'archive_path': None if arguments.code_only else f'model/{onnx.name}'},
            'mixed_fp16_onnx': ({'sha256': sha256_file(fp16), 'bytes': fp16.stat().st_size,
                                 'expected_qualified_sha256': EXPECTED_FP16_SHA256,
                                 'matches_qualified_lineage': sha256_file(fp16) == EXPECTED_FP16_SHA256,
                                 'archive_path': f'model/{fp16.name}'} if fp16 else
                                {'sha256': None, 'shipped': False,
                                 'expected_qualified_sha256': EXPECTED_FP16_SHA256,
                                 'reproduction': ('the artifact is not in Git; regenerate it on the '
                                                  'device with code/scripts/convert_fp16_onnx.py, which '
                                                  'restores the Ultralytics metadata ModelOpt strips and '
                                                  'records the new SHA-256 in fp16-generation.json')}),
            'contract_record': {'sha256': sha256_file(contract), 'bytes': contract.stat().st_size,
                                'contract_version': contract_version,
                                'archive_path': None if arguments.code_only else f'model/{contract.name}'},
        },
        'canonical_class_mapping': {'contract': 'hansung_ppe_yolov8n_10',
                                    'source_head': '10 classes, output [1,14,8400]',
                                    'canonical': {'0': 'Hardhat/helmet', '2': 'NO-Hardhat/no_helmet',
                                                  '5': 'Person'},
                                    'note': 'decoded by shared/model_contract.py; the head argmax is NOT '
                                            'used, because a discarded source class can win it'},
        'evaluation_samples': {
            'video': video_archive_name, 'source': str(video).replace('\\', '/'),
            'source_fps': source_fps, 'source_frames_available': available_frames,
            'frame_indices': [int(value) for value in arguments.frames.split(',') if value.strip()],
            'files': [record for record in declared if record['role'] == 'evaluation_sample']},
        'contents': declared_samples,
        'code': {'file_count': sum(1 for _n, _p, role in entries if role == 'repository'),
                 'archive_prefix': 'code/',
                 'reproducible_from': {'git_commit': commit, 'working_tree': working_tree},
                 'includes_uncommitted_files': working_tree != 'clean',
                 'required_files_asserted': REQUIRED_CODE},
        'engines': {'included': False,
                    'why': ('a TensorRT plan built on another host is not portable; engines are built '
                            'here and identified by SHA-256 + size, never shipped')},
        'archive': None,
        'purpose': ('inputs for a physical-Jetson qualification run; contains no result, no engine and '
                    'no metric from any host'),
        'audit': None,
    }

    out = Path(arguments.out)
    out.mkdir(parents=True, exist_ok=True)
    archive = out / ('visionops-jetson-validation.zip' if not arguments.code_only
                     else 'visionops-jetson-validation-code.zip')

    # Order matters: write the payload and audit it *first*, then append MANIFEST.json.
    # The manifest has to record the finished archive's size and SHA-256, and a zip
    # cannot contain its own digest - writing it last is the only way the extracted copy
    # is complete instead of a stub, and it avoids a duplicate-member warning.
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as handle:
        for name, payload, _role in entries:
            handle.writestr(name, payload)
    audit_record = audit(archive, declared)
    manifest['archive'] = {'path': str(archive).replace('\\', '/'), 'bytes': archive.stat().st_size,
                           'sha256': sha256_file(archive),
                           'payload_file_count': audit_record['file_count'],
                           'note': 'this digest covers the payload written before MANIFEST.json '
                                   'was appended, so it cannot be recomputed by a reader from '
                                   'inside the archive'}
    manifest['audit'] = audit_record
    with zipfile.ZipFile(archive, 'a') as handle:
        handle.writestr('MANIFEST.json', json.dumps(manifest, indent=2))
    audit_record['file_count'] += 1  # MANIFEST.json itself, appended above
    Path(arguments.manifest).parent.mkdir(parents=True, exist_ok=True)
    Path(arguments.manifest).write_text(json.dumps(manifest, indent=2))

    print(json.dumps({'archive': {'path': manifest['archive']['path'],
                                  'bytes': manifest['archive']['bytes'],
                                  'sha256': manifest['archive']['sha256'],
                                  'file_count': audit_record['file_count']},
                      'audit': audit_record,
                      'fp32_sha256': manifest['model']['fp32_onnx']['sha256'],
                      'fp16_shipped': bool(fp16), 'code_files': manifest['code']['file_count'],
                      'samples': len(manifest['evaluation_samples']['files']),
                      'manifest': arguments.manifest}, indent=2))
    if audit_record['problems']:
        print('BUNDLE_AUDIT_FAILED', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
