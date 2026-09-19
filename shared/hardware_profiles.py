"""Explicit hardware profiles and the compatibility matrix that gates releases.

The edge agent previously accepted exactly one literal profile and one literal
machine string. That is replaced by a declared matrix so x86_64 CPU, ARM64 CPU
and NVIDIA Jetson/TensorRT targets are all representable, and so an incompatible
release is rejected for a stated reason instead of an opaque string comparison.

A simulated target is allowed but must set `simulated_hardware: true` and must
never report GPU/TOPS numbers, because none were measured.
"""
import os
import platform

ARCHITECTURE_ALIASES = {'x86_64': 'x86_64', 'amd64': 'x86_64', 'AMD64': 'x86_64',
                        'aarch64': 'aarch64', 'arm64': 'aarch64', 'ARM64': 'aarch64'}

PROFILES = {
    'cpu_onnx_x86_64': {
        'description': 'Local/x86_64 CPU worker using ONNX Runtime',
        'architectures': ['x86_64'], 'runtimes': ['onnxruntime'],
        'accelerators': ['cpu'], 'model_formats': ['onnx'],
        'requires': [], 'device_class': 'cpu_only'},
    'cpu_onnx_arm64': {
        'description': 'ARM64 CPU worker (Jetson without TensorRT, or ARM server)',
        'architectures': ['aarch64'], 'runtimes': ['onnxruntime'],
        'accelerators': ['cpu'], 'model_formats': ['onnx'],
        'requires': [], 'device_class': 'cpu_only'},
    'nvidia_jetson_tensorrt_arm64': {
        'description': 'Jetson-class ARM64 device using TensorRT engines',
        'architectures': ['aarch64'], 'runtimes': ['tensorrt'],
        'accelerators': ['cuda', 'tensorrt'], 'model_formats': ['onnx', 'tensorrt'],
        'requires': ['jetpack_version', 'cuda_version', 'tensorrt_version'],
        'device_class': 'nvidia_edge'},
    'nvidia_jetson_orin_arm64': {
        # Same contract as the generic Jetson profile, but named for the Orin family
        # that the physical qualification run targets. It exists so a real run reports
        # the device it actually found instead of a generic label.
        'description': 'NVIDIA Jetson Orin (AGX / NX / Nano) ARM64 device using TensorRT engines',
        'architectures': ['aarch64'], 'runtimes': ['tensorrt'],
        'accelerators': ['cuda', 'tensorrt'], 'model_formats': ['onnx', 'tensorrt'],
        'requires': ['jetpack_version', 'cuda_version', 'tensorrt_version'],
        'device_class': 'nvidia_edge'},
}

SIMULATABLE = ('cpu_onnx_arm64', 'nvidia_jetson_tensorrt_arm64', 'nvidia_jetson_orin_arm64')

# Physical-Jetson markers, checked in order of strength. A declaration in the
# environment is deliberately NOT one of them: only the device itself can say.
JETSON_STRONG_MARKERS = ('/etc/nv_tegra_release', '/proc/device-tree/compatible')


def normalise_architecture(machine):
    return ARCHITECTURE_ALIASES.get(str(machine), str(machine).lower())


def resolve(name):
    if name not in PROFILES:
        raise ValueError('unknown_hardware_profile')
    return {**PROFILES[name], 'profile': name}


def known(name):
    return name in PROFILES


def profile_names():
    return sorted(PROFILES)


def requirements(name):
    return list(resolve(name)['requires'])


GPU_VERSION_FIELDS = ('jetpack_version', 'cuda_version', 'tensorrt_version')
DECLARED_PREFIX = 'VISIONOPS_DECLARED_'


def declared_target(environment=None):
    """Operator-declared target versions, only for an explicitly simulated target.

    These are a *deployment contract*, not a measurement. `detect_local_environment`
    records them separately and labels them so no report can present an intended
    Jetson/CUDA/TensorRT version as if it had been observed.
    """
    source = os.environ if environment is None else environment
    declared = {field: source[DECLARED_PREFIX + field.upper()]
                for field in GPU_VERSION_FIELDS if source.get(DECLARED_PREFIX + field.upper())}
    declared_architecture = source.get(DECLARED_PREFIX + 'ARCHITECTURE')
    if declared_architecture:
        declared['architecture'] = normalise_architecture(declared_architecture)
    return declared


def detect_local_environment(capabilities=None, declared=None):
    """Truthful description of *this* host. Unmeasured fields stay absent."""
    from edge.runtimes import CPU, CUDA, TENSORRT, detect_capabilities
    capabilities = capabilities or detect_capabilities()
    declared = declared_target() if declared is None else declared
    environment = {'architecture': declared.get('architecture', normalise_architecture(platform.machine())),
                   'machine': platform.machine(),
                   'os': f'{platform.system()} {platform.release()}',
                   'simulated_hardware': bool(declared),
                   'declared_architecture': 'architecture' in declared,
                   'declared_versions': sorted(field for field in declared if field != 'architecture'),
                   'measured_versions': []}
    try:
        import onnxruntime
        environment['runtime_version'] = onnxruntime.__version__
    except Exception:  # noqa: BLE001
        environment['runtime_version'] = None
    if capabilities.get(TENSORRT, {}).get('available'):
        environment['runtime'] = 'tensorrt'
    elif capabilities.get(CUDA, {}).get('available'):
        environment['runtime'] = 'onnxruntime'
        environment['accelerator'] = 'cuda'
    elif capabilities.get(CPU, {}).get('available'):
        environment['runtime'] = 'onnxruntime'
    else:
        environment['runtime'] = None
    measured = {field: capabilities.get(TENSORRT, {}).get(field) for field in GPU_VERSION_FIELDS}
    measured = {field: value for field, value in measured.items() if value}
    if environment['simulated_hardware'] and measured:
        # A simulated target must never also claim measurements it cannot have.
        raise ValueError('simulated_target_cannot_report_measured_gpu_versions')
    for field in GPU_VERSION_FIELDS:
        if measured.get(field):
            environment[field] = measured[field]
            environment['measured_versions'].append(field)
        elif declared.get(field):
            environment[field] = declared[field]
    environment['capabilities'] = {name: capabilities.get(name, {}).get('available')
                                   for name in (CPU, CUDA, TENSORRT)}
    return environment


def evaluate(profile_name, environment):
    """Decide compatibility, always with reason codes and explicit notices.

    A simulated target may run a profile whose runtime is not installed locally,
    but that relaxation is recorded as a notice so no report can present it as a
    qualified runtime.
    """
    profile = resolve(profile_name)
    reasons, notices = [], []
    if environment.get('architecture') not in profile['architectures']:
        reasons.append('architecture_mismatch')
    runtime = environment.get('runtime')
    if runtime not in profile['runtimes']:
        if environment.get('simulated_hardware'):
            notices.append('runtime_not_present_on_simulation_host')
        else:
            reasons.append('runtime_mismatch')
    if environment.get('simulated_hardware') and profile_name not in SIMULATABLE:
        reasons.append('profile_not_simulatable')
    declared = environment.get('declared_versions') or []
    for requirement in profile['requires']:
        if not environment.get(requirement):
            reasons.append(f'missing_{requirement}')
        elif requirement in declared:
            notices.append(f'declared_not_measured_{requirement}')
    return {'profile': profile_name, 'compatible': not reasons, 'reasons': reasons,
            'notices': notices, 'simulated_hardware': bool(environment.get('simulated_hardware'))}


def compatible(profile_name, environment):
    """Return `(ok, reason_codes)`. Every rejection carries a reason code."""
    result = evaluate(profile_name, environment)
    return result['compatible'], result['reasons']


def matrix():
    """Machine-readable matrix for docs, API responses and verification output."""
    return {name: {**profile, 'profile': name, 'simulatable': name in SIMULATABLE}
            for name, profile in sorted(PROFILES.items())}
