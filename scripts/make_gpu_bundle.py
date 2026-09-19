"""Package the real PPE artifact into one uploadable file for a free GPU runtime.

`var/` is gitignored on purpose: the qualified 12 MB ONNX artifact is not in the
repository, so a Colab/Kaggle session cannot get it from `git clone`. This script
produces a single self-contained archive containing the exact artifact, the
contract record, real evaluation frames cut from the local evaluation video
(`var/media/ppe-2.mp4`, which is gitignored - the reason those frames have to
travel inside the bundle at all), and the verification harness itself, plus a
manifest of every SHA-256.

    python -m scripts.make_gpu_bundle \
        --onnx var/model/hansung-p3.onnx \
        --contract-record var/model/hansung-p3.json \
        --video var/media/ppe-2.mp4 --frames 0,25,50,100,150,200

Then upload `var/bundle/visionops-gpu-bundle.zip` into the notebook described in
docs/evidence/tensorrt/README.md. Nothing here fabricates a result: the bundle
only carries inputs. The archive lives under gitignored `var/`; the small
manifest is written to `docs/evidence/tensorrt/` so the shipped inputs stay
auditable in Git.
"""
import argparse
import hashlib
import io
import json
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

EXCLUDED_PARTS = {'.git', '.venv', 'var', 'node_modules', '__pycache__', '.mypy_cache', '.pytest_cache'}


def sha256_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


def tracked_files():
    """Tracked paths, plus new-but-unignored paths.

    `--others --exclude-standard` is not optional here. The verification harness and
    the runtime it exercises are frequently newer than the last commit, and listing
    only `--cached` produced a bundle that silently omitted exactly those files. New
    files are included; ignored ones (`var/`, `.venv/`) stay out.
    """
    result = subprocess.run(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
                            cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0 or not result.stdout:
        return sorted(str(path.relative_to(ROOT)).replace('\\', '/')
                      for path in ROOT.rglob('*')
                      if path.is_file() and not EXCLUDED_PARTS & set(path.parts))
    return sorted({name for name in result.stdout.split('\0') if name})


def git_commit():
    try:
        commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True,
                                text=True, timeout=20).stdout.strip()
        dirty = subprocess.run(['git', 'status', '--porcelain'], cwd=ROOT, capture_output=True,
                               text=True, timeout=20).stdout.strip()
        return commit or None, 'dirty' if dirty else 'clean'
    except Exception:  # noqa: BLE001
        return None, 'unknown'


def extract_frames(video, index_list):
    import cv2
    capture = cv2.VideoCapture(str(video))
    available = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    frames = []
    for index in index_list:
        if index >= available:
            continue
        capture.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = capture.read()
        if not ok:
            continue
        ok, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
        if not ok:
            continue
        frames.append((f'samples/frame-{index:04d}.jpg', buffer.tobytes(), list(frame.shape)))
    capture.release()
    return frames, available


def main(argv=None):
    parser = argparse.ArgumentParser(description='Build the uploadable GPU verification bundle.')
    parser.add_argument('--onnx', default='var/model/hansung-p3.onnx')
    parser.add_argument('--contract-record', default='var/model/hansung-p3.json')
    parser.add_argument('--video', default='var/media/ppe-2.mp4')
    parser.add_argument('--frames', default='0,25,50,100,150,200')
    parser.add_argument('--out', default='var/bundle')
    parser.add_argument('--manifest', default='docs/evidence/tensorrt/bundle-manifest.json')
    parser.add_argument('--code-only', action='store_true',
                       help='omit the ONNX artifact (use when cloning the public repo instead)')
    arguments = parser.parse_args(argv)

    onnx = Path(arguments.onnx)
    contract_record = Path(arguments.contract_record)
    video = Path(arguments.video)
    for required in (onnx, contract_record):
        if not required.is_file():
            raise SystemExit(f'missing required input: {required}')
    if not video.is_file():
        raise SystemExit(f'missing evaluation video: {video}')

    commit, state = git_commit()
    frames, available = extract_frames(video, [int(value) for value in arguments.frames.split(',') if value.strip()])

    entries = []  # (archive_name, bytes, role)
    for name in tracked_files():
        path = ROOT / name
        if path.is_file():
            entries.append((f'code/{name}', path.read_bytes(), 'repository'))
    if not arguments.code_only:
        entries.append(('model/' + onnx.name, onnx.read_bytes(), 'model'))
        entries.append(('model/' + contract_record.name, contract_record.read_bytes(), 'contract_record'))
    for name, payload, _shape in frames:
        entries.append((name, payload, 'evaluation_sample'))

    manifest = {
        'created_at': datetime.now(timezone.utc).isoformat(),
        'git_commit': commit, 'working_tree': state,
        'onnx': {'path': str(onnx).replace('\\', '/'), 'bytes': onnx.stat().st_size,
                 'sha256': sha256_bytes(onnx.read_bytes()),
                 'archive_path': None if arguments.code_only else f'model/{onnx.name}'},
        'contract_record': {'path': str(contract_record).replace('\\', '/'),
                            'sha256': sha256_bytes(contract_record.read_bytes()),
                            'archive_path': None if arguments.code_only else f'model/{contract_record.name}'},
        'evaluation_samples': {'video': str(video).replace('\\', '/'),
                               'video_frames_available': available,
                               'indices': [int(value) for value in arguments.frames.split(',') if value.strip()],
                               'files': [{'archive_path': name, 'sha256': sha256_bytes(payload),
                                          'shape': shape} for name, payload, shape in frames]},
        # Only the inputs that cannot be recovered from the commit are listed: the
        # repository code is reproducible from git_commit, so hashing 136 source
        # files here would churn on every edit without adding provenance.
        'contents': [{'archive_path': name, 'bytes': len(payload), 'role': role,
                      'sha256': sha256_bytes(payload)} for name, payload, role in entries
                     if role != 'repository'],
        'code': {'file_count': sum(1 for entry in entries if entry[2] == 'repository'),
                 'archive_prefix': 'code/', 'reproducible_from': {'git_commit': commit,
                                                                  'working_tree': state},
                 'includes_uncommitted_files': state != 'clean',
                 'verification_entry_point': 'code/scripts/verify_tensorrt_gpu.py',
                 'requirements': 'code/requirements-gpu.txt',
                 'notebook': 'code/docs/evidence/tensorrt/colab_gpu_verify.ipynb'},
        # A zip cannot contain its own SHA-256, so this stays null inside the archive
        # and is filled in for the on-disk copy below. It is present at all so a reader
        # knows the field was considered rather than forgotten.
        'archive': None,
        'purpose': ('inputs for docs/evidence/tensorrt/colab_gpu_verify.ipynb; it contains no results, '
                    'no engine file and no metric'),
        'note': ('the archive is written under gitignored var/ so 12 MB of model weights are not committed; '
                 'the public repository supplies code/ via git clone as well; the in-archive copy of this '
                 'manifest leaves `archive` null because a zip cannot carry its own digest, and the on-disk '
                 'copy at docs/evidence/tensorrt/bundle-manifest.json records it'),
    }

    out = Path(arguments.out)
    out.mkdir(parents=True, exist_ok=True)
    archive = out / ('visionops-gpu-bundle.zip' if not arguments.code_only else 'visionops-code-bundle.zip')
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as handle:
        for name, payload, _role in entries:
            handle.writestr(name, payload)
        handle.writestr('MANIFEST.json', json.dumps(manifest, indent=2))
    manifest['archive'] = {'path': str(archive).replace('\\', '/'), 'bytes': archive.stat().st_size,
                           'sha256': sha256_bytes(archive.read_bytes()),
                           'file_count': len(entries)}
    Path(arguments.manifest).parent.mkdir(parents=True, exist_ok=True)
    Path(arguments.manifest).write_text(json.dumps(manifest, indent=2))
    print(json.dumps({'archive': manifest['archive'], 'onnx_sha256': manifest['onnx']['sha256'],
                      'evaluation_samples': len(frames), 'code_files': sum(
                          1 for entry in entries if entry[2] == 'repository'),
                      'manifest': arguments.manifest}, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
