"""Identify whether this host is a real NVIDIA Jetson, and describe it truthfully.

Writes `jetson_environment.json`. The result is one of

    PHYSICAL_JETSON_CONFIRMED      real Tegra/Jetson evidence, on aarch64
    PARTIAL_JETSON_ENVIRONMENT     some evidence, but not enough to confirm
    JETSON_NOT_DETECTED            no Jetson evidence at all

Detection never trusts a declaration. `VISIONOPS_DECLARED_*` (see
`shared/hardware_profiles.py`) describes an *intended* target and is what the
simulated-target path uses; it can never make this script report a physical
Jetson. Evidence has to come from the machine: `/etc/nv_tegra_release`, the Tegra
device tree, `tegrastats`, JetPack packages, and aarch64.

`nvidia-smi` is deliberately not required. Many Jetson images do not expose it the
way a datacentre driver does, so a missing `nvidia-smi` says nothing here and is
recorded as an observation rather than a failure.

Power and clock state are read-only by default. A remote provider may impose
power or thermal caps, so `nvpmodel`/`jetson_clocks` are only ever *changed* with
`--enable-max-performance`, which additionally requires working non-interactive
`sudo` and records exactly what it ran.

Exit codes
    0  PHYSICAL_JETSON_CONFIRMED
    3  JETSON_NOT_DETECTED  (also used by callers as BLOCKED_NOT_PHYSICAL_JETSON)
    5  PARTIAL_JETSON_ENVIRONMENT

    python3 -m scripts.detect_jetson_environment --out evidence/jetson
"""
import argparse
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CONFIRMED = 'PHYSICAL_JETSON_CONFIRMED'
PARTIAL = 'PARTIAL_JETSON_ENVIRONMENT'
NOT_DETECTED = 'JETSON_NOT_DETECTED'

NOT_DETECTED_EXIT = 3
PARTIAL_EXIT = 5

TEGRA_RELEASE = Path('/etc/nv_tegra_release')
BOOT_CONTROL = Path('/etc/nv_boot_control.conf')
OS_RELEASE = Path('/etc/os-release')
DEVICE_TREE_MODEL = Path('/proc/device-tree/model')
DEVICE_TREE_COMPATIBLE = Path('/proc/device-tree/compatible')
JETPACK_ROOT = Path('/opt/nvidia/jetson')          # present on JetPack 5/6 images
TEGRASTATS = ('tegrastats',)
JETSON_TOOLS = ('tegrastats', 'nvpmodel', 'jetson_clocks')
MEDIA_TOOLS = ('gst-launch-1.0', 'gst-inspect-1.0', 'ffmpeg')
DEEPSTREAM_TOOLS = ('deepstream-app', 'deepstream-config.yml')

# `Jetson Orin NX` etc. live in the device tree; the family word is what varies.
ORIN = re.compile(r'\borin\b', re.IGNORECASE)
XAVIER = re.compile(r'\bxavier\b', re.IGNORECASE)
NANO = re.compile(r'\bnano\b', re.IGNORECASE)
TEGRA = re.compile(r'tegra', re.IGNORECASE)


def utc():
    return datetime.now(timezone.utc).isoformat()


def is_root():
    """`os.geteuid` is POSIX-only; this script also has to run on the detector host."""
    geteuid = getattr(os, 'geteuid', None)
    return bool(geteuid()) if geteuid is not None else False


def read_text(path, limit=4000):
    """Read a file if it exists. Returns None rather than an empty string."""
    try:
        return Path(path).read_text(errors='replace').strip()[:limit] or None
    except Exception:  # noqa: BLE001 - absence is a finding, not a crash
        return None


def run(command, timeout=25):
    """Run a command and record everything, including how it failed."""
    executable = shutil.which(command[0])
    record = {'command': ' '.join(command), 'executable': executable,
              'returncode': None, 'stdout': None, 'stderr': None, 'error': None}
    if not executable:
        record['error'] = 'not_on_path'
        return record
    try:
        result = subprocess.run([executable, *command[1:]], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        record['error'] = 'timeout'
        return record
    except Exception as exc:  # noqa: BLE001
        record['error'] = type(exc).__name__
        return record
    record.update(returncode=result.returncode,
                  stdout=(result.stdout or '').strip()[:4000] or None,
                  stderr=(result.stderr or '').strip()[:2000] or None)
    return record


def first_line(text):
    if not text:
        return None
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return None


def parse_os_release(text):
    fields = {}
    for line in (text or '').splitlines():
        if '=' not in line:
            continue
        key, _, value = line.partition('=')
        fields[key.strip()] = value.strip().strip('"')
    return fields


def l4t_version(text):
    """`# R35 (release), REVISION: 4.1` -> `R35.4.1`."""
    if not text:
        return None
    release = re.search(r'#\s*(R\d+)', text)
    revision = re.search(r'REVISION:\s*([0-9.]+)', text)
    if release and revision:
        return f'{release.group(1)}.{revision.group(1)}'
    return release.group(1) if release else None


def jetpack_versions():
    """JetPack / L4T / CUDA / cuDNN / TensorRT versions from packages and modules.

    Each value is measured on this host or absent. Nothing is inferred from a
    documentation table.
    """
    versions = {'jetpack': None, 'l4t': None, 'cuda': None, 'cudnn': None, 'tensorrt': None,
                'cuda_evidence': None, 'sources': {}}
    tegra_text = read_text(TEGRA_RELEASE)
    versions['l4t'] = l4t_version(tegra_text)
    if versions['l4t']:
        versions['sources']['l4t'] = str(TEGRA_RELEASE)

    dpkg = run(['dpkg-query', '-W', '-f=${Package} ${Version}', 'nvidia-jetpack'])
    if dpkg['returncode'] == 0 and dpkg['stdout']:
        versions['jetpack'] = dpkg['stdout'].split()[-1]
        versions['sources']['jetpack'] = 'dpkg-query nvidia-jetpack'
    else:
        # No version is invented from a missing package: record what was actually seen.
        if JETPACK_ROOT.exists():
            versions['sources']['jetpack'] = f'{JETPACK_ROOT} present (no nvidia-jetpack package)'
        elif Path('/etc/nv_tegra_release').exists():
            versions['sources']['jetpack'] = ('/etc/nv_tegra_release present; nvidia-jetpack package not '
                                              'installed, so the JetPack version is unknown')

    nvcc = run(['nvcc', '--version'])
    if nvcc['stdout']:
        match = re.search(r'release\s+([0-9.]+)', nvcc['stdout'])
        if match:
            versions['cuda'] = match.group(1)
            versions['cuda_evidence'] = 'nvcc --version'
    if not versions['cuda']:
        cuda_dirs = sorted(p.name for p in Path('/usr/local').glob('cuda-*')) if Path('/usr/local').exists() else []
        if cuda_dirs:
            versions['cuda'] = cuda_dirs[-1].replace('cuda-', '')
            versions['cuda_evidence'] = '/usr/local/' + cuda_dirs[-1]

    trt = subprocess_result([sys.executable, '-c', 'import tensorrt as trt; print(trt.__version__)'])
    if trt:
        versions['tensorrt'] = trt
        versions['sources']['tensorrt'] = 'python -c "import tensorrt"'
    if not versions['tensorrt']:
        for candidate in (Path('/usr/lib/python3/dist-packages/tensorrt'), Path('/usr/src/tensorrt')):
            if candidate.exists():
                versions['sources']['tensorrt'] = f'{candidate} present (version not readable)'
                break

    cudnn = subprocess_result([sys.executable, '-c',
                               'import torch; print(torch.backends.cudnn.version())'])
    if cudnn and cudnn.isdigit():
        major, remainder = divmod(int(cudnn), 10000)
        minor, patch = divmod(remainder, 100)
        versions['cudnn'] = f'{major}.{minor}.{patch}'
        versions['sources']['cudnn'] = 'torch.backends.cudnn.version()'
    if not versions['cudnn']:
        header = read_text('/usr/include/cudnn_version.h')
        match = re.search(r'CUDNN_MAJOR\s+(\d+).*?CUDNN_MINOR\s+(\d+).*?CUDNN_PATCHLEVEL\s+(\d+)',
                          header or '', re.DOTALL)
        if match:
            versions['cudnn'] = '.'.join(match.groups())
            versions['sources']['cudnn'] = 'cudnn_version.h'
    return versions


def subprocess_result(command, timeout=30):
    """Last non-empty stdout line, or None. Used for one-line version probes."""
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except Exception:  # noqa: BLE001
        return None
    if result.returncode != 0:
        return None
    for line in reversed((result.stdout or '').strip().splitlines()):
        if line.strip():
            return line.strip()
    return None


def module_version(name):
    return subprocess_result([sys.executable, '-c', f'import {name}; print({name}.__version__)'])


def tee_evidence():
    """Raw Jetson markers. Absence is recorded as None, never as an empty match."""
    return {
        'nv_tegra_release': {'path': str(TEGRA_RELEASE), 'present': TEGRA_RELEASE.exists(),
                             'content': read_text(TEGRA_RELEASE)},
        'nv_boot_control_conf': {'path': str(BOOT_CONTROL), 'present': BOOT_CONTROL.exists(),
                                 'content': read_text(BOOT_CONTROL, 1500)},
        'device_tree_model': read_text(DEVICE_TREE_MODEL, 200),
        'device_tree_compatible': read_text(DEVICE_TREE_COMPATIBLE, 400),
        'jetson_root': {'path': str(JETPACK_ROOT), 'present': JETPACK_ROOT.exists()},
        'tegra_proc': sorted(p.name for p in Path('/proc').glob('tegra*')) if Path('/proc').exists() else [],
    }


def jetson_family(device_tree_model, compatible, tegrastats_present):
    """Best available family label, from the device itself. Never guessed."""
    haystack = ' '.join(filter(None, (device_tree_model, compatible)))
    if ORIN.search(haystack):
        return 'orin'
    if XAVIER.search(haystack):
        return 'xavier'
    if TEGRA.search(haystack):
        return 'tegra_generic'
    if tegrastats_present:
        return 'jetson_unidentified'
    return None


def classify(markers, machine, tools):
    """Decide the physical-Jetson state from evidence alone.

    `PHYSICAL_JETSON_CONFIRMED` needs a real marker *and* aarch64. A declaration in
    the environment is not a marker, so it cannot reach this branch.
    """
    strong = []
    if markers['nv_tegra_release']['present']:
        strong.append('/etc/nv_tegra_release')
    if TEGRA.search(markers['device_tree_compatible'] or ''):
        strong.append('/proc/device-tree/compatible(tegra)')
    if TEGRA.search(markers['device_tree_model'] or ''):
        strong.append('/proc/device-tree/model(tegra)')
    weak = [name for name in ('tegrastats', 'nvpmodel', 'jetson_clocks') if tools.get(name)]
    if markers['jetson_root']['present']:
        weak.append(str(JETPACK_ROOT))

    aarch64 = machine in ('aarch64', 'arm64', 'ARM64')
    if strong and aarch64:
        return CONFIRMED, {'strong_markers': strong, 'weak_markers': weak,
                           'reason': None}
    if not strong and not weak:
        return NOT_DETECTED, {'strong_markers': [], 'weak_markers': [],
                              'reason': 'no_tegra_or_jetpack_evidence_on_this_host'}
    if strong and not aarch64:
        return PARTIAL, {'strong_markers': strong, 'weak_markers': weak,
                         'reason': f'jetson_evidence_on_a_non_aarch64_machine ({machine})'}
    return PARTIAL, {'strong_markers': strong, 'weak_markers': weak,
                     'reason': 'aarch64_or_jetson_tools_without_a_tegra_release_marker'}


def power_and_clocks(tools, enable_max_performance=False):
    """Read power mode and clock state. Change them only on explicit request.

    A remote provider may cap power or thermals, so the default is read-only and
    the switch path needs both the flag and non-interactive sudo.
    """
    record = {'mode': 'read_only', 'requested_max_performance': bool(enable_max_performance),
              'nvpmodel_query': None, 'jetson_clocks_show': None, 'changes': [],
              'skipped_reason': None}
    if tools.get('nvpmodel'):
        record['nvpmodel_query'] = run(['nvpmodel', '-q'])['stdout']
    if tools.get('jetson_clocks'):
        record['jetson_clocks_show'] = run(['jetson_clocks', '--show'])['stdout']
    if not enable_max_performance:
        record['skipped_reason'] = ('read-only by default: a provider may impose power/thermal limits and '
                                    'changing the mode alters the measurements')
        return record
    if not is_root() and not shutil.which('sudo'):
        record['skipped_reason'] = 'sudo_unavailable'
        return record
    privilege = [] if is_root() else ['sudo', '-n']
    for tool, tool_arguments in (('nvpmodel', ['-m', '0']), ('jetson_clocks', [])):
        if not shutil.which(tool):
            record['changes'].append({'command': f'{tool} {" ".join(tool_arguments)}'.strip(),
                                      'status': 'tool_missing'})
            continue
        command = [*privilege, tool, *tool_arguments]
        outcome = run(command)
        record['changes'].append({'command': ' '.join(command), 'returncode': outcome['returncode'],
                                  'stdout': outcome['stdout'], 'stderr': outcome['stderr'],
                                  'error': outcome['error'],
                                  'status': 'applied' if outcome['returncode'] == 0 else 'failed'})
    record['mode'] = ('max_performance_applied'
                      if any(item['status'] == 'applied' for item in record['changes'])
                      else 'max_performance_request_failed')
    record['provider_warning'] = ('the requested power mode changed after this point, so measurements taken '
                                  'here are not comparable with a default-mode run')
    return record


def detect(enable_max_performance=False):
    """Full physical-Jetson environment record. No value here is invented."""
    markers = tee_evidence()
    tools = {name: shutil.which(name) for name in JETSON_TOOLS}
    media_tools = {name: shutil.which(name) for name in MEDIA_TOOLS + DEEPSTREAM_TOOLS}
    machine = platform.machine()
    state, verdict = classify(markers, machine, tools)
    versions = jetpack_versions()
    family = jetson_family(markers['device_tree_model'], markers['device_tree_compatible'],
                           bool(tools.get('tegrastats')))

    declared = {key: value for key, value in os.environ.items()
                if key.startswith('VISIONOPS_DECLARED_')}
    record = {
        'captured_at': utc(),
        'state': state,
        'physical_jetson': state == CONFIRMED,
        'verdict': verdict,
        'declared_environment_ignored': {
            'keys': sorted(declared),
            'why': ('VISIONOPS_DECLARED_* describes an intended target for the simulated path; it is '
                    'recorded here only to prove it did not influence this result')},
        'host': {'hostname': socket.gethostname(), 'architecture': machine,
                 'aarch64': machine in ('aarch64', 'arm64', 'ARM64'),
                 'os': f'{platform.system()} {platform.release()}', 'kernel': platform.release(),
                 'python': platform.python_version(), 'platform': platform.platform()},
        'os_release': parse_os_release(read_text(OS_RELEASE)),
        'jetson': {'family': family,
                   'model': markers['device_tree_model'],
                   'compatible': markers['device_tree_compatible']},
        'versions': versions,
        'tools': {'jetson': tools, 'media': media_tools,
                  'nvidia_smi': shutil.which('nvidia-smi'),
                  'nvidia_smi_note': ('not required on Jetson: several images do not expose it the way a '
                                      'datacentre driver does, so its absence is recorded, not treated as a failure')},
        'power_and_clocks': power_and_clocks(tools, enable_max_performance),
        'software': {'torch': module_version('torch'), 'onnxruntime': module_version('onnxruntime'),
                     'numpy': module_version('numpy'), 'cv2': module_version('cv2'),
                     'gi': module_version('gi')},
        'raw_evidence': markers,
        'not_measured': ['TOPS', 'GPU core count', 'memory bandwidth', 'module part number',
                         'power-mode wattage'],
        'not_measured_note': ('none of these is asserted anywhere in this repository unless the device '
                              'itself reported it; they are listed so their absence is explicit'),
    }
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description='Detect a physical NVIDIA Jetson and describe it.')
    parser.add_argument('--out', default='docs/evidence/jetson')
    parser.add_argument('--enable-max-performance', action='store_true',
                       help='attempt nvpmodel/jetson_clocks changes (requires sudo; default is read-only)')
    parser.add_argument('--print', action='store_true', help='print the full record')
    arguments = parser.parse_args(argv)

    record = detect(enable_max_performance=arguments.enable_max_performance)
    out = Path(arguments.out)
    out.mkdir(parents=True, exist_ok=True)
    target = out / 'jetson_environment.json'
    target.write_text(json.dumps(record, indent=2, default=str))

    summary = {'state': record['state'], 'physical_jetson': record['physical_jetson'],
               'architecture': record['host']['architecture'],
               'jetson_family': record['jetson']['family'],
               'l4t': record['versions']['l4t'], 'jetpack': record['versions']['jetpack'],
               'cuda': record['versions']['cuda'], 'tensorrt': record['versions']['tensorrt'],
               'tegrastats': record['tools']['jetson'].get('tegrastats'),
               'gst_launch': record['tools']['media'].get('gst-launch-1.0'),
               'deepstream_app': record['tools']['media'].get('deepstream-app'),
               'reason': record['verdict']['reason'], 'evidence': str(target)}
    print(json.dumps(summary if not arguments.print else record, indent=2, default=str))

    if record['state'] == CONFIRMED:
        return 0
    print(f'BLOCKED_NOT_PHYSICAL_JETSON: {record["state"]}'
          + (f" ({record['verdict']['reason']})" if record['verdict']['reason'] else ''), flush=True)
    return NOT_DETECTED_EXIT if record['state'] == NOT_DETECTED else PARTIAL_EXIT


if __name__ == '__main__':
    sys.exit(main())
