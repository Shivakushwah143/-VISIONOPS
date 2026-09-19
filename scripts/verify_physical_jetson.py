"""One command that qualifies the real VisionOps model on a PHYSICAL NVIDIA Jetson.

    python3 -m scripts.verify_physical_jetson \
        --bundle-root . --samples samples --video samples/ppe.mp4 \
        --precisions fp32,fp16 --iterations 100 \
        --sustained-seconds 300 --out evidence/jetson

It orchestrates, in order:

    environment detection -> model identity -> canonical contract
    -> TensorRT engine build ON THIS DEVICE -> parity -> benchmark
    -> tegrastats telemetry -> real video pipeline -> optional RTSP
    -> optional DeepStream inspection -> sustained run -> evidence ZIP

Three rules this script is built around.

1. It does not run anywhere but a real Jetson. The first phase is
   `scripts.detect_jetson_environment.detect()`, which needs actual Tegra evidence
   (`/etc/nv_tegra_release`, JetPack packages, an ARM64 kernel) on an aarch64 host.
   Anything else exits ``BLOCKED_NOT_PHYSICAL_JETSON`` (3). There is no flag that
   turns an x86 host into a Jetson: ``--allow-partial`` widens the gate only to
   ``PARTIAL_JETSON_ENVIRONMENT``, and a run in that state can never produce a
   status-patch that authorises a Jetson claim.

2. TensorRT engines are built on the device that runs them. Nothing in the bundle
   is a prebuilt engine, and this script never copies one in: a plan built for a
   Tesla T4 is not merely slower on Orin, it is invalid. Engines are written to a
   scratch directory and recorded by SHA-256 + size rather than shipped.

3. A failed phase still produces evidence. Every phase records its exact command,
   exit code, stdout/stderr tails and duration into ``phases.json``; on failure the
   archive is still packaged, because "it failed at step 6 with this error on this
   hardware" is the result. Only the status-patch is gated, and it authorises a
   claim only when the matching evidence file exists.

Exit codes:
    0  qualification complete (every requested phase VERIFIED)
    3  BLOCKED_NOT_PHYSICAL_JETSON
    4  ran on a Jetson, but at least one phase failed (evidence still packaged)
    5  PARTIAL_JETSON_ENVIRONMENT (needs --allow-partial to proceed)
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

COMPLETE = 0
BLOCKED_NOT_PHYSICAL_JETSON = 3
PHASE_FAILED = 4
PARTIAL_BLOCKED = 5

# The qualified lineage these artifacts must match. A mismatch aborts before any
# engine is built: qualifying a different model than the one the evidence trail
# describes would silently invalidate the T4 comparison.
EXPECTED_FP32_SHA256 = 'b239aa7e881fc590633944cf37df46ed477f3a819d09ec7a7bdb19d052ca2422'
EXPECTED_FP16_SHA256 = 'a74ec6b384c93b2d0bd5d99b3fcc1399712ffee479e9b490c2cda276e1cbea58'

TAIL_CHARS = 4000


def utc():
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def tail(text, limit=TAIL_CHARS):
    text = (text or '').strip()
    return text if len(text) <= limit else '...' + text[-limit:]


def run(command, timeout, cwd=None, env=None):
    """Run a phase command, capturing everything a later reader needs."""
    started = time.time()
    try:
        completed = subprocess.run(command, cwd=str(cwd) if cwd else None, env=env,
                                   capture_output=True, text=True, timeout=timeout)
        return {'command': command, 'exit_code': completed.returncode,
                'stdout_tail': tail(completed.stdout), 'stderr_tail': tail(completed.stderr),
                'duration_s': round(time.time() - started, 3), 'timeout_s': timeout}
    except subprocess.TimeoutExpired as expired:
        return {'command': command, 'exit_code': None, 'timeout': True,
                'stdout_tail': tail(expired.stdout if isinstance(expired.stdout, str) else ''),
                'stderr_tail': tail(expired.stderr if isinstance(expired.stderr, str) else ''),
                'duration_s': round(time.time() - started, 3), 'timeout_s': timeout}
    except FileNotFoundError as missing:
        return {'command': command, 'exit_code': None, 'missing_executable': str(missing),
                'stdout_tail': '', 'stderr_tail': str(missing),
                'duration_s': round(time.time() - started, 3), 'timeout_s': timeout}


def probe(command, timeout=30):
    """Read-only capability probe: never raises, never changes device state."""
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        present = completed.returncode == 0
        return {'command': command, 'available': present, 'exit_code': completed.returncode,
                'output': tail(completed.stdout or completed.stderr, 1500)}
    except Exception as error:  # noqa: BLE001
        return {'command': command, 'available': False, 'exit_code': None,
                'output': f'{type(error).__name__}: {error}'}


class Ledger:
    """Ordered phase record. Written incrementally so a crash still leaves evidence."""

    def __init__(self, out):
        self.out = out
        self.phases = []
        self.path = out / 'phases.json'

    def add(self, name, status, **payload):
        record = {'phase': name, 'status': status, 'at': utc(), **payload}
        self.phases.append(record)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({'phases': self.phases}, indent=2, default=str))
        summary = {key: record[key] for key in ('exit_code', 'duration_s', 'evidence')
                   if key in record}
        print(json.dumps({'phase': name, 'status': status, **summary}), flush=True)
        return record

    def tried(self, name):
        return any(record['phase'] == name and record['status'] == 'VERIFIED'
                   for record in self.phases)


def environment_fields(environment):
    """Flatten the detector record into the fields a status document needs.

    Read through this helper rather than reaching into the record directly: the
    detector nests host/versions/verdict, and guessing a top-level key is how a
    `null` silently replaces a real value in a status patch.
    """
    versions = environment.get('versions') or {}
    host = environment.get('host') or {}
    verdict = environment.get('verdict') or {}
    jetson = environment.get('jetson') or {}
    return {
        'state': environment.get('state'),
        'physical_jetson': bool(environment.get('physical_jetson')),
        'architecture': host.get('architecture'),
        'aarch64': host.get('aarch64'),
        'os': host.get('os'),
        'kernel': host.get('kernel'),
        'python': host.get('python'),
        'hostname': host.get('hostname'),
        'family': jetson.get('family'),
        'model': jetson.get('model'),
        'reason': verdict.get('reason'),
        'strong_markers': verdict.get('strong_markers'),
        'weak_markers': verdict.get('weak_markers'),
        'jetpack': versions.get('jetpack'),
        'l4t': versions.get('l4t'),
        'cuda': versions.get('cuda'),
        'cudnn': versions.get('cudnn'),
        'tensorrt': versions.get('tensorrt'),
    }


def resolve_layout(bundle_root, arguments):
    """Find model/sample/code paths in the bundle layout *and* in a plain checkout.

    The bundle is not a checkout: `model/`, `samples/` and `code/` are siblings, and
    `run_jetson_validation.sh` cd's into `code/` to run. A developer running this in
    the repository instead has `var/model/` (gitignored) and `scripts/`. Both work,
    and every path can be overridden explicitly.
    """
    root = Path(bundle_root).resolve()
    code_root = root / 'code' if (root / 'code' / 'scripts').is_dir() else root
    if not (code_root / 'scripts' / 'verify_physical_jetson.py').is_file() and code_root != ROOT:
        code_root = ROOT

    def pick(explicit, candidates):
        if explicit:
            return Path(explicit).resolve()
        for candidate in candidates:
            if candidate.is_file():
                return candidate.resolve()
        return None

    onnx = pick(arguments.onnx, [root / 'model' / 'hansung-p3.onnx',
                                 root / 'var' / 'model' / 'hansung-p3.onnx',
                                 ROOT / 'var' / 'model' / 'hansung-p3.onnx'])
    fp16 = pick(arguments.fp16_onnx, [root / 'model' / 'hansung-p3-fp16.onnx',
                                      root / 'var' / 'model' / 'hansung-p3-fp16.onnx',
                                      ROOT / 'var' / 'model' / 'hansung-p3-fp16.onnx'])
    contract = pick(arguments.contract_record, [root / 'model' / 'hansung-p3.json',
                                                root / 'var' / 'model' / 'hansung-p3.json',
                                                ROOT / 'var' / 'model' / 'hansung-p3.json'])
    samples = Path(arguments.samples_dir).resolve() if arguments.samples_dir else None
    if samples is None or not samples.is_dir():
        for candidate in (root / 'samples', root / 'var' / 'samples', ROOT / 'var' / 'samples'):
            if candidate.is_dir():
                samples = candidate.resolve()
                break
    video = pick(arguments.video, [root / 'samples' / 'ppe.mp4',
                                   root / 'var' / 'media' / 'ppe-2.mp4',
                                   ROOT / 'var' / 'media' / 'ppe-2.mp4'])
    if video is not None and not video.is_file():
        # A mistyped --video must not silently become a different clip, but it also must not
        # become "no source": the video phase would then have nothing to decode on the device.
        # Only the video is treated this way; a missing --onnx stays a hard failure.
        video = None
    if video is None:
        # Last resort: a single clip in the samples directory, whatever it is called. The
        # bundle names its clip canonically, but a hand-made one may not.
        for directory in (samples, root / 'var' / 'media', ROOT / 'var' / 'media'):
            if directory and directory.is_dir():
                clips = sorted(path for path in directory.iterdir()
                               if path.suffix.lower() in ('.mp4', '.mkv', '.avi', '.mov'))
                if clips:
                    video = clips[0].resolve()
                    break
    return {'root': root, 'code_root': code_root, 'onnx': onnx, 'fp16_onnx': fp16,
            'contract': contract, 'video': video, 'samples': samples}


def model_identity(layout, out):
    """Hash the artifacts before touching TensorRT and compare to the qualified lineage."""
    record = {'expected_fp32_sha256': EXPECTED_FP32_SHA256,
              'expected_fp16_sha256': EXPECTED_FP16_SHA256, 'artifacts': {}}
    for role, path in (('fp32_onnx', layout['onnx']), ('fp16_onnx', layout['fp16_onnx']),
                       ('contract_record', layout['contract']), ('video', layout['video'])):
        if path is None or not path.is_file():
            record['artifacts'][role] = {'path': None, 'present': False}
            continue
        digest = sha256_file(path)
        record['artifacts'][role] = {'path': str(path), 'present': True,
                                     'bytes': path.stat().st_size, 'sha256': digest}
    fp32 = record['artifacts']['fp32_onnx']
    record['fp32_matches_qualified_lineage'] = fp32.get('sha256') == EXPECTED_FP32_SHA256
    fp16 = record['artifacts']['fp16_onnx']
    record['fp16_present'] = bool(fp16.get('present'))
    record['fp16_matches_qualified_lineage'] = fp16.get('sha256') == EXPECTED_FP16_SHA256
    record['model_versions'] = {}
    if layout['contract'] and layout['contract'].is_file():
        try:
            payload = json.loads(layout['contract'].read_text())
            record['model_versions'] = {key: payload.get(key) for key in
                                        ('model_name', 'model_version', 'contract_version',
                                         'class_mapping_version', 'runtime', 'hardware_profile',
                                         'input_shape', 'artifact_sha256', 'source_mlflow_run',
                                         'dataset_version') if key in payload}
        except Exception as error:  # noqa: BLE001
            record['contract_read_error'] = f'{type(error).__name__}: {error}'
    (out / 'model-identity.json').write_text(json.dumps(record, indent=2, default=str))
    return record


def ensure_mixed_fp16(layout, out, ledger, code_root, python):
    """Regenerate the mixed-FP16 graph on this device when the bundle did not ship it.

    The 5.9 MB artifact itself is not in Git and may not be in the bundle, but its
    lineage is: the FP32 ONNX plus `scripts/convert_fp16_onnx.py`, which restores the
    Ultralytics metadata that ModelOpt strips. Regenerating is a first-class path, not
    a fallback - but the SHA is then a new one, and it is recorded as such.
    """
    if layout['fp16_onnx'] and layout['fp16_onnx'].is_file():
        return layout['fp16_onnx'], None
    target = out / 'model' / 'hansung-p3-fp16.onnx'
    target.parent.mkdir(parents=True, exist_ok=True)
    record = run([python, '-m', 'scripts.convert_fp16_onnx', '--onnx', str(layout['onnx']),
                  '--out', str(target)], timeout=1800, cwd=code_root)
    record['evidence'] = str(out / 'fp16-generation.json')
    (out / 'fp16-generation.json').write_text(json.dumps(
        {'reason': 'bundle did not carry model/hansung-p3-fp16.onnx; regenerated on-device',
         'expected_qualified_sha256': EXPECTED_FP16_SHA256, **record}, indent=2, default=str))
    if record['exit_code'] == 0 and target.is_file():
        digest = sha256_file(target)
        ledger.add('mixed-fp16-generation', 'VERIFIED', evidence=record['evidence'],
                   sha256=digest, matches_qualified_lineage=digest == EXPECTED_FP16_SHA256,
                   note='on-device regeneration; a differing SHA is recorded, not hidden')
        return target, digest
    ledger.add('mixed-fp16-generation', 'FAILED', exit_code=record['exit_code'],
               stderr_tail=record['stderr_tail'], evidence=record['evidence'])
    return None, None


def deepstream_phase(out):
    """DeepStream is optional and is never installed automatically."""
    directory = out / 'deepstream'
    directory.mkdir(parents=True, exist_ok=True)
    app = probe(['deepstream-app', '--version'])
    plugins = probe(['gst-inspect-1.0'], timeout=60)
    nvidia_plugins = sorted({line.split(':')[0].strip() for line in
                             (plugins.get('output') or '').splitlines()
                             if line.strip().startswith('nv') or ' nv' in line[:12]})
    record = {'deepstream_app': app, 'gstreamer_plugin_tools': plugins['available'],
              'nvidia_plugin_tokens': nvidia_plugins[:40]}
    if not app['available']:
        record.update({'status': 'NOT AVAILABLE ON HOST',
                       'inference_executed': False,
                       'note': ('installing the DeepStream SDK is deliberately not automated; '
                                'the adapter and its generated canonical contract are shipped, '
                                'and the runtime stays NOT VERIFIED')})
        (directory / 'NOT_AVAILABLE.json').write_text(json.dumps(record, indent=2, default=str))
        return 'NOT AVAILABLE ON HOST', directory / 'NOT_AVAILABLE.json'
    # Present but not exercised by this script: the DeepStream adapter is a separate
    # C++/gst binary (edge/pipeline/deepstream) and running it here would attribute its
    # output to the TensorRT path. Its availability is reported; its silence is not.
    record.update({'status': 'PRESENT — RUN NOT REQUESTED BY THIS SCRIPT',
                   'inference_executed': False,
                   'how_to_run': 'cd code/edge/pipeline/deepstream && make && ./visionops-deepstream --help'})
    (directory / 'result.json').write_text(json.dumps(record, indent=2, default=str))
    return 'PRESENT — NOT EXERCISED', directory / 'result.json'


def sustained_phase(arguments, layout, out, ledger, python, code_root, rtsp_url):
    """Application-inference endurance test, not a hardware stress test.

    tegrastats is started first and the real video pipeline runs inside its window, so
    the telemetry covers actual inference rather than an idle SoC.
    """
    seconds = max(0.0, float(arguments.sustained_seconds))
    if seconds <= 0:
        ledger.add('sustained', 'NOT REQUESTED', note='--sustained-seconds 0')
        return None
    directory = out / 'sustained'
    directory.mkdir(parents=True, exist_ok=True)
    telemetry_command = [python, '-m', 'scripts.collect_jetson_telemetry', '--seconds',
                         str(seconds), '--out', str(directory)]
    started = time.time()
    telemetry = subprocess.Popen(telemetry_command, cwd=str(code_root),
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    video_command = [python, '-m', 'scripts.verify_jetson_video_pipeline',
                     '--onnx', str(layout['onnx']), '--runtime', arguments.runtime,
                     '--precision', 'fp32', '--backend', arguments.backend,
                     '--seconds', str(seconds), '--inference-fps', str(arguments.inference_fps),
                     '--out', str(directory)]
    if layout['video']:
        video_command += ['--video', str(layout['video'])]
    if rtsp_url:
        video_command += ['--rtsp-url', rtsp_url]
    video = run(video_command, timeout=int(seconds) + 900, cwd=code_root)
    try:
        telemetry_stdout, telemetry_stderr = telemetry.communicate(timeout=max(120, seconds + 300))
        telemetry_exit = telemetry.returncode
    except subprocess.TimeoutExpired:
        telemetry.kill()
        telemetry_stdout, telemetry_stderr, telemetry_exit = '', 'tegrastats collector timed out', None
    video_payload = {}
    video_file = directory / 'video' / 'video-pipeline.json'
    if video_file.is_file():
        try:
            video_payload = json.loads(video_file.read_text())
        except Exception:  # noqa: BLE001
            video_payload = {}
    record = {'requested_seconds': seconds, 'wall_clock_s': round(time.time() - started, 3),
              'purpose': 'application inference endurance; NOT a hardware stress test',
              'telemetry': {'command': telemetry_command, 'exit_code': telemetry_exit,
                            'stdout_tail': tail(telemetry_stdout),
                            'stderr_tail': tail(telemetry_stderr)},
              'video': video,
              'processed_fps': video_payload.get('processed_fps'),
              'events_durable': video_payload.get('events_durable'),
              'latency': video_payload.get('latency'),
              'worker_errors': video_payload.get('errors')}
    if not record['latency'] and isinstance(video_payload.get('run'), dict):
        record['latency'] = video_payload['run'].get('latency')
    (directory / 'sustained_run.json').write_text(json.dumps(record, indent=2, default=str))
    status = 'VERIFIED' if video['exit_code'] == 0 else 'FAILED'
    ledger.add('sustained', status, exit_code=video['exit_code'], evidence=str(directory / 'sustained_run.json'),
               processed_fps=record['processed_fps'], events_durable=record['events_durable'])
    return record


def build_status_patch(out, environment, identity, phases, ledgers_evidence):
    """Authorise a claim ONLY where the matching evidence exists.

    The gate is evidence-file presence plus the phase's own VERIFIED status, so a
    missing file or a failed phase cannot be laundered into a documentation change.
    Both lists are explicit: what a successful run may edit, and what it may never.
    """
    verified = {record['phase'] for record in phases if record['status'] == 'VERIFIED'}
    available = {name: path.is_file() for name, path in ledgers_evidence.items()}
    fields = environment_fields(environment)
    confirmed = fields['state'] == 'PHYSICAL_JETSON_CONFIRMED'

    def authorised(*requirements):
        return all(available.get(requirement, False) for requirement in requirements)

    # `verify_jetson_tensorrt` writes its summary into its own --out, which the orchestrator
    # passes as <out>/tensorrt. Looking for it at <out> directly silently produced an empty
    # summary, and because the precision gate then saw no `label_backed_by_engine_dtypes`,
    # the TensorRT-on-Jetson claim could never be authorised even on a real device. The
    # parity and engine-metadata files are now the fallback source for that same field.
    summary_path = out / 'tensorrt' / 'tensorrt-qualification.json'
    if not summary_path.is_file():
        summary_path = out / 'tensorrt-qualification.json'
    tensorrt = json.loads(summary_path.read_text()) if summary_path.is_file() else {}
    reported = tensorrt.get('precisions') or {}
    precision_verified = {}
    for precision in ('fp32', 'fp16'):
        parity = out / 'tensorrt' / precision / 'parity.json'
        benchmark = out / 'tensorrt' / precision / 'benchmark.json'
        metadata = out / 'tensorrt' / precision / 'engine-metadata.json'
        payload = json.loads(parity.read_text()) if parity.is_file() else {}
        engine_record = json.loads(metadata.read_text()) if metadata.is_file() else {}
        entry = reported.get(precision) or {}
        label_backed = entry.get('label_backed_by_engine_dtypes')
        if label_backed is None:
            label_backed = payload.get('label_backed_by_engine_dtypes')
        if label_backed is None:
            label_backed = payload.get('engine_dtypes_declare_requested_precision')
        precision_verified[precision] = {
            'status': entry.get('status') or payload.get('status') or 'NOT VERIFIED',
            'label_backed_by_engine_dtypes': label_backed,
            'actual_engine_precision': entry.get('actual_engine_precision')
                                     or payload.get('actual_engine_precision')
                                     or payload.get('actual_engine_precision_label'),
            'engine_dtypes': engine_record.get('engine_dtypes') or payload.get('engine_dtypes'),
            'summary_source': str(summary_path) if summary_path.is_file() else None,
            'requires': [str(parity), str(metadata)],
            'evidence_present': parity.is_file() and metadata.is_file(),
        }

    patch = {
        'generated_at': utc(),
        'note': ('only a physical run produces this file; a claim appears here only when its '
                 'evidence file exists, and the T4 evidence is never used as Jetson evidence'),
        'physical_jetson_confirmed': confirmed,
        'authorised_changes': [],
        'not_authorised': [],
        'verified_phases': sorted(verified),
    }
    if confirmed and authorised('jetson_environment'):
        patch['authorised_changes'] += [
            {'claim': 'Physical Jetson: VERIFIED',
             'evidence': [str(out / 'jetson_environment.json')],
             'state': fields['state'], 'family': fields['family'], 'model': fields['model']},
            {'claim': 'ARM64/aarch64 runtime execution: VERIFIED',
             'evidence': [str(out / 'jetson_environment.json')],
             'architecture': fields['architecture']},
        ]
        if fields['jetpack'] or fields['l4t']:
            patch['authorised_changes'].append(
                {'claim': 'JetPack: VERIFIED', 'evidence': [str(out / 'jetson_environment.json')],
                 'jetpack': fields['jetpack'], 'l4t': fields['l4t']})
    else:
        patch['not_authorised'].append(
            {'claim': 'Physical Jetson / JetPack / ARM64', 'reason': 'environment not confirmed'})

    for precision in ('fp32', 'fp16'):
        record = precision_verified[precision]
        if confirmed and verified and record['evidence_present'] and \
                record['status'] == 'VERIFIED' and record['label_backed_by_engine_dtypes'] is True:
            patch['authorised_changes'].append({
                'claim': f'TensorRT on Jetson ({precision}): VERIFIED',
                'evidence': [str(out / 'tensorrt' / precision / 'parity.json'),
                             str(out / 'tensorrt' / precision / 'engine-metadata.json')],
                'actual_engine_precision': record['actual_engine_precision'],
                'engine_dtypes': record['engine_dtypes'],
                'label_backed_by_engine_dtypes': True,
                'tensorrt_version': fields['tensorrt'], 'cuda': fields['cuda']})
        else:
            patch['not_authorised'].append({
                'claim': f'TensorRT on Jetson ({precision}): VERIFIED',
                'reason': ('needs a confirmed Jetson, a parity.json with status VERIFIED, and an engine '
                           'whose own tensor dtypes back the label'),
                'observed': {'status': record['status'],
                             'label_backed_by_engine_dtypes': record['label_backed_by_engine_dtypes'],
                             'confirmed_jetson': confirmed, 'evidence_present': record['evidence_present']}})
    # Only name the editable files when there is at least one real claim to record. An
    # editing list with nothing to edit reads as permission and invites a hand-written
    # upgrade, which is the failure mode this whole gate exists to prevent.
    if patch['authorised_changes']:
        patch['authorised_changes'].append(
            {'claim': 'CURRENT_VERIFIED_STATE.md: the physical-Jetson rows only',
             'files': ['docs/CURRENT_VERIFIED_STATE.md', 'docs/JETSON_DEPLOYMENT_TARGET.md',
                       'docs/interview/11_VERIFIED_VS_UNVERIFIED.md',
                       'docs/interview/TENSORRT_INTERVIEW_EVIDENCE.md', 'README.md'],
             'scope': 'only the claims listed above; every other row keeps its current state'})

    patch['not_authorised'] += [
        {'claim': 'DeepStream runtime: VERIFIED', 'reason': 'only set when result.json shows inference_executed'},
        {'claim': 'NVDEC / hardware decode: VERIFIED',
         'reason': 'only set when video/hardware-decode.json shows HARDWARE_DECODE_RUNTIME_VERIFIED'},
        {'claim': 'RTSP on Jetson: VERIFIED', 'reason': 'only set by a real rtsp/reconnect.json'},
        {'claim': 'Thermal stability / sustained power: VERIFIED',
         'reason': 'tegrastats samples a window; that is telemetry, not a thermal qualification'},
        {'claim': '10K physical Jetson fleet: VERIFIED', 'reason': 'requires 10000 real devices'},
        {'claim': 'x86/desktop TensorRT parity as Jetson evidence',
         'reason': 'the Tesla T4 run is a separate host and a separate evidence directory'},
    ]
    (out / 'status-patch.json').write_text(json.dumps(patch, indent=2, default=str))
    return patch


def package_evidence(out, arguments, ledger):
    """Archive the evidence. Engine plans are excluded by default and hashed instead."""
    archive = Path(arguments.evidence_zip) if arguments.evidence_zip else \
        Path(arguments.out).parent / 'visionops-jetson-evidence.zip'
    include_engines = arguments.keep_engines
    entries = []
    skipped = []
    for path in sorted(out.rglob('*')):
        if not path.is_file():
            continue
        relative = path.relative_to(out).as_posix()
        if 'engines' in path.parts and not include_engines:
            skipped.append({'file': relative, 'bytes': path.stat().st_size,
                            'sha256': sha256_file(path), 'reason': 'engine plan: hardware-specific'})
            continue
        entries.append((relative, path.read_bytes()))
    if not any(name == 'phases.json' for name, _payload in entries):
        entries.append(('phases.json', (out / 'phases.json').read_bytes()
                        if (out / 'phases.json').is_file() else b'{}'))
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as handle:
        for name, payload in entries:
            handle.writestr(name, payload)
        handle.writestr('manifest.json', json.dumps({
            'created_at': utc(), 'out_dir': str(out), 'file_count': len(entries),
            'excluded_engine_plans': skipped,
            'note': 'engines are excluded by default: a TensorRT plan is valid only on the device '
                    'and TensorRT version that built it, so it is identified by hash, not shipped',
        }, indent=2, default=str))
    record = {'path': str(archive), 'bytes': archive.stat().st_size,
              'sha256': sha256_file(archive), 'file_count': len(entries),
              'excluded_engine_plans': len(skipped)}
    ledger.add('evidence-package', 'VERIFIED', evidence=str(archive), **record)
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Qualify VisionOps on a physical NVIDIA Jetson (refuses to run elsewhere).')
    parser.add_argument('--bundle-root', default='.', help='unpacked bundle root, or the repository')
    parser.add_argument('--onnx', default=None, help='override the FP32 ONNX path')
    parser.add_argument('--fp16-onnx', default=None, help='mixed-FP16 ONNX, if the bundle carries it')
    parser.add_argument('--contract-record', default=None)
    parser.add_argument('--samples-dir', default=None)
    parser.add_argument('--video', default=None)
    parser.add_argument('--frames', default='0,25,50,100,150,200')
    parser.add_argument('--precisions', default='fp32,fp16')
    parser.add_argument('--iterations', type=int, default=100)
    parser.add_argument('--warmup', type=int, default=20)
    parser.add_argument('--inference-fps', type=float, default=5.0)
    parser.add_argument('--runtime', default='auto', choices=['auto', 'tensorrt', 'onnx-cpu'])
    parser.add_argument('--backend', default='auto', choices=['auto', 'gstreamer', 'opencv'])
    parser.add_argument('--video-seconds', type=float, default=60.0,
                        help='duration of the standalone video-pipeline phase')
    parser.add_argument('--rtsp-url', default=None,
                        help='optional real RTSP source; local video is the guaranteed path')
    parser.add_argument('--test-rtsp-reconnect', action='store_true')
    parser.add_argument('--sustained-seconds', type=float, default=300.0)
    parser.add_argument('--enable-max-performance', action='store_true',
                        help='opt-in only: never applied automatically, provider limits win')
    parser.add_argument('--allow-partial', action='store_true',
                        help='proceed on PARTIAL_JETSON_ENVIRONMENT; never authorises a Jetson claim')
    parser.add_argument('--keep-engines', action='store_true',
                        help='include engine plans in the evidence ZIP (default: hash only)')
    parser.add_argument('--evidence-zip', default=None)
    parser.add_argument('--out', default='evidence/jetson')
    parser.add_argument('--skip-video', action='store_true')
    arguments = parser.parse_args(argv)

    out = Path(arguments.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    ledger = Ledger(out)
    python = sys.executable
    layout = resolve_layout(arguments.bundle_root, arguments)
    record = {'started_at': utc(), 'arguments': vars(arguments), 'layout': {
        key: (str(value) if value else None) for key, value in layout.items()}}

    # ---- Phase 1: is this actually a Jetson? -------------------------------
    from scripts.detect_jetson_environment import CONFIRMED, NOT_DETECTED, PARTIAL, detect
    environment = detect(enable_max_performance=arguments.enable_max_performance)
    (out / 'jetson_environment.json').write_text(json.dumps(environment, indent=2, default=str))
    state = environment.get('state')
    record['environment_state'] = state

    if state != CONFIRMED:
        gate = PARTIAL if (state == PARTIAL and arguments.allow_partial) else None
        fields = environment_fields(environment)
        blocked = {
            'status': 'BLOCKED_NOT_PHYSICAL_JETSON', **fields,
            'proceeded': gate is not None,
            'what_this_does_not_mean': (
                'the x86 TensorRT verification on the Tesla T4 remains valid and separate; this '
                'file only records that a physical-Jetson claim could not be made from this host'),
            'required_to_proceed': ['/etc/nv_tegra_release or an equivalent L4T marker',
                                    'aarch64 machine', 'JetPack packages or tegrastats'],
        }
        (out / 'blocked.json').write_text(json.dumps({**record, **blocked}, indent=2, default=str))
        # Phase 20 says generate the status patch only after a physical Jetson is confirmed.
        # Write the file anyway with an empty authorisation list, so a reader can see the
        # question was asked and withheld rather than that it was never considered.
        (out / 'status-patch.json').write_text(json.dumps({
            'generated_at': utc(), 'physical_jetson_confirmed': False,
            'withheld': True, 'authorised_changes': [],
            'reason': f'host is {state}; a status patch is produced only by a confirmed physical run',
            'not_authorised': [
                {'claim': 'Physical Jetson / JetPack / ARM64', 'reason': 'environment not confirmed'},
                {'claim': 'TensorRT on Jetson', 'reason': 'no on-device engine was built'},
                {'claim': 'DeepStream / NVDEC / RTSP / thermal / power',
                 'reason': 'no phase ran; nothing was measured'},
                {'claim': 'any claim derived from the Tesla T4 run',
                 'reason': 'different host, different GPU architecture, separate evidence directory'},
            ],
            'note': ('the x86 T4 verification is unaffected by this file; this host simply cannot '
                     'make a Jetson claim'),
        }, indent=2, default=str))
        ledger.add('environment', state, evidence=str(out / 'jetson_environment.json'),
                   exit_code=NOT_DETECTED if state != PARTIAL else PARTIAL)
        if gate is None:
            ledger.add('gate', 'BLOCKED_NOT_PHYSICAL_JETSON',
                       reason='the first phase must confirm a physical Jetson; nothing else ran')
            package_evidence(out, arguments, ledger)
            print(json.dumps(blocked, indent=2, default=str))
            print('BLOCKED_NOT_PHYSICAL_JETSON', flush=True)
            return BLOCKED_NOT_PHYSICAL_JETSON
        ledger.add('gate', 'PROCEEDING_ON_PARTIAL_ENVIRONMENT',
                   note='--allow-partial; no Jetson claim will be authorised by this run')

    fields = environment_fields(environment)
    record['environment'] = fields
    ledger.add('environment', state, evidence=str(out / 'jetson_environment.json'),
               jetpack=fields['jetpack'], l4t=fields['l4t'], cuda=fields['cuda'],
               tensorrt=fields['tensorrt'], architecture=fields['architecture'],
               family=fields['family'], model=fields['model'])

    # ---- Phase 2: model identity ------------------------------------------
    identity = model_identity(layout, out)
    if not identity['artifacts']['fp32_onnx'].get('present'):
        ledger.add('model-identity', 'FAILED', reason='FP32 ONNX not found in the bundle')
        package_evidence(out, arguments, ledger)
        print(json.dumps({'status': 'MODEL_ARTIFACT_MISSING', **record}, indent=2, default=str))
        return PHASE_FAILED
    if not identity['fp32_matches_qualified_lineage']:
        ledger.add('model-identity', 'FAILED', reason='FP32 ONNX SHA-256 differs from the qualified lineage',
                   found=identity['artifacts']['fp32_onnx']['sha256'], expected=EXPECTED_FP32_SHA256)
        package_evidence(out, arguments, ledger)
        print(json.dumps({'status': 'MODEL_ARTIFACT_MISMATCH', **record}, indent=2, default=str))
        return PHASE_FAILED
    ledger.add('model-identity', 'VERIFIED', evidence=str(out / 'model-identity.json'),
               sha256=identity['artifacts']['fp32_onnx']['sha256'],
               fp16_present=identity['fp16_present'])

    # ---- Phase 3: canonical contract semantics -----------------------------
    semantics = run([python, '-m', 'scripts.verify_model_contract', '--onnx', str(layout['onnx']),
                     '--out', str(out / 'contract')], timeout=900, cwd=layout['code_root'])
    semantics_file = out / 'contract' / 'model-contract-runtime.json'
    ledger.add('canonical-contract',
               'VERIFIED' if semantics['exit_code'] == 0 else 'FAILED',
               exit_code=semantics['exit_code'], stderr_tail=semantics['stderr_tail'],
               evidence=str(semantics_file) if semantics_file.is_file() else None)
    if semantics['exit_code'] != 0:
        ledger.add('gate', 'ABORT', reason='canonical contract failed; no engine is built from an '
                                           'unverified contract')
        package_evidence(out, arguments, ledger)
        return PHASE_FAILED

    # ---- Phase 4: baseline telemetry (idle, before any inference) ----------
    telemetry = run([python, '-m', 'scripts.collect_jetson_telemetry', '--seconds', '10',
                     '--out', str(out / 'telemetry-baseline')], timeout=300, cwd=layout['code_root'])
    ledger.add('telemetry-baseline', 'VERIFIED' if telemetry['exit_code'] == 0 else 'FAILED',
               exit_code=telemetry['exit_code'], stderr_tail=telemetry['stderr_tail'])

    # ---- Phase 5: TensorRT FP32 (+ FP16) built on this device --------------
    fp16_onnx, fp16_sha = ensure_mixed_fp16(layout, out, ledger, layout['code_root'], python)
    trt_command = [python, '-m', 'scripts.verify_jetson_tensorrt',
                   '--onnx', str(layout['onnx']), '--samples-dir', str(layout['samples'] or 'samples'),
                   '--frames', arguments.frames, '--precisions', arguments.precisions,
                   '--warmup', str(arguments.warmup), '--iterations', str(arguments.iterations),
                   '--out', str(out / 'tensorrt'), '--require-jetson']
    if layout['contract']:
        trt_command += ['--contract-record', str(layout['contract'])]
    if fp16_onnx:
        trt_command += ['--fp16-onnx', str(fp16_onnx)]
    if layout['video']:
        trt_command += ['--video', str(layout['video'])]
    tensorrt = run(trt_command, timeout=5400, cwd=layout['code_root'])
    tensorrt_ok = tensorrt['exit_code'] == 0
    ledger.add('tensorrt', 'VERIFIED' if tensorrt_ok else 'FAILED',
               exit_code=tensorrt['exit_code'], stderr_tail=tensorrt['stderr_tail'],
               stdout_tail=tensorrt['stdout_tail'][:1200],
               evidence=str(out / 'tensorrt' / 'tensorrt-qualification.json'))

    # ---- Phase 6: real video pipeline -------------------------------------
    if arguments.skip_video:
        ledger.add('video-pipeline', 'NOT REQUESTED', note='--skip-video')
    else:
        video_command = [python, '-m', 'scripts.verify_jetson_video_pipeline',
                         '--onnx', str(layout['onnx']), '--runtime', arguments.runtime,
                         '--precision', 'fp32', '--backend', arguments.backend,
                         '--seconds', str(arguments.video_seconds), '--inference-fps',
                         str(arguments.inference_fps), '--out', str(out)]
        if layout['video']:
            video_command += ['--video', str(layout['video'])]
        if arguments.rtsp_url:
            video_command += ['--rtsp-url', arguments.rtsp_url]
        if arguments.test_rtsp_reconnect:
            video_command += ['--simulate-outage']
        video = run(video_command, timeout=int(arguments.video_seconds) + 1800,
                    cwd=layout['code_root'])
        ledger.add('video-pipeline', 'VERIFIED' if video['exit_code'] == 0 else 'FAILED',
                   exit_code=video['exit_code'], stderr_tail=video['stderr_tail'],
                   evidence=str(out / 'video' / 'video-pipeline.json'))

    # ---- Phase 7: DeepStream availability (never auto-installed) ----------
    deepstream_status, deepstream_file = deepstream_phase(out)
    ledger.add('deepstream', deepstream_status, evidence=str(deepstream_file))

    # ---- Phase 8: sustained application inference -------------------------
    sustained_phase(arguments, layout, out, ledger, python, layout['code_root'], arguments.rtsp_url)

    # ---- Phase 9: status patch + evidence package -------------------------
    evidence_map = {'jetson_environment': out / 'jetson_environment.json',
                    'jetson_telemetry': out / 'sustained' / 'jetson_telemetry.json',
                    'tegrastats': out / 'sustained' / 'tegrastats.log',
                    'tensorrt': out / 'tensorrt' / 'tensorrt-qualification.json',
                    'video': out / 'video' / 'video-pipeline.json'}
    patch = build_status_patch(out, environment, identity, ledger.phases, evidence_map)
    ledger.add('status-patch', 'GENERATED', evidence=str(out / 'status-patch.json'),
               authorised=len(patch['authorised_changes']))
    package = package_evidence(out, arguments, ledger)

    failed = [record['phase'] for record in ledger.phases if record['status'] == 'FAILED']
    print(json.dumps({'status': 'QUALIFICATION_COMPLETE' if not failed else 'QUALIFICATION_PARTIAL',
                      'physical_jetson_confirmed': state == CONFIRMED,
                      'failed_phases': failed,
                      'authorised_claims': [entry['claim'] for entry in patch['authorised_changes']],
                      'evidence_zip': package, 'truth': {
                          'tensorrt_on_jetson': 'VERIFIED' if tensorrt_ok else 'NOT VERIFIED',
                          'deepstream': deepstream_status,
                          'nvidia_hardware_decode': 'see video/hardware-decode.json',
                          'physical_jetson': state}},
                     indent=2, default=str))
    return PHASE_FAILED if failed or not tensorrt_ok else COMPLETE


if __name__ == '__main__':
    sys.exit(main())
