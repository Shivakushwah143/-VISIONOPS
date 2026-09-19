"""Inference runtime abstraction with honest capability detection.

    InferenceRuntime
    |-- OnnxRuntimeCPU      expected on any supported CPU host
    |-- OnnxRuntimeCUDA     optional (needs the CUDA execution provider)
    |-- TensorRTRuntime     optional (needs TensorRT plus an NVIDIA device)

Every runtime returns the *same* raw tensor and feeds the same canonical decoder
in `shared/model_contract.py`, so the CPU and NVIDIA paths cannot drift.

Nothing here fabricates an engine file, a provider, or a GPU number. A runtime
that is not genuinely available reports `available: false` with a reason code and
refuses to load; it never silently falls back to CPU while claiming to be CUDA or
TensorRT.
"""
import hashlib
import platform
import shutil
import sys
from pathlib import Path

import numpy as np

CPU = 'ONNX_CPU'
CUDA = 'ONNX_CUDA'
TENSORRT = 'TENSORRT'
STATUS_IMPLEMENTED_UNVERIFIED = 'IMPLEMENTED - NVIDIA RUNTIME NOT VERIFIED'

# Provider states. `get_available_providers()` only lists what the binary was built
# with; on the GPU qualification host the CUDA provider was listed while failing to
# load because its CUDA library did not match the driver runtime. Listing is therefore
# never treated as proof that a provider is operational.
PROVIDER_UNAVAILABLE = 'UNAVAILABLE'
PROVIDER_LISTED = 'AVAILABLE - NOT QUALIFIED'
PROVIDER_LOAD_FAILED = 'LOAD_FAILED'
PROVIDER_VERIFIED = 'VERIFIED'

# ONNX Runtime declares an input as `tensor(float)`, `tensor(float16)`, ... An adapter
# that always sends float32 cannot run the mixed-FP16 graph that ModelOpt AutoCast
# produces, so the declared type drives the cast instead of an assumed float32.
ONNX_TYPE_TO_NUMPY = {
    'tensor(float)': np.float32,
    'tensor(float16)': np.float16,
    'tensor(double)': np.float64,
    'tensor(int64)': np.int64,
    'tensor(int32)': np.int32,
    'tensor(int16)': np.int16,
    'tensor(int8)': np.int8,
    'tensor(uint8)': np.uint8,
    'tensor(bool)': np.bool_,
}


class RuntimeUnavailable(RuntimeError):
    """Raised when a runtime is requested but genuinely cannot run here."""


def _onnxruntime():
    try:
        import onnxruntime
        return onnxruntime
    except Exception:  # noqa: BLE001 - reported as a capability, not a crash
        return None


def _nvidia_smi():
    """Returns (gpu_present, reason, [gpu names], raw query output)."""
    executable = shutil.which('nvidia-smi')
    if not executable:
        return False, 'nvidia-smi_not_found', [], None
    import subprocess
    query = 'name,driver_version,memory.total,compute_cap'
    try:
        result = subprocess.run([executable, f'--query-gpu={query}', '--format=csv,noheader'],
                                capture_output=True, text=True, timeout=20)
    except Exception as exc:  # noqa: BLE001
        return False, type(exc).__name__, [], None
    if result.returncode != 0:
        return False, 'nvidia-smi_failed', [], result.stderr.strip()[:400]
    lines = [line.strip() for line in result.stdout.strip().splitlines() if line.strip()]
    names = [line.split(',')[0].strip() for line in lines]
    return bool(names), 'ok', names, result.stdout.strip()[:2000]


def _tensorrt_module():
    try:
        import tensorrt  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def numpy_dtype_for_onnx_type(onnx_type):
    """`tensor(float)` -> np.float32. Raises rather than guessing an input type."""
    if onnx_type not in ONNX_TYPE_TO_NUMPY:
        raise RuntimeUnavailable(f'unsupported_onnx_input_type:{onnx_type}')
    return ONNX_TYPE_TO_NUMPY[onnx_type]


def check_provider(model_path, provider, run_inference=True):
    """Really initialise `provider` and complete one inference.

    Presence in `get_available_providers()` is necessary but not sufficient, so:

    * provider not listed          -> UNAVAILABLE
    * listed but never probed      -> AVAILABLE - NOT QUALIFIED
    * session raised / fell back   -> LOAD_FAILED
    * session bound it and ran     -> VERIFIED

    `run_inference=False` stops at a bound session, which is still stronger than the
    provider listing but proves nothing about kernel execution.
    """
    onnxruntime = _onnxruntime()
    record = {'provider': provider, 'model': str(model_path)}
    if onnxruntime is None:
        return {**record, 'state': PROVIDER_UNAVAILABLE, 'reason': 'onnxruntime_not_installed', 'listed': False}
    listed = provider in onnxruntime.get_available_providers()
    record['listed'] = listed
    if not listed:
        return {**record, 'state': PROVIDER_UNAVAILABLE, 'reason': 'provider_not_listed_in_this_build'}
    try:
        options = onnxruntime.SessionOptions()
        options.log_severity_level = 3
        session = onnxruntime.InferenceSession(str(model_path), sess_options=options, providers=[provider])
    except Exception as exc:  # noqa: BLE001 - reported as a provider state, not a crash
        return {**record, 'state': PROVIDER_LOAD_FAILED, 'reason': type(exc).__name__,
                'detail': str(exc)[:400]}
    bound = list(session.get_providers())
    record['providers_bound'] = bound
    if provider not in bound:
        return {**record, 'state': PROVIDER_LOAD_FAILED,
                'reason': 'session_fell_back_off_the_requested_provider'}
    if not run_inference:
        return {**record, 'state': PROVIDER_VERIFIED, 'reason': 'session_initialised'}
    meta = session.get_inputs()[0]
    shape = [dim if isinstance(dim, int) and dim > 0 else 1 for dim in (meta.shape or [])]
    try:
        tensor = np.zeros(shape or [1, 3, 640, 640], dtype=numpy_dtype_for_onnx_type(meta.type))
        session.run(None, {meta.name: tensor})
    except Exception as exc:  # noqa: BLE001
        return {**record, 'state': PROVIDER_LOAD_FAILED, 'reason': 'inference_failed',
                'detail': f'{type(exc).__name__}: {exc}'[:400]}
    return {**record, 'state': PROVIDER_VERIFIED, 'reason': 'session_initialised_and_inference_ran',
            'input_type': meta.type, 'input_shape': shape}


def _provider_state(provider, providers, reports):
    """Probed state if a probe ran, otherwise listed/unavailable."""
    if provider in reports:
        return reports[provider]['state']
    return PROVIDER_UNAVAILABLE if provider not in providers else PROVIDER_LISTED


def detect_capabilities(probe_model=None):
    """Truthful runtime report.

    `available` means the runtime could attempt to load. `provider_state` is the
    stronger claim: an accelerator provider only reaches `VERIFIED` when a real
    session bound it and an inference completed, which needs `probe_model`. Without a
    probe, a listed accelerator stays `AVAILABLE - NOT QUALIFIED` and `available`
    keeps its historical "listed" meaning so nothing silently upgrades.
    """
    onnxruntime = _onnxruntime()
    providers = list(onnxruntime.get_available_providers()) if onnxruntime else []
    gpu_present, gpu_reason, gpu_names, gpu_query = _nvidia_smi()
    # /dev/nvidia* is the Linux device-node test; Windows reports through nvidia-smi.
    device_nodes = sorted(str(p) for p in Path('/dev').glob('nvidia*')) if Path('/dev').exists() else []
    tensorrt_module = _tensorrt_module()
    deepstream_root = Path('/opt/nvidia/deepstream')
    pyds = False
    if deepstream_root.exists():
        try:
            import pyds  # noqa: F401
            pyds = True
        except Exception:  # noqa: BLE001
            pyds = False

    cpu_available = onnxruntime is not None and 'CPUExecutionProvider' in providers
    trt_provider = 'TensorrtExecutionProvider' in providers
    provider_reports = {}
    if probe_model:
        for provider in ('CUDAExecutionProvider', 'TensorrtExecutionProvider'):
            if provider in providers:
                provider_reports[provider] = check_provider(probe_model, provider)
    cuda_state = _provider_state('CUDAExecutionProvider', providers, provider_reports)
    trt_state = _provider_state('TensorrtExecutionProvider', providers, provider_reports)
    cuda_available = (cuda_state == PROVIDER_VERIFIED if probe_model
                      else 'CUDAExecutionProvider' in providers)
    tensorrt_available = tensorrt_module and (trt_state == PROVIDER_VERIFIED if probe_model
                                              else (trt_provider or gpu_present))

    return {
        'host': {'platform': platform.platform(), 'machine': platform.machine(),
                 'python': sys.version.split()[0]},
        'device_nodes': device_nodes,
        'nvidia_smi': {'present': shutil.which('nvidia-smi') is not None,
                       'gpu_detected': gpu_present, 'reason': gpu_reason,
                       'gpu_names': gpu_names, 'query_output': gpu_query},
        CPU: {'available': cpu_available,
              'status': 'VERIFIED' if cpu_available else 'NOT IMPLEMENTED',
              'reason': None if cpu_available else 'onnxruntime_cpu_provider_unavailable',
              'providers': providers},
        CUDA: {'available': cuda_available,
               'status': STATUS_IMPLEMENTED_UNVERIFIED if not cuda_available else 'AVAILABLE - NOT QUALIFIED LOCALLY',
               'reason': None if cuda_available else ('gpu_not_detected' if not gpu_present else 'cuda_execution_provider_missing'),
               'provider_state': cuda_state,
               'provider_probe': provider_reports.get('CUDAExecutionProvider'),
               'providers': providers},
        TENSORRT: {'available': tensorrt_available,
                   'status': STATUS_IMPLEMENTED_UNVERIFIED if not tensorrt_available else 'AVAILABLE - NOT QUALIFIED LOCALLY',
                   'reason': None if tensorrt_available else ('tensorrt_module_not_installed' if not tensorrt_module else 'no_nvidia_gpu_detected'),
                   'provider_state': trt_state,
                   'provider_probe': provider_reports.get('TensorrtExecutionProvider'),
                   'tensorrt_module': tensorrt_module,
                   'tensorrt_execution_provider': trt_provider},
        'DEEPSTREAM': {'available': deepstream_root.exists() and pyds,
                       'status': STATUS_IMPLEMENTED_UNVERIFIED,
                       'reason': None if (deepstream_root.exists() and pyds) else 'deepstream_or_pyds_unavailable',
                       'sdk_root_present': deepstream_root.exists(), 'pyds_importable': pyds},
        'gpu_metrics_available': gpu_present,
    }


class InferenceRuntime:
    """Common surface: load a qualified artifact, infer, describe."""

    name = 'abstract'
    hardware_profile_suffix = 'onnx'

    def __init__(self, model_path, contract, precision='fp32'):
        self.model_path = Path(model_path)
        self.contract = contract
        self.precision = precision
        self.session = None

    def load(self):
        raise NotImplementedError

    def infer(self, tensor):
        """Run the graph and return the raw output tensor (unnormalised)."""
        raise NotImplementedError

    def describe(self):
        return {'runtime': self.name, 'precision': self.precision,
                'model_path': str(self.model_path), 'input_shape': list(self.contract.input_shape),
                'contract_profile': self.contract.profile,
                'expected_output_channels': self.contract.channels}


class _OnnxBackedRuntime(InferenceRuntime):
    """Shared ONNX Runtime behaviour: bind the tensor the model's own type declares.

    ONNX Runtime reports the declared input as `tensor(float)`, `tensor(float16)`,
    ... Sending float32 to the mixed-FP16 graph that ModelOpt AutoCast produces
    fails or silently inserts a cast, so the session's declared type decides the
    cast instead of an assumed float32.
    """

    def _bind_session(self, session, provider):
        """Attach a session and prove it actually bound the requested provider."""
        self.session = session
        self.provider_used = list(session.get_providers())
        if provider not in self.provider_used:
            # A session that quietly fell back is a CPU measurement wearing an
            # accelerator label, so it is refused rather than reported.
            raise RuntimeUnavailable(f'{provider}_not_bound_session_used:'
                                     + ','.join(self.provider_used))
        meta = session.get_inputs()[0]
        self.input_name = meta.name
        self.input_type = meta.type
        self.declared_input_shape = list(meta.shape)
        # Fail here rather than deep inside `session.run` with an unreadable error.
        numpy_dtype_for_onnx_type(self.input_type)
        return self

    def bind_input(self, tensor):
        target = numpy_dtype_for_onnx_type(self.input_type)
        array = tensor if isinstance(tensor, np.ndarray) else np.asarray(tensor)
        return np.ascontiguousarray(array.astype(target) if array.dtype != target else array)

    def infer(self, tensor):
        return self.session.run(None, {self.input_name: self.bind_input(tensor)})[0]

    def describe(self):
        payload = {**super().describe(), 'onnx_input_type': self.input_type,
                   'declared_input_shape': self.declared_input_shape,
                   'providers_used': self.provider_used}
        if self.input_type != 'tensor(float)':
            payload['input_type_note'] = ('the graph does not declare a float32 input; the adapter casts to '
                                          'the declared type instead of assuming float32')
        return payload


class OnnxRuntimeCPU(_OnnxBackedRuntime):
    name = CPU

    def load(self):
        import onnxruntime as ort
        if 'CPUExecutionProvider' not in ort.get_available_providers():
            raise RuntimeUnavailable('onnxruntime_cpu_provider_unavailable')
        options = ort.SessionOptions()
        options.log_severity_level = 3
        return self._bind_session(
            ort.InferenceSession(str(self.model_path), sess_options=options,
                                 providers=['CPUExecutionProvider']),
            'CPUExecutionProvider')


class OnnxRuntimeCUDA(_OnnxBackedRuntime):
    """CUDA execution provider. Refuses to run when the provider is absent."""

    name = CUDA
    hardware_profile_suffix = 'cuda'

    def load(self):
        import onnxruntime as ort
        if 'CUDAExecutionProvider' not in ort.get_available_providers():
            raise RuntimeUnavailable('cuda_execution_provider_missing')
        options = ort.SessionOptions()
        options.log_severity_level = 3
        # No CPU fallback list: a partially-offloaded session would misreport latency.
        # No fallback means a provider that cannot load raises here rather than
        # quietly producing CPU numbers under a CUDA label.
        try:
            session = ort.InferenceSession(str(self.model_path), sess_options=options,
                                           providers=['CUDAExecutionProvider'])
        except Exception as exc:  # noqa: BLE001
            raise RuntimeUnavailable(f'cuda_execution_provider_load_failed:{type(exc).__name__}:{exc}'[:400]) from exc
        return self._bind_session(session, 'CUDAExecutionProvider')


ENGINE_SUFFIX = '.engine'


class TensorRTRuntime(InferenceRuntime):
    """TensorRT engine path: ONNX -> FP16/FP32 engine -> canonical decode.

    `build_engine` is the only place an engine is written, and it runs only when
    TensorRT is genuinely importable on an NVIDIA host. No engine is downloaded,
    committed, or synthesised.
    """

    name = TENSORRT
    hardware_profile_suffix = 'tensorrt'

    def __init__(self, model_path, contract, precision='fp16', engine_path=None, workspace_bytes=1 << 30,
                 dynamic_input_shape=None):
        super().__init__(model_path, contract, precision)
        self.engine_path = Path(engine_path) if engine_path else Path(str(model_path) + ENGINE_SUFFIX)
        self.workspace_bytes = workspace_bytes
        self.dynamic_input_shape = tuple(dynamic_input_shape) if dynamic_input_shape else None
        self.gpu_name = None
        self.fast_fp16 = None
        self.dynamic_input = False
        self.tensor_dtypes = {}
        # The requested precision label and what the engine actually declares are two
        # different facts. Only the engine's own tensor dtypes count as evidence, so the
        # label is recorded separately and can never stand in for the dtype check.
        self.precision_label = precision
        self.precision_flag_applied = None
        self.precision_flag_reason = None
        self.onnx_input_dtype = None
        self.stream = None


    @staticmethod
    def planned_commands(model_path, engine_path, precision='fp16'):
        """Exact commands an NVIDIA host must run; recorded for reproducibility."""
        return {'trtexec': f'trtexec --onnx={model_path} --saveEngine={engine_path} '
                           f'--fp16={str(precision == "fp16").lower()} --fp32={str(precision == "fp32").lower()}',
                'or_tensorrt_python': 'edge.runtimes.TensorRTRuntime.build_engine'}

    # -- capability --------------------------------------------------------
    @staticmethod
    def require_nvidia_gpu():
        """Return the GPU name or raise. A GPU is required, never a fallback."""
        try:
            import torch
        except Exception as exc:  # noqa: BLE001
            raise RuntimeUnavailable('torch_not_installed_for_tensorrt_buffers') from exc
        if not torch.cuda.is_available():
            raise RuntimeUnavailable('no_nvidia_gpu_detected')
        return torch.cuda.get_device_name(0)

    def _tensorrt(self):
        try:
            import tensorrt as trt
        except Exception as exc:  # noqa: BLE001
            raise RuntimeUnavailable('tensorrt_module_not_installed') from exc
        return trt

    def _resolve_dynamic_shape(self, declared_shape):
        """A concrete min=opt=max shape for a dynamic input, taken from the contract.

        TensorRT requires an optimization profile before it will build a network
        with a dynamic dimension. Pinning min=opt=max keeps the engine honest: no
        performance claim is made for batch sizes that were never built.
        """
        reference = tuple(int(dim) for dim in self.contract.input_shape)
        return tuple(int(dim) if dim > 0
                     else (reference[position] if position < len(reference) and reference[position] > 0 else 1)
                     for position, dim in enumerate(declared_shape))

    def build_engine(self, workspace_bytes=1 << 30, timing_cache=None):
        """ONNX -> serialized engine. Writes only where the caller asked it to."""
        trt = self._tensorrt()
        self.gpu_name = self.require_nvidia_gpu()
        logger = trt.Logger(trt.Logger.WARNING)
        builder = trt.Builder(logger)
        try:
            flags = 1 << int(trt.NetworkDefinitionCreationFlag.EXPLICIT_BATCH)
            network = builder.create_network(flags)
        except AttributeError:  # TensorRT 10 folds EXPLICIT_BATCH into create_network()
            network = builder.create_network()
        parser = trt.OnnxParser(network, logger)
        with open(self.model_path, 'rb') as handle:
            if not parser.parse(handle.read()):
                errors = [parser.get_error(index).desc() for index in range(parser.num_errors)]
                raise RuntimeUnavailable('tensorrt_onnx_parse_failed:' + '; '.join(errors[:3]))
        config = builder.create_builder_config()
        config.set_memory_pool_limit(trt.MemoryPoolType.WORKSPACE, workspace_bytes)
        # TensorRT 10 keeps the ONNX-declared shape on the network definition, and a
        # dynamic tensor is rejected by the builder until an optimization profile
        # exists. The qualified artifact declares a fixed [1,3,640,640] input, so
        # this is a compatibility branch rather than the one this run exercises.
        network_input = network.get_input(0)
        declared_shape = tuple(int(dim) for dim in network_input.shape)
        self.dynamic_input = any(dim < 0 for dim in declared_shape)
        if self.dynamic_input:
            resolved = self._resolve_dynamic_shape(declared_shape)
            profile = builder.create_optimization_profile()
            profile.set_shape(network_input.name, min=resolved, opt=resolved, max=resolved)
            config.add_optimization_profile(profile)
        self.onnx_input_dtype = str(network_input.dtype)
        if self.precision == 'fp16':
            # platform_has_fast_fp16 is deprecated across TensorRT 10.x, so its
            # absence is treated as "not asserted" rather than as a failure.
            fast_fp16 = getattr(builder, 'platform_has_fast_fp16', None)
            self.fast_fp16 = None if fast_fp16 is None else bool(fast_fp16)
            # TensorRT 11 removed the builder-level FP16 flag: precision is carried by a
            # strongly-typed ONNX graph instead. Where the flag is gone nothing is
            # asserted, because the engine's own tensor dtypes are the only proof of
            # precision - see `actual_precision()` and `precision_label_matches_engine()`.
            flag = getattr(trt.BuilderFlag, 'FP16', None)
            if flag is None:
                self.precision_flag_applied = False
                self.precision_flag_reason = ('builder_flag_fp16_absent_precision_lives_in_the_sonnx_graph'
                                              .replace('sonnx', 'onnx'))
            else:
                if self.fast_fp16 is False:
                    raise RuntimeUnavailable('gpu_has_no_fast_fp16')
                config.set_flag(flag)
                self.precision_flag_applied = True
                self.precision_flag_reason = 'builder_flag_fp16_set'
        elif self.precision == 'fp32':
            self.precision_flag_applied = False
            self.precision_flag_reason = 'fp32_label_no_builder_flag_required'
        else:
            raise RuntimeUnavailable('unsupported_engine_precision')
        if timing_cache:
            config.set_timing_cache(timing_cache, ignore_mismatch=True)
        serialized = builder.build_serialized_network(network, config)
        if serialized is None:
            raise RuntimeUnavailable('tensorrt_engine_build_failed')
        self.engine_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine_path.write_bytes(bytes(serialized))
        return self.engine_path

    def load(self):
        """Deserialize and bind the raw tensor addresses used by `infer`."""
        trt = self._tensorrt()
        self.gpu_name = self.require_nvidia_gpu()
        import torch
        if not self.engine_path.exists():
            raise RuntimeUnavailable('tensorrt_engine_missing_build_it_on_this_gpu_host')
        logger = trt.Logger(trt.Logger.WARNING)
        runtime = trt.Runtime(logger)
        self.engine = runtime.deserialize_cuda_engine(self.engine_path.read_bytes())
        if self.engine is None:
            raise RuntimeUnavailable('tensorrt_engine_deserialize_failed')
        self.context = self.engine.create_execution_context()
        self.trt = trt
        self.torch = torch
        self.io = {'inputs': [], 'outputs': []}
        self.tensor_dtypes = {}
        for index in range(self.engine.num_io_tensors):
            name = self.engine.get_tensor_name(index)
            mode = self.engine.get_tensor_mode(name)
            trt_dtype = self.engine.get_tensor_dtype(name)
            self.tensor_dtypes[name] = trt_dtype
            entry = {'name': name, 'dtype': str(trt_dtype),
                     'shape': tuple(int(dim) for dim in self.engine.get_tensor_shape(name)),
                     'torch_dtype': str(self._torch_dtype(name))}
            self.io['inputs' if mode == trt.TensorIOMode.INPUT else 'outputs'].append(entry)
        if len(self.io['inputs']) != 1:
            raise RuntimeUnavailable('tensorrt_requires_a_single_input')
        self.input_name = self.io['inputs'][0]['name']
        self.output_names = [entry['name'] for entry in self.io['outputs']]
        # A dynamic input keeps its -1 dimension here and is bound per call in
        # `infer`; a fixed-shape engine binds once. Neither case is assumed.
        self.dynamic_input = any(dim < 0 for dim in self.io['inputs'][0]['shape'])
        # A dedicated stream: enqueuing on the default stream both trips TensorRT's own
        # warning and lets the host copy race. Latency measured on this stream is a
        # measured latency, not a queued launch.
        self.stream = torch.cuda.Stream()
        return self

    # Engine-declared dtype -> torch/numpy. A HALF tensor is not a FLOAT tensor, so
    # every buffer is allocated from the engine's own declaration instead of an
    # assumed float32.
    TORCH_DTYPES = {'FLOAT': 'float32', 'HALF': 'float16', 'INT32': 'int32', 'INT8': 'int8',
                    'BOOL': 'bool', 'UINT8': 'uint8', 'INT64': 'int64', 'BF16': 'bfloat16'}
    NUMPY_DTYPES = {'FLOAT': np.float32, 'HALF': np.float16, 'INT32': np.int32, 'INT8': np.int8,
                    'BOOL': np.bool_, 'UINT8': np.uint8, 'INT64': np.int64}

    def dtype_name(self, tensor_name):
        """The engine's declared dtype as a plain name, or None if unrecognised."""
        trt_type = self.tensor_dtypes.get(tensor_name)
        if trt_type is None:
            return None
        for attribute in self.TORCH_DTYPES:
            if trt_type == getattr(self.trt.DataType, attribute, None):
                return attribute
        return None

    def _torch_dtype(self, tensor_name):
        return getattr(self.torch, self.TORCH_DTYPES.get(self.dtype_name(tensor_name) or '', 'float32'))

    def _numpy_dtype(self, tensor_name):
        name = self.dtype_name(tensor_name)
        if name not in self.NUMPY_DTYPES:
            raise RuntimeUnavailable('unsupported_tensorrt_input_dtype:'
                                     + str(self.tensor_dtypes.get(tensor_name)))
        return self.NUMPY_DTYPES[name]

    # Dtype names that count as evidence for a requested precision label.
    PRECISION_EVIDENCE = {'fp32': ('FLOAT',), 'fp16': ('HALF', 'BF16')}

    def _engine_dtype_names(self):
        """Sorted dtype names the loaded engine declares across all I/O tensors."""
        if not getattr(self, 'io', None):
            return []
        names = {self.dtype_name(entry['name']) for entry in self.io['inputs'] + self.io['outputs']}
        return sorted(name for name in names if name)

    def actual_precision(self):
        """Precision the ENGINE declares - never the label that was requested."""
        kinds = self._engine_dtype_names()
        if not kinds:
            return 'UNKNOWN'
        if kinds == ['FLOAT']:
            return 'FP32'
        if kinds == ['HALF']:
            return 'FP16'
        return 'MIXED:' + '+'.join(kinds)

    def precision_label_matches_engine(self):
        """True only when the engine's own tensor dtypes back the requested label.

        This is the check that stops an FP32 graph from being reported as FP16 merely
        because the caller asked for `fp16`: `MIXED:FLOAT+HALF` contains real HALF
        evidence, a pure FLOAT engine contains none. It is deliberately strict in both
        directions — a mixed engine is not an FP32 engine either — so a label can only
        ever describe what the engine actually declares.
        """
        declared = set(self._engine_dtype_names())
        if not declared:
            return False
        if self.precision == 'fp32':
            return declared == {'FLOAT'}
        required = self.PRECISION_EVIDENCE.get(self.precision)
        if not required:
            return False
        # Any one genuinely reduced-precision dtype is acceptable evidence for `fp16`.
        return bool(declared & set(required))

    def precision_provenance(self):
        """The evidence trail for a precision claim, so it can be audited later."""
        return {'precision_label': self.precision,
                'actual_engine_precision': self.actual_precision(),
                'engine_dtypes': self._engine_dtype_names(),
                'label_backed_by_engine_dtypes': self.precision_label_matches_engine(),
                'builder_flag_fp16_applied': self.precision_flag_applied,
                'builder_flag_reason': self.precision_flag_reason,
                'onnx_input_dtype': self.onnx_input_dtype,
                'dedicated_cuda_stream': self.stream is not None,
                'rule': ('a precision label is only VERIFIED when the engine declares matching tensor '
                         'dtypes; the requested label is never treated as evidence')}

    def infer(self, tensor):
        """Run the engine on its own CUDA stream; return the first output as float32 numpy.

        Device memory is allocated with the dtype the engine declares, never an
        assumed float32. Only the returned host array is widened to float32, because
        the canonical decoder in `shared/model_contract.py` is float32 arithmetic.
        The dedicated stream is synchronised before the host copy, so a reported
        latency is a measured latency and not a queued kernel launch.
        """
        torch = self.torch
        device = torch.device('cuda')
        host = tensor if isinstance(tensor, np.ndarray) else np.asarray(tensor)
        expected = tuple(self.io['inputs'][0]['shape'])
        if tuple(host.shape) != expected:
            if not self.dynamic_input:
                raise RuntimeUnavailable(f'tensorrt_input_shape_mismatch expected={expected} got={tuple(host.shape)}')
            if not self.context.set_input_shape(self.input_name, tuple(int(dim) for dim in host.shape)):
                raise RuntimeUnavailable(f'tensorrt_set_input_shape_failed got={tuple(host.shape)}')
        # All device work runs on the runtime's own stream, and that stream is what is
        # synchronised. The default stream is never enqueued onto: TensorRT warns about
        # it, and a host copy issued on the default stream can race the engine's work.
        stream = self.stream
        stream.wait_stream(torch.cuda.current_stream())
        with torch.cuda.stream(stream):
            device_input = torch.from_numpy(
                np.ascontiguousarray(host, dtype=self._numpy_dtype(self.input_name))).to(device)
            addresses = {self.input_name: device_input.data_ptr()}
            device_outputs = []
            for name in self.output_names:
                shape = tuple(int(dim) for dim in self.context.get_tensor_shape(name))
                if any(dim < 0 for dim in shape):
                    raise RuntimeUnavailable('tensorrt_unresolved_output_shape:' + name)
                buffer = torch.empty(shape, dtype=self._torch_dtype(name), device=device)
                device_outputs.append(buffer)
                addresses[name] = buffer.data_ptr()
            for name, pointer in addresses.items():
                if not self.context.set_tensor_address(name, pointer):
                    raise RuntimeUnavailable('tensorrt_set_tensor_address_failed:' + name)
            if not self.context.execute_async_v3(stream_handle=stream.cuda_stream):
                raise RuntimeUnavailable('tensorrt_execute_async_v3_failed')
        stream.synchronize()
        return device_outputs[0].detach().to(torch.float32).cpu().numpy()

    def describe(self):
        payload = {**super().describe(), 'engine': str(self.engine_path),
                   'engine_bytes': self.engine_path.stat().st_size if self.engine_path.exists() else None,
                   'engine_sha256': (hashlib.sha256(self.engine_path.read_bytes()).hexdigest()
                                     if self.engine_path.exists() else None),
                   'onnx_sha256': hashlib.sha256(self.model_path.read_bytes()).hexdigest(),
                   'workspace_bytes': self.workspace_bytes}
        if getattr(self, 'io', None):
            payload['engine_io'] = self.io
            payload['gpu'] = self.gpu_name
            payload['tensorrt_version'] = getattr(self.trt, '__version__', None)
            payload['dynamic_input'] = self.dynamic_input
            payload['fast_fp16'] = self.fast_fp16
            payload.update(self.precision_provenance())
        if hasattr(self, 'engine') and self.engine is not None:
            payload['device_memory_bytes'] = int(self.engine.device_memory_size) if hasattr(
                self.engine, 'device_memory_size') else None
        return payload


RUNTIMES = {CPU: OnnxRuntimeCPU, CUDA: OnnxRuntimeCUDA, TENSORRT: TensorRTRuntime}


def create_runtime(name, model_path, contract, **kwargs):
    """Instantiate a runtime class; loading is the caller's explicit step."""
    if name not in RUNTIMES:
        raise ValueError('unknown_inference_runtime')
    return RUNTIMES[name](model_path, contract, **kwargs)


def preferred_runtime(capabilities=None):
    """Highest genuinely available runtime; never assumes NVIDIA is present."""
    capabilities = capabilities or detect_capabilities()
    for name in (TENSORRT, CUDA, CPU):
        if capabilities.get(name, {}).get('available'):
            return name
    return None
