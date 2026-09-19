"""Real NVIDIA GPU verification for the VisionOps PPE model.

    qualified FP32 ONNX -> TensorRT FP32 -> parity -> benchmark
    qualified FP32 ONNX -> ModelOpt mixed-FP16 ONNX -> metadata preservation
                        -> FP32-vs-FP16 ONNX parity -> TensorRT FP16 -> parity -> benchmark

    -> parity against the reference runtime through the SAME canonical decoder
    -> synchronised latency/throughput benchmark + optional trtexec summary
    -> JSON evidence

This script never falls back to CPU. If TensorRT or an NVIDIA GPU is genuinely
absent it writes a blocker report and exits `BLOCKED_BY_NVIDIA_HARDWARE` (code 3),
because a CPU number is not a TensorRT result.

It also refuses to call an engine FP16 because the caller asked for `fp16`. Every
precision claim is checked against the **engine's own declared tensor dtypes** after
load: a label the engine does not back is recorded as
`NOT VERIFIED - LABEL_NOT_BACKED_BY_ENGINE_DTYPES` and kept out of the authoritative
benchmark table. TensorRT 11 removed `BuilderFlag.FP16`, so true FP16 requires a
strongly-typed graph from `scripts/convert_fp16_onnx.py`; without one, the precision is
skipped with that reason rather than measured under a false label.

Exit codes
    0  at least one precision verified (parity + benchmark, label backed by real dtypes)
    3  BLOCKED_BY_NVIDIA_HARDWARE (no GPU / no TensorRT)
    4  ran on a GPU but a precision failed parity or the benchmark

    # FP32
    python -m scripts.verify_tensorrt_gpu \
        --onnx var/model/hansung-p3.onnx \
        --contract-record var/model/hansung-p3.json \
        --video var/media/ppe-2.mp4 --frames 0,25,50,100,150,200 \
        --precisions fp32 --out docs/evidence/tensorrt/rerun-fp32

    # true mixed FP16 (TensorRT 11+)
    python -m scripts.convert_fp16_onnx \
        --onnx var/model/hansung-p3.onnx --out var/model/hansung-p3-fp16.onnx
    python -m scripts.verify_tensorrt_gpu \
        --onnx var/model/hansung-p3.onnx --fp16-onnx var/model/hansung-p3-fp16.onnx \
        --video var/media/ppe-2.mp4 --frames 0,25,50,100,150,200 \
        --precisions fp16 --out docs/evidence/tensorrt/rerun-fp16
"""
import argparse
import hashlib
import json
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BLOCKED_EXIT = 3
FAILED_EXIT = 4
PARITY_IOU_MINIMUM = 0.5
PARITY_CONFIDENCE_TOLERANCE = 0.05
PARITY_SCORE_THRESHOLD = 0.35
ENGINE_PLAN = ('trtexec --onnx=<onnx> --saveEngine=<engine> --fp16 ; equivalently '
               'edge.runtimes.TensorRTRuntime.build_engine() (used here)')


def utc():
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def git_commit():
    try:
        commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True,
                                text=True, timeout=15).stdout.strip()
        dirty = subprocess.run(['git', 'status', '--porcelain'], cwd=ROOT, capture_output=True,
                               text=True, timeout=15).stdout.strip()
        return {'commit': commit or None, 'working_tree': 'dirty' if dirty else 'clean'}
    except Exception as exc:  # noqa: BLE001
        return {'commit': None, 'working_tree': 'unknown', 'error': type(exc).__name__}


class Step:
    """Step recorder so a failure reports which stage failed and why."""

    def __init__(self):
        self.records = []

    def _add(self, name, status, **payload):
        self.records.append({'step': name, 'status': status, **payload})
        print(f'[{status:^6}] {name} ' + json.dumps(payload, default=str)[:220], flush=True)

    def ok(self, name, **payload):
        self._add(name, 'ok', **payload)

    def failed(self, name, reason, **payload):
        self._add(name, 'failed', reason=reason, **payload)

    def skipped(self, name, reason, **payload):
        self._add(name, 'skipped', reason=reason, **payload)

    def as_list(self):
        return self.records


def collect_environment():
    """Every value is read from the machine; nothing is assumed or defaulted."""
    from edge.runtimes import detect_capabilities

    capabilities = detect_capabilities()
    environment = {'captured_at': utc(), 'host': capabilities['host'], 'python': sys.version,
                   'platform': platform.platform(), 'machine': platform.machine(),
                   'gpu_names': capabilities['nvidia_smi']['gpu_names'],
                   'nvidia_smi_query': capabilities['nvidia_smi']['query_output'],
                   'nvidia_smi': {key: capabilities['nvidia_smi'][key]
                                  for key in ('present', 'gpu_detected', 'reason', 'gpu_names')},
                   'onnxruntime_providers': capabilities['ONNX_CPU']['providers'],
                   'driver': None, 'cuda': None, 'tensorrt': None, 'torch': None, 'onnxruntime': None}
    raw = capabilities['nvidia_smi']['query_output']
    if raw:
        first = [value.strip() for value in raw.splitlines()[0].split(',')]
        if len(first) >= 4:
            environment['driver'] = first[1]
            environment['gpu_vram'] = first[2]
            environment['compute_capability'] = first[3]
    try:
        import torch
        environment['torch'] = torch.__version__
        environment['torch_cuda_build'] = torch.version.cuda
        environment['torch_cuda_available'] = bool(torch.cuda.is_available())
        if torch.cuda.is_available():
            properties = torch.cuda.get_device_properties(0)
            environment['torch_device_name'] = torch.cuda.get_device_name(0)
            environment['torch_total_memory_bytes'] = int(properties.total_memory)
            environment['torch_compute_capability'] = f'{properties.major}.{properties.minor}'
        if torch.version.cuda:
            environment['cuda'] = torch.version.cuda
    except Exception as exc:  # noqa: BLE001
        environment['torch'] = f'unavailable: {type(exc).__name__}'
    try:
        import onnxruntime
        environment['onnxruntime'] = onnxruntime.__version__
    except Exception:  # noqa: BLE001
        pass
    try:
        import tensorrt as trt
        environment['tensorrt'] = trt.__version__
    except Exception as exc:  # noqa: BLE001
        environment['tensorrt'] = f'unavailable: {type(exc).__name__}'
    if environment.get('cuda') is None and shutil.which('nvcc'):
        result = subprocess.run(['nvcc', '--version'], capture_output=True, text=True, timeout=20)
        environment['cuda'] = next((line.strip() for line in result.stdout.splitlines()
                                    if 'release' in line), None)
    environment['capabilities'] = {key: capabilities[key] for key in
                                  ('ONNX_CPU', 'ONNX_CUDA', 'TENSORRT', 'DEEPSTREAM', 'gpu_metrics_available')}
    environment['git'] = git_commit()
    return environment, capabilities


def verify_model(onnx_path, contract_record):
    """Step 2: identify the artifact exactly, and refuse anything else."""
    import onnx

    path = Path(onnx_path)
    if not path.is_file():
        raise SystemExit(f'ONNX artifact not found: {path}')
    model = onnx.load(str(path), load_external_data=False)
    metadata = {prop.key: prop.value for prop in model.metadata_props}
    output = model.graph.output[0]
    record = {'path': str(path), 'bytes': path.stat().st_size, 'sha256': sha256_file(path),
              'opset': [{'domain': item.domain or 'ai.onnx', 'version': item.version}
                        for item in model.opset_import],
              'ir_version': model.ir_version, 'input_name': model.graph.input[0].name,
              'input_shape': [int(dim.dim_value) for dim in model.graph.input[0].type.tensor_type.shape.dim],
              'output_name': output.name,
              'output_shape': [int(dim.dim_value) for dim in output.type.tensor_type.shape.dim],
              'declared_names': metadata.get('names'),
              'producer': f'{model.producer_name} {model.producer_version}'.strip()}
    if contract_record and Path(contract_record).is_file():
        expected = json.loads(Path(contract_record).read_text())
        qualified = expected['artifact']['sha256']
        record['expected_sha256'] = qualified
        record['matches_qualified_artifact'] = record['sha256'] == qualified
        if record['sha256'] != qualified:
            raise SystemExit('ONNX artifact does not match the qualified artifact record (sha256 mismatch)')
        record['qualified_record'] = {'architecture': expected.get('source', {}).get('architecture'),
                                      'publisher': expected.get('source', {}).get('publisher'),
                                      'source_index_to_canonical_id': expected['source_index_to_canonical_id'],
                                      'canonical_class_map': expected['canonical_class_map'],
                                      'declared_output_shape': expected['output']['shape']}
    return record


ONNX_ELEM_TYPES = {1: 'float32', 2: 'uint8', 3: 'int8', 4: 'uint16', 5: 'int16', 6: 'int32', 7: 'int64',
                   9: 'bool', 10: 'float16', 11: 'float64', 12: 'uint32', 16: 'bfloat16'}


def _builder_flag_fp16_available():
    """Whether this TensorRT build still exposes the builder-level FP16 flag.

    TensorRT 11 removed it, which means precision has to live in the ONNX graph. This
    is the difference between a true FP16 engine and an FP32 engine wearing an FP16
    label, so it is checked explicitly instead of assumed.
    """
    try:
        import tensorrt as trt
    except Exception:  # noqa: BLE001
        return False
    return getattr(trt.BuilderFlag, 'FP16', None) is not None


def verify_fp16_graph(fp16_path, fp32_record, contract_record):
    """Identity of the ModelOpt mixed-FP16 graph, and proof its taxonomy survived.

    ModelOpt AutoCast drops the Ultralytics metadata, which is what the contract's
    `model_missing_class_metadata` guard exists to catch. The conversion helper
    restores it, and this function refuses the graph if it did not - so the safety
    check is enforced, never bypassed.
    """
    import onnx

    path = Path(fp16_path)
    if not path.is_file():
        raise SystemExit(f'mixed-FP16 ONNX not found: {path}')
    model = onnx.load(str(path), load_external_data=False)
    metadata = {prop.key: prop.value for prop in model.metadata_props}
    graph_input, output = model.graph.input[0], model.graph.output[0]
    record = {'path': str(path), 'bytes': path.stat().st_size, 'sha256': sha256_file(path),
              'opset': [{'domain': item.domain or 'ai.onnx', 'version': item.version}
                        for item in model.opset_import],
              'ir_version': model.ir_version, 'input_name': graph_input.name,
              'input_dtype': ONNX_ELEM_TYPES.get(graph_input.type.tensor_type.elem_type,
                                                 str(graph_input.type.tensor_type.elem_type)),
              'input_shape': [int(dim.dim_value) for dim in graph_input.type.tensor_type.shape.dim],
              'output_name': output.name,
              'output_shape': [int(dim.dim_value) for dim in output.type.tensor_type.shape.dim],
              'declared_names': metadata.get('names'),
              'producer': f'{model.producer_name} {model.producer_version}'.strip(),
              'derived_from_fp32_onnx_sha256': fp32_record['sha256'],
              'conversion_tool': metadata.get('modelopt_version') or metadata.get('ModelOpt')}
    if record['declared_names'] != fp32_record.get('declared_names'):
        raise SystemExit('mixed-FP16 ONNX lost the class metadata that ModelOpt strips; restore it with '
                         'scripts/convert_fp16_onnx.py before qualifying this artifact')
    record['class_metadata_restored'] = True
    record['input_shape_matches_fp32_graph'] = record['input_shape'] == fp32_record['input_shape']
    record['output_shape_matches_fp32_graph'] = record['output_shape'] == fp32_record['output_shape']
    if contract_record and Path(contract_record).is_file():
        expected = json.loads(Path(contract_record).read_text())
        record['declared_output_shape'] = expected['output']['shape']
        record['output_shape_matches_qualified_record'] = record['output_shape'] == expected['output']['shape']
    if not record['output_shape_matches_fp32_graph']:
        raise SystemExit('mixed-FP16 ONNX output shape differs from the qualified FP32 graph')
    return record


def onnx_graph_parity(fp32_path, fp32_record, fp16_path, fp16_record, frames, ids, contract, threshold):
    """Did the FP32 -> mixed-FP16 conversion itself change what the model predicts?

    Both graphs are decoded by the shared canonical contract on both sides, so a
    difference can only come from the conversion. Success of the export is never
    treated as evidence of correctness.
    """
    reference = Detector(fp32_path, fp32_record['sha256'], contract=contract)
    candidate = Detector(fp16_path, fp16_record['sha256'], contract=contract)
    result = parity(reference, candidate, frames, ids, threshold)
    return {'conversion': 'fp32_onnx_to_modelopt_mixed_fp16_onnx',
            'reference_onnx': fp32_record['path'], 'reference_onnx_sha256': fp32_record['sha256'],
            'candidate_onnx': fp16_record['path'], 'candidate_onnx_sha256': fp16_record['sha256'],
            'function_preserved': result['passed'],
            'meaning': ('the optimisation transform preserved model behaviour on the qualification samples; '
                        'it is not a claim that either graph is accurate against labels'),
            **result}


def load_samples(arguments):
    """Returns (metadata, frames). Every runtime sees these exact bytes."""
    import cv2

    frames, meta = [], []
    if arguments.samples_dir:
        for path in sorted(p for p in Path(arguments.samples_dir).iterdir()
                           if p.suffix.lower() in ('.jpg', '.jpeg', '.png')):
            frame = cv2.imread(str(path))
            if frame is None:
                raise SystemExit('unreadable sample image: ' + str(path))
            frames.append(frame)
            meta.append({'id': path.name, 'sha256': sha256_file(path), 'shape': list(frame.shape)})
    elif arguments.video:
        capture = cv2.VideoCapture(arguments.video)
        available = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        for index in [int(value) for value in arguments.frames.split(',') if value.strip()]:
            if index >= available:
                continue
            capture.set(cv2.CAP_PROP_POS_FRAMES, index)
            ok, frame = capture.read()
            if not ok:
                continue
            _, buffer = cv2.imencode('.jpg', frame)
            frames.append(frame)
            meta.append({'id': f'frame-{index:03d}', 'sha256': sha256_bytes(buffer.tobytes()),
                         'shape': list(frame.shape)})
        capture.release()
    else:
        raise SystemExit('supply --samples-dir or --video')
    if not frames:
        raise SystemExit('no evaluation samples were loaded')
    return ({'source': arguments.samples_dir or arguments.video, 'count': len(frames), 'samples': meta,
             'note': 'each runtime preprocesses these exact inputs with the shared letterbox'},
            frames)


def percentile(values, quantile):
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[max(0, int(len(ordered) * quantile) - 1)], 3)


def benchmark_raw(runtime, tensor, warmup, iterations):
    """Synchronised raw-runtime latency; preprocessing is excluded on purpose."""
    import torch
    for _ in range(warmup):
        runtime.infer(tensor)
    if torch.cuda.is_available():
        torch.cuda.synchronize()
    samples = []
    for _ in range(iterations):
        started = time.perf_counter()
        runtime.infer(tensor)
        samples.append((time.perf_counter() - started) * 1000)
    mean = float(statistics.fmean(samples))
    return {'iterations': iterations, 'warmup': warmup, 'p50_ms': percentile(samples, 0.5),
            'p95_ms': percentile(samples, 0.95), 'mean_ms': round(mean, 3),
            'min_ms': round(min(samples), 3), 'max_ms': round(max(samples), 3),
            'throughput_fps_model_only': round(1000.0 / mean, 2),
            'timing': 'host timer around a synchronised execute (torch.cuda.synchronize after each call)'}


def benchmark_detector(detector, frames, warmup, iterations):
    for _ in range(warmup):
        detector.infer(frames[0])
    samples = []
    for index in range(iterations):
        started = time.perf_counter()
        detector.infer(frames[index % len(frames)])
        samples.append((time.perf_counter() - started) * 1000)
    mean = float(statistics.fmean(samples))
    return {'iterations': iterations, 'warmup': warmup, 'p50_ms': percentile(samples, 0.5),
            'p95_ms': percentile(samples, 0.95), 'mean_ms': round(mean, 3),
            'throughput_fps_end_to_end': round(1000.0 / mean, 2),
            'scope': 'letterbox + runtime + canonical decode; excludes video decode, tracking and events'}


def iou(box_a, box_b):
    x, y = max(box_a[0], box_b[0]), max(box_a[1], box_b[1])
    X, Y = min(box_a[2], box_b[2]), min(box_a[3], box_b[3])
    intersection = max(0.0, X - x) * max(0.0, Y - y)
    union = ((box_a[2] - box_a[0]) * (box_a[3] - box_a[1])
             + (box_b[2] - box_b[0]) * (box_b[3] - box_b[1]) - intersection)
    return float(intersection / max(union, 1e-12))


def detections_of(detector, frame, threshold):
    detections, _ = detector.infer(frame, score_threshold=threshold)
    return ([list(box) for box in detections.xyxy], [float(v) for v in detections.confidence],
            [int(v) for v in detections.class_id])


def parity(reference_detector, candidate_detector, frames, ids, threshold):
    """The same canonical decoder on both sides; greedy best-IoU match within a class."""
    rows, matched_total, total = [], 0, 0
    min_iou, sum_iou, max_confidence, max_box, class_agreement = 1.0, 0.0, 0.0, 0.0, 0
    for index, frame in enumerate(frames):
        expected = detections_of(reference_detector, frame, threshold)
        actual = detections_of(candidate_detector, frame, threshold)
        total += len(expected[0])
        used = set()
        matched = missing = 0
        for position, box in enumerate(expected[0]):
            best_iou, best_index = 0.0, -1
            for other in range(len(actual[0])):
                if other in used or actual[2][other] != expected[2][position]:
                    continue
                overlap = iou(box, actual[0][other])
                if overlap > best_iou:
                    best_iou, best_index = overlap, other
            if best_index >= 0 and best_iou >= PARITY_IOU_MINIMUM:
                used.add(best_index)
                matched += 1
                class_agreement += 1
                min_iou = min(min_iou, best_iou)
                sum_iou += best_iou
                max_confidence = max(max_confidence, abs(expected[1][position] - actual[1][best_index]))
                max_box = max(max_box, max(abs(a - b) for a, b in zip(box, actual[0][best_index])))
            else:
                missing += 1
        extra = len(actual[0]) - len(used)
        matched_total += matched
        rows.append({'sample': ids[index], 'reference_detections': len(expected[0]),
                     'candidate_detections': len(actual[0]), 'matched': matched,
                     'missing': missing, 'extra': extra,
                     'min_iou': round(min_iou, 4) if matched else None,
                     'max_confidence_delta': round(max_confidence, 4) if matched else None})
    mean_iou = round(sum_iou / matched_total, 4) if matched_total else None
    passed = bool(total) and matched_total == total and min_iou >= PARITY_IOU_MINIMUM \
        and max_confidence <= PARITY_CONFIDENCE_TOLERANCE
    return {'threshold': threshold, 'samples': rows, 'reference_detections': total,
            'matched': matched_total, 'missing': total - matched_total,
            'min_iou': round(min_iou, 4) if matched_total else None, 'mean_iou': mean_iou,
            'max_confidence_delta': round(max_confidence, 4) if matched_total else None,
            'max_box_delta_pixels': round(max_box, 4) if matched_total else None,
            'class_agreement': f'{class_agreement}/{matched_total}' if matched_total else '0/0',
            'criterion': f'all detections matched within the same class at IoU>={PARITY_IOU_MINIMUM} '
                         f'and |confidence delta|<={PARITY_CONFIDENCE_TOLERANCE}',
            'passed': bool(passed)}


def contract_semantics(detector, frames, ids, contract, threshold=0.001):
    """Step 12: a discarded source class must not be able to hijack the decision.

    The head has more classes than the canonical taxonomy. A naive global argmax
    would relabel or drop boxes and silently change the PPE interpretation; this
    counts how often that would have happened on the real tensors.
    """
    from shared.model_contract import canonical_detections, letterbox

    rows, total_boxes, discarded_top, divergent = [], 0, 0, 0
    for index, frame in enumerate(frames):
        tensor, geometry = letterbox(frame, tuple(contract.input_shape))
        raw = detector.runtime.infer(tensor)
        predictions = np.squeeze(np.asarray(raw))
        if predictions.shape[0] < predictions.shape[1]:
            predictions = predictions.T
        scores = predictions[:, 4:]
        if scores.shape[1] != contract.source_count:
            raise SystemExit(f'raw channel count {scores.shape[1]} != contract {contract.source_count}')
        global_top = scores.argmax(1)
        subset_top = np.array(contract.sources)[scores[:, list(contract.sources)].argmax(1)]
        above = scores.max(1) >= threshold
        discarded = int(np.sum(above & ~np.isin(global_top, list(contract.sources))))
        changed = int(np.sum(above & (global_top != subset_top)))
        total_boxes += int(above.sum())
        discarded_top += discarded
        divergent += changed
        canonical = canonical_detections(raw, contract, threshold, geometry)
        rows.append({'sample': ids[index], 'boxes_above_threshold': int(above.sum()),
                     'global_argmax_on_discarded_class': discarded,
                     'global_argmax_differs_from_canonical_subset': changed,
                     'canonical_detections_emitted': int(len(canonical[0])),
                     'canonical_classes': sorted({int(v) for v in canonical[2]})})
    conclusion = (f'A global argmax would have changed the interpretation of {divergent} box(es) in this '
                  f'sample set; every runtime therefore uses the canonical subset decode.'
                  if divergent else
                  'No box in this sample set would be re-interpreted by a global argmax.')
    return {'threshold': threshold, 'mapped_source_indices': list(contract.sources),
            'discarded_source_indices': sorted(set(range(contract.source_count)) - set(contract.sources)),
            'source_count': contract.source_count, 'canonical_classes': sorted(contract.mapping.values()),
            'boxes_above_threshold': total_boxes, 'global_argmax_on_discarded_class': discarded_top,
            'argmax_interpretations_that_would_differ': divergent, 'samples': rows, 'conclusion': conclusion}


def run_trtexec(engine_path, out, iterations):
    """If NVIDIA's trtexec exists, preserve its own performance summary verbatim."""
    executable = shutil.which('trtexec')
    if not executable:
        return {'status': 'NOT AVAILABLE', 'reason': 'trtexec not on PATH (pip TensorRT wheels omit it)'}
    command = [executable, f'--loadEngine={engine_path}', f'--iterations={iterations}',
               f'--warmUp=1000', '--avgRuns=100', '--percentile=95', '--useSpinWait']
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        return {'status': 'FAILED', 'reason': 'timeout', 'command': ' '.join(command)}
    (out / 'trtexec.log').write_text(f'$ {" ".join(command)}\n\n' + result.stdout + result.stderr)
    summary = [line for line in result.stdout.splitlines()
               if 'Throughput' in line or 'Latency' in line or 'percentile' in line]
    return {'status': 'VERIFIED' if result.returncode == 0 else 'FAILED', 'command': ' '.join(command),
            'returncode': result.returncode, 'summary': summary[-12:]}


# Every status sentence in the repository that a GPU run is allowed to change. The
# exact line is located at run time instead of being copied into this script, so the
# patch cannot silently point at text that has since moved.
STATUS_ANCHORS = (
    {'file': 'docs/CURRENT_VERIFIED_STATE.md', 'anchor': '| TensorRT runtime |',
     'intent': 'move the TensorRT runtime row out of "IMPLEMENTED - NOT VERIFIED" and cite this run'},
    {'file': 'docs/CURRENT_VERIFIED_STATE.md', 'anchor': '* NVIDIA runtime verification:',
     'intent': 'keep the local-host limitation, and scope it to the local host only'},
    {'file': 'README.md', 'anchor': 'Added and **implemented but not runtime verified**',
     'intent': 'state that the TensorRT runtime is now verified on an NVIDIA GPU'},
    {'file': 'docs/VERIFICATION_REPORT.md', 'anchor': '| GStreamer backend, MediaMTX environment, TensorRT engine path',
     'intent': 'split the TensorRT engine path out of the unverified row'},
    {'file': 'docs/KNOWN_LIMITATIONS.md', 'anchor': '> but **not runtime verified**:',
     'intent': 'remove the TensorRT engine runtime from the local not-verified banner'},
    {'file': 'docs/BENCHMARK_REPORT.md', 'anchor': 'NVIDIA/TensorRT benchmarks remain BLOCKED BY HARDWARE.',
     'intent': 'replace the blocked NVIDIA statement with the measured GPU benchmark table'},
    {'file': 'docs/JETSON_DEPLOYMENT_TARGET.md', 'anchor': '**no physical Jetson, ARM64 execution or TensorRT runtime was validated here**',
     'intent': 'keep Jetson unverified and name the GPU host that was validated instead'},
    {'file': 'docs/interview/TENSORRT_INTERVIEW_EVIDENCE.md', 'anchor': 'TensorRT on NVIDIA GPU:',
     'intent': 'flip the one line the GPU run authorises'},
)


def _anchor_edit(entry):
    """Locate the status line a patch entry targets, so the edit is mechanical."""
    path = ROOT / entry['file']
    record = {**entry, 'path_exists': path.is_file(), 'line': None, 'before': None}
    if not path.is_file():
        record['status'] = 'file_absent_at_run_time'
        return record
    for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), start=1):
        if entry['anchor'] in line:
            record.update({'status': 'located', 'line': number, 'before': line.strip()[:400]})
            return record
    record['status'] = 'anchor_not_found'
    return record


def write_status_patch(out, environment, table, results, model_record):
    """The exact edits a VERIFIED run authorises - and nothing wider.

    The patch is generated, never applied: this script produces evidence, and a
    status flip still needs a human to land it with the surrounding prose intact.
    """
    verified = sorted(precision for precision, result in results.items() if result.get('status') == 'VERIFIED')
    patch = {
        'generated_at': utc(),
        'authorised': bool(verified),
        'verified_precisions': verified,
        'status_line_to_set': 'TensorRT on NVIDIA GPU: VERIFIED',
        'hardware': {
            'gpu': environment.get('torch_device_name') or (environment.get('gpu_names') or [None])[0],
            'driver': environment.get('driver'), 'cuda': environment.get('cuda'),
            'tensorrt': environment.get('tensorrt'),
            'compute_capability': environment.get('compute_capability'),
            'framework': 'CUDA GPU host (Colab/Kaggle class) - NOT a Jetson'},
        'model': {'sha256': model_record['sha256'], 'input_shape': model_record['input_shape'],
                  'output_shape': model_record['output_shape']},
        'authorised_by': 'docs/evidence/tensorrt/tensorrt-verification.json',
        'edits': [_anchor_edit(entry) for entry in STATUS_ANCHORS],
        'must_not_change': [
            'Physical NVIDIA Jetson: NOT VERIFIED',
            'JetPack on physical hardware: NOT VERIFIED',
            'Jetson thermal / power / NVDEC behaviour: NOT VERIFIED',
            '10K physical Jetson fleet: NOT VERIFIED',
            'ARM64 runtime execution: NOT VERIFIED',
            'model quality (mAP, no_helmet precision/recall, event precision/recall): BLOCKED BY DATA'],
        'scope_note': ('a Colab/Kaggle T4-class GPU is not a Jetson. Only "TensorRT on NVIDIA GPU" becomes '
                       'VERIFIED; every claim about physical Jetson or ARM64 hardware stays NOT VERIFIED.'),
    }
    (out / 'status-patch.json').write_text(json.dumps(patch, indent=2, default=str))
    return patch


def _markdown_table(headers, rows):
    lines = ['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join('---' for _ in headers) + ' |']
    for row in rows:
        lines.append('| ' + ' | '.join('NOT VERIFIED' if value is None else str(value) for value in row) + ' |')
    return '\n'.join(lines)


def write_gpu_run_result(out, summary, environment, table, results, patch):
    """One human-readable file that states what this GPU run measured, and what it did not."""
    gpu = environment.get('torch_device_name') or (environment.get('gpu_names') or ['NOT VERIFIED'])[0]
    hardware = _markdown_table(['Fact', 'Value'], [
        ('GPU', gpu), ('Driver', environment.get('driver')), ('CUDA', environment.get('cuda')),
        ('TensorRT', environment.get('tensorrt')), ('PyTorch', environment.get('torch')),
        ('PyTorch CUDA build', environment.get('torch_cuda_build')),
        ('torch.cuda.is_available()', environment.get('torch_cuda_available')),
        ('Compute capability', environment.get('compute_capability')),
        ('VRAM', environment.get('gpu_vram')), ('Platform', environment.get('platform')),
        ('ONNX Runtime', environment.get('onnxruntime')),
        ('Host type', 'CUDA GPU host (Colab/Kaggle class) - NOT a Jetson'),
        ('Captured at', environment.get('captured_at'))])
    model = _markdown_table(['Fact', 'Value'], [
        ('ONNX path', summary['model']['path']), ('ONNX SHA-256', summary['model']['sha256']),
        ('ONNX bytes', summary['model']['bytes']),
        ('Input', f"{summary['model']['input_name']} {summary['model']['input_shape']}"),
        ('Output', f"{summary['model']['output_name']} {summary['model']['output_shape']}"),
        ('Opset', summary['model']['opset']),
        ('Contract profile', summary['contract'].get('profile')),
        ('Score head width', summary['contract'].get('source_count')),
        ('Class mapping version', summary['class_mapping_version']),
        ('Evaluation samples', summary['samples']['count'])])
    parity_headers = ['Precision', 'Reference', 'Reference detections', 'Matched', 'Missing', 'Extra',
                      'Min IoU', 'Mean IoU', 'Max confidence delta', 'Max box delta (px)', 'Class agreement', 'Passed']
    parity_rows = []
    for precision, result in sorted(results.items()):
        parity = result.get('parity')
        if not parity:
            parity_rows.append([precision, result.get('reason', 'FAILED')] + ['NOT VERIFIED'] * 10)
            continue
        # Every field is read defensively: an evidence document that throws would cost
        # the whole run, and a missing measurement must render as NOT VERIFIED.
        parity_rows.append([precision,
                            parity.get('reference_runtime') or summary['model'].get('reference_runtime'),
                            parity.get('reference_detections'), parity.get('matched'), parity.get('missing'),
                            sum(row.get('extra', 0) for row in parity.get('samples', [])),
                            parity.get('min_iou'), parity.get('mean_iou'), parity.get('max_confidence_delta'),
                            parity.get('max_box_delta_pixels'), parity.get('class_agreement'),
                            parity.get('passed')])
    benchmark_rows = [[row.get('runtime'), row.get('precision'), row.get('gpu'), row.get('input'),
                       row.get('p50_ms'), row.get('p95_ms'), row.get('mean_ms'),
                       row.get('throughput_fps_model_only'), row.get('gpu_memory')] for row in table]
    precision_notes = []
    for precision, result in sorted(results.items()):
        engine = result.get('engine') or {}
        precision_notes.append({precision: {'engine_sha256': (engine.get('engine_sha256') or 'NOT VERIFIED'),
                                             'engine_bytes': engine.get('engine_bytes'),
                                             'build_seconds': engine.get('build_seconds'),
                                             'engine_dtypes': (engine.get('engine_io') or {}).get('inputs'),
                                             'fast_fp16': engine.get('fast_fp16'),
                                             'dynamic_input': engine.get('dynamic_input'),
                                             'trtexec': (result.get('trtexec') or {}).get('status', 'NOT RUN')}})
    document = f"""# VisionOps TensorRT GPU run result

Status: **{summary['status']}**

Generated by `python -m scripts.verify_tensorrt_gpu` at {summary['captured_at']}.
Every number below was measured in this GPU session; the machine-readable source is
`tensorrt-verification.json`, `benchmark.json`, `parity_fp32.json`, `parity_fp16.json`
and `contract-semantics.json` in the same directory.

```text
REAL NVIDIA GPU -> real Hansung PPE ONNX -> TensorRT FP32 + FP16 -> real inference
                -> canonical decode -> parity vs reference -> latency/throughput
```

## Hardware

{hardware}

## Model under test

{model}

## Parity - TensorRT against the reference runtime, through the same canonical decoder

{_markdown_table(parity_headers, parity_rows)}

Criterion: `{next((r['parity']['criterion'] for r in results.values() if r.get('parity')), 'n/a')}`

## Benchmark (CUDA-synchronised, host timer around a synchronised execute)

{_markdown_table(['Runtime', 'Precision', 'GPU', 'Input', 'p50 ms', 'p95 ms', 'Mean ms',
                      'FPS (model-only)', 'GPU memory'], benchmark_rows)}

Model-only theoretical throughput (1000 / mean_latency_ms):

```json
{json.dumps(summary['model_only_theoretical_throughput']['values'], indent=2)}
```

{summary['model_only_theoretical_throughput']['caveat']}

Engine detail:

```json
{json.dumps(precision_notes, indent=2, default=str)}
```

## Canonical model contract (the fix this run guards)

```json
{json.dumps({key: summary['contract_semantics'].get(key) for key in
             ('mapped_source_indices', 'discarded_source_indices', 'source_count', 'canonical_classes',
              'boxes_above_threshold', 'global_argmax_on_discarded_class',
              'argmax_interpretations_that_would_differ', 'conclusion')}, indent=2, default=str)}
```

## INT8

`{summary['int8']['status']}` - {summary['int8']['reason']}

## Not verified by this run

{chr(10).join('* ' + item for item in summary['not_verified'])}

## Status patch

`status-patch.json` holds the exact edits this run authorises (`authorised: {str(patch['authorised']).lower()}`,
precisions `{patch['verified_precisions']}`). It changes only the TensorRT-on-GPU status.

{chr(10).join('* ' + item for item in patch['must_not_change'])}

{patch['scope_note']}
"""
    (out / 'GPU_RUN_RESULT.md').write_text(document, encoding='utf-8')
    return 'GPU_RUN_RESULT.md'


class _StubTensorRT:
    """Minimal TensorRT surface for the local precision-rule check. No GPU involved."""

    class DataType:
        FLOAT = 'FLOAT'
        HALF = 'HALF'
        BF16 = 'BF16'
        INT32 = 'INT32'
        INT8 = 'INT8'
        BOOL = 'BOOL'


def tensorrt_static_checks():
    """Checks on the TensorRT path that need no GPU to run.

    These catch the class of mistake a GPU session would otherwise be spent on: a
    buffer allocated as float32 for an engine that declares HALF, a TensorRT API
    surface the runtime does not actually use, or a precision label that is trusted
    instead of checked. Source inspection proves nothing about execution, and the
    precision-rule table below is a real behavioural check with a stubbed engine I/O
    surface - neither substitutes for the GPU gate further down.
    """
    import inspect

    from edge import runtimes

    runtime = runtimes.TensorRTRuntime
    infer_source = inspect.getsource(runtime.infer)
    load_source = inspect.getsource(runtime.load)
    build_source = inspect.getsource(runtime.build_engine)
    issues = []
    if '_torch_dtype(' not in infer_source:
        issues.append('infer_does_not_derive_output_dtype_from_the_engine')
    if '_numpy_dtype(self.input_name)' not in infer_source:
        issues.append('infer_does_not_derive_input_dtype_from_the_engine')
    if 'execute_async_v3' not in infer_source:
        issues.append('infer_does_not_use_execute_async_v3')
    if 'set_tensor_address' not in infer_source:
        issues.append('infer_does_not_bind_tensor_addresses')
    if 'num_io_tensors' not in load_source or 'get_tensor_dtype' not in load_source:
        issues.append('load_does_not_enumerate_io_tensors_with_dtypes')
    if 'create_optimization_profile' not in build_source:
        issues.append('build_engine_has_no_dynamic_shape_path')
    # The default-stream warning TensorRT emits is a correctness hazard, not just a
    # performance one: a host copy issued on the default stream can race the engine.
    uses_dedicated_stream = 'self.stream' in infer_source and 'current_stream().cuda_stream' not in infer_source
    if not uses_dedicated_stream:
        issues.append('infer_does_not_use_a_dedicated_cuda_stream')
    # TensorRT 11 dropped BuilderFlag.FP16; an unguarded set_flag would raise AttributeError
    # on the very runtime this layer targets.
    guards_fp16_flag = 'getattr(trt.BuilderFlag' in build_source
    if not guards_fp16_flag:
        issues.append('build_engine_does_not_guard_the_missing_fp16_builder_flag')
    verifies_precision = hasattr(runtime, 'precision_label_matches_engine') and hasattr(runtime, 'actual_precision')
    if not verifies_precision:
        issues.append('runtime_cannot_prove_a_precision_label_from_engine_dtypes')
    missing = sorted(set(runtime.NUMPY_DTYPES) - set(runtime.TORCH_DTYPES))
    if missing:
        issues.append('numpy_dtype_map_missing:' + ','.join(missing))
    # Behavioural check of the precision rule with a stubbed engine I/O surface: no GPU
    # and no TensorRT needed, and it fails loudly if the rule is ever relaxed back to
    # trusting the requested label. This is the defect that produced a mislabelled
    # evidence set once already, so it is asserted here on every local preflight.
    table = [('fp32', ('FLOAT',), True), ('fp32', ('HALF',), False),
             ('fp32', ('FLOAT', 'HALF'), False), ('fp16', ('HALF',), True),
             ('fp16', ('FLOAT',), False), ('fp16', ('FLOAT', 'HALF'), True),
             ('fp16', (), False), ('fp16', ('BF16',), True)]
    precision_rule = []
    for label, dtypes, expected in table:
        stub = object.__new__(runtime)
        stub.precision = label
        stub.trt = _StubTensorRT()
        stub.io = {'inputs': [{'name': 'in'}], 'outputs': [{'name': 'out'}]}
        stub.tensor_dtypes = {name: dtypes[index % len(dtypes)] for index, name in enumerate(('in', 'out'))} \
            if dtypes else {}
        actual = stub.precision_label_matches_engine()
        precision_rule.append({'label': label, 'engine_dtypes': list(dtypes),
                               'actual_engine_precision': stub.actual_precision(),
                               'label_backed': actual, 'expected': expected})
        if actual is not expected:
            issues.append(f'precision_rule_wrong_for_{label}_over_{list(dtypes)}')
    return {'runtime': runtime.name, 'declared_torch_dtypes': sorted(runtime.TORCH_DTYPES),
            'declared_numpy_dtypes': sorted(runtime.NUMPY_DTYPES),
            'uses_execute_async_v3': 'execute_async_v3' in infer_source,
            'derives_both_dtypes_from_the_engine': '_torch_dtype(' in infer_source
            and '_numpy_dtype(self.input_name)' in infer_source,
            'has_dynamic_input_profile_path': 'create_optimization_profile' in build_source,
            'uses_a_dedicated_cuda_stream': uses_dedicated_stream,
            'guards_missing_fp16_builder_flag': guards_fp16_flag,
            'verifies_precision_against_engine_dtypes': verifies_precision,
            'precision_evidence_rules': {name: list(values) for name, values
                                         in sorted(runtime.PRECISION_EVIDENCE.items())},
            'precision_rule_behaviour': precision_rule,
            'issues': issues, 'ok': not issues,
            'note': ('source inspection plus a stubbed precision-rule check; the dtype mapping, the '
                     'execution path and every measured number are confirmed only by a run on an '
                     'NVIDIA GPU')}


def preflight(arguments, out, environment):
    """Everything except the GPU: prove the harness is correct on the real artifact.

    Returns exit code 0 only when model identity, the canonical contract, sample
    extraction, a CPU decode through the shared decoder and the static TensorRT
    checks all succeed. It says nothing about TensorRT execution, which is why the
    GPU gate below is still mandatory.
    """
    from edge.pipeline import Detector
    from edge.runtimes import CPU, detect_capabilities
    from shared import model_contract

    steps = Step()
    model_record = verify_model(arguments.onnx, arguments.contract_record)
    steps.ok('verify_model_identity', sha256=model_record['sha256'][:16], bytes=model_record['bytes'],
             input=model_record['input_shape'], output=model_record['output_shape'],
             opset=model_record['opset'])
    detector = Detector(arguments.onnx, model_record['sha256'], profile=arguments.contract_profile)
    contract = detector.contract
    steps.ok('canonical_contract', profile=contract.profile, source_count=contract.source_count,
             raw_channels=contract.channels, sources=list(contract.sources),
             mapping_version=model_contract.mapping_version(contract))
    samples, frames = load_samples(arguments)
    steps.ok('load_evaluation_samples', count=samples['count'], source=str(samples['source']))
    counts = []
    for index, frame in enumerate(frames):
        detections, latency = detector.infer(frame)
        counts.append({'sample': samples['samples'][index]['id'], 'detections': int(len(detections.xyxy)),
                       'classes': sorted({int(value) for value in detections.class_id}),
                       'cpu_latency_ms': round(float(latency), 2)})
    steps.ok('cpu_reference_decode', samples=len(frames),
             detections=sum(item['detections'] for item in counts))
    static_checks = tensorrt_static_checks()
    if static_checks['ok']:
        steps.ok('tensorrt_path_static_checks', **{key: static_checks[key] for key in
                 ('uses_execute_async_v3', 'derives_both_dtypes_from_the_engine',
                  'has_dynamic_input_profile_path', 'uses_a_dedicated_cuda_stream',
                  'guards_missing_fp16_builder_flag', 'verifies_precision_against_engine_dtypes')})
    else:
        steps.failed('tensorrt_path_static_checks', ';'.join(static_checks['issues']))
    provider_states = {name: {'provider_state': value['provider_state'], 'available': value['available']}
                       for name, value in detect_capabilities(arguments.onnx).items()
                       if isinstance(value, dict) and 'provider_state' in value}
    if provider_states:
        steps.ok('onnx_provider_states', **provider_states)
    report = {'status': 'PREFLIGHT_OK' if static_checks['ok'] else 'PREFLIGHT_FAILED',
              'captured_at': utc(), 'environment': environment,
              'tensorrt_path': static_checks, 'onnx_provider_states': provider_states,
              'fp16_builder_flag_available': _builder_flag_fp16_available(),
              'model': model_record, 'contract': contract.as_dict(),
              'class_mapping_version': model_contract.mapping_version(contract),
              'samples': samples, 'cpu_reference_decode': counts, 'steps': steps.as_list(),
              'scope': ('validates model identity, canonical contract, sample extraction, the shared '
                        'decoder on CPU and the static TensorRT path checks. It does NOT verify TensorRT '
                        'execution; a real run still requires an NVIDIA GPU.'),
              'next_step': ('python -m scripts.verify_tensorrt_gpu --onnx ... --samples-dir ... --out '
                            'docs/evidence/tensorrt on an NVIDIA host')}
    out.mkdir(parents=True, exist_ok=True)
    (out / 'preflight.json').write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps({'status': report['status'], 'model_sha256': model_record['sha256'],
                      'contract_profile': contract.profile, 'samples': samples['count'],
                      'cpu_detections': counts, 'tensorrt_path_issues': static_checks['issues']},
                     indent=2, default=str))
    return 0 if static_checks['ok'] else FAILED_EXIT


def main(argv=None):
    parser = argparse.ArgumentParser(description='Real NVIDIA GPU TensorRT verification for the VisionOps PPE model.')
    parser.add_argument('--onnx', default='var/model/hansung-p3.onnx')
    parser.add_argument('--fp16-onnx', default=None,
                       help='mixed-FP16 ONNX from scripts/convert_fp16_onnx.py; required for a true FP16 engine '
                            'on TensorRT 11+, where BuilderFlag.FP16 no longer exists')
    parser.add_argument('--contract-record', default='var/model/hansung-p3.json')
    parser.add_argument('--contract-profile', default=None)
    parser.add_argument('--video', default=None)
    parser.add_argument('--frames', default='0,25,50,100,150,200')
    parser.add_argument('--samples-dir', default=None)
    parser.add_argument('--out', default='docs/evidence/tensorrt')
    parser.add_argument('--precisions', default='fp32,fp16')
    parser.add_argument('--reference', default='auto', choices=['auto', 'onnx-cpu', 'onnx-cuda', 'pytorch'])
    parser.add_argument('--pytorch-checkpoint', default=None)
    parser.add_argument('--workspace-bytes', type=int, default=1 << 30)
    parser.add_argument('--warmup', type=int, default=20)
    parser.add_argument('--iterations', type=int, default=100)
    parser.add_argument('--keep-engines', action='store_true')
    parser.add_argument('--preflight', action='store_true',
                       help='validate everything except the GPU (model, contract, samples, CPU decode)')
    parser.add_argument('--engine-dir', default=None)
    parser.add_argument('--trtexec', action='store_true', default=True)
    parser.add_argument('--no-trtexec', dest='trtexec', action='store_false')
    arguments = parser.parse_args(argv)

    out = Path(arguments.out)
    steps = Step()
    environment, capabilities = collect_environment()
    print(json.dumps({key: environment.get(key) for key in
                      ('gpu_names', 'driver', 'cuda', 'tensorrt', 'torch', 'torch_cuda_available')},
                     indent=2, default=str), flush=True)

    # A GPU-free rehearsal of the harness, so a GPU session is not spent debugging it.
    if arguments.preflight:
        return preflight(arguments, out, environment)

    # ---- gate: a GPU and TensorRT are required; no CPU substitution ----------
    blockers = []
    if not environment['gpu_names'] and not environment.get('torch_cuda_available'):
        blockers.append('no_nvidia_gpu_detected')
    if not capabilities['TENSORRT']['available']:
        blockers.append(capabilities['TENSORRT']['reason'] or 'tensorrt_unavailable')
    if environment['nvidia_smi'].get('present') is False:
        blockers.append('nvidia-smi_not_found')
    if blockers:
        out.mkdir(parents=True, exist_ok=True)
        (out / 'environment.json').write_text(json.dumps(environment, indent=2, default=str))
        (out / 'blocked.json').write_text(json.dumps({
            'status': 'BLOCKED_BY_NVIDIA_HARDWARE', 'blockers': blockers, 'captured_at': utc(),
            'hardware': {'gpu_names': environment['gpu_names'], 'driver': environment.get('driver'),
                         'cuda': environment.get('cuda'), 'tensorrt': environment.get('tensorrt'),
                         'torch_cuda_available': environment.get('torch_cuda_available')},
            'attempted': ['nvidia-smi --query-gpu=name,driver_version,memory.total,compute_cap',
                          'torch.cuda.is_available()', 'import tensorrt'],
            'did_not_do': ['no CPU fallback was substituted',
                           'no TensorRT number was estimated or copied from documentation',
                           'no engine file was fabricated'],
            'next_step': 'run the same command on an NVIDIA GPU host (docs/evidence/tensorrt/README.md)',
            'how_to_reproduce': ('python -m scripts.verify_tensorrt_gpu --onnx <onnx> '
                                 '--video <video> --frames 0,25,50,100,150,200 --out docs/evidence/tensorrt'),
            'environment': environment}, indent=2, default=str))
        print('BLOCKED_BY_NVIDIA_HARDWARE: ' + ', '.join(blockers), flush=True)
        return BLOCKED_EXIT

    from edge.pipeline import Detector
    from edge.runtimes import (CPU, CUDA, PROVIDER_LISTED, PROVIDER_UNAVAILABLE, PROVIDER_VERIFIED, TENSORRT,
                               RuntimeUnavailable, TensorRTRuntime, check_provider, create_runtime)
    from shared import model_contract
    from shared.model_contract import letterbox

    # ---- model identity -----------------------------------------------------
    try:
        model_record = verify_model(arguments.onnx, arguments.contract_record)
    except SystemExit as exc:
        steps.failed('verify_model_identity', str(exc))
        out.mkdir(parents=True, exist_ok=True)
        (out / 'blocked.json').write_text(json.dumps({'status': 'MODEL_ARTIFACT_MISMATCH', 'reason': str(exc),
                                                      'environment': environment}, indent=2, default=str))
        return FAILED_EXIT
    environment['onnx_sha256'] = model_record['sha256']
    environment['onnx_bytes'] = model_record['bytes']
    steps.ok('verify_model_identity', sha256=model_record['sha256'][:16], bytes=model_record['bytes'],
             input=model_record['input_shape'], output=model_record['output_shape'],
             opset=model_record['opset'])

    digest = model_record['sha256']
    contract = Detector(arguments.onnx, digest, profile=arguments.contract_profile).contract
    environment['contract'] = contract.as_dict()
    environment['class_mapping_version'] = model_contract.mapping_version(contract)
    steps.ok('canonical_contract', profile=contract.profile, source_count=contract.source_count,
             raw_channels=contract.channels, mapping={str(k): v for k, v in sorted(contract.mapping.items())},
             mapping_version=environment['class_mapping_version'])

    samples, frames = load_samples(arguments)
    ids = [item['id'] for item in samples['samples']]
    (out / 'samples.json').parent.mkdir(parents=True, exist_ok=True)
    (out / 'samples.json').write_text(json.dumps(samples, indent=2, default=str))
    steps.ok('load_evaluation_samples', count=samples['count'], source=str(samples['source']))

    # ---- mixed-FP16 ONNX, if one was supplied -------------------------------
    # TensorRT 11 removed BuilderFlag.FP16, so a true FP16 engine needs a strongly-typed
    # graph. The conversion itself is qualified here, before any engine is built: an
    # export that merely succeeded is not evidence that the model still behaves.
    fp16_record, graph_parity = None, None
    if arguments.fp16_onnx:
        try:
            fp16_record = verify_fp16_graph(arguments.fp16_onnx, model_record, arguments.contract_record)
        except SystemExit as exc:
            steps.failed('verify_fp16_graph', str(exc))
            (out / 'mixed_fp16_graph.json').parent.mkdir(parents=True, exist_ok=True)
            (out / 'mixed_fp16_graph.json').write_text(json.dumps(
                {'status': 'FAILED', 'reason': str(exc), 'path': str(arguments.fp16_onnx)}, indent=2))
            return FAILED_EXIT
        steps.ok('verify_fp16_graph', sha256=fp16_record['sha256'][:16], bytes=fp16_record['bytes'],
                 input_dtype=fp16_record['input_dtype'], class_metadata_restored=True,
                 derived_from=model_record['sha256'][:16])
        (out / 'mixed_fp16_graph.json').write_text(json.dumps(fp16_record, indent=2, default=str))
        graph_parity = onnx_graph_parity(arguments.onnx, model_record, arguments.fp16_onnx, fp16_record,
                                         frames, ids, contract, PARITY_SCORE_THRESHOLD)
        (out / 'fp32_onnx_vs_mixed_fp16_onnx.json').write_text(json.dumps(graph_parity, indent=2, default=str))
        if graph_parity['passed']:
            steps.ok('fp32_vs_mixed_fp16_onnx_parity', matched=graph_parity['matched'],
                     of=graph_parity['reference_detections'], min_iou=graph_parity['min_iou'],
                     max_confidence_delta=graph_parity['max_confidence_delta'])
        else:
            steps.failed('fp32_vs_mixed_fp16_onnx_parity',
                         f"conversion changed behaviour: matched {graph_parity['matched']}/"
                         f"{graph_parity['reference_detections']} at threshold {PARITY_SCORE_THRESHOLD}")

    # ---- accelerator provider probes ---------------------------------------
    # A listed provider is not an operational one: on the GPU qualification host the CUDA
    # provider was listed while failing to load because its CUDA library did not match the
    # driver runtime. Every accelerator is therefore probed with a real session and a real
    # inference before it is used or reported as usable.
    cuda_report = check_provider(arguments.onnx, 'CUDAExecutionProvider')
    provider_probes = {CUDA: cuda_report}
    if cuda_report['state'] == PROVIDER_VERIFIED:
        steps.ok('provider_probe_ONNX_CUDA', provider=cuda_report['provider'], state=cuda_report['state'],
                 reason=cuda_report['reason'])
    else:
        steps.failed('provider_probe_ONNX_CUDA',
                     f"CUDAExecutionProvider {cuda_report['state']}: {cuda_report.get('reason')}")
    trt_provider_listed = 'TensorrtExecutionProvider' in capabilities[CPU]['providers']
    provider_probes[TENSORRT] = {
        'provider': 'TensorrtExecutionProvider',
        'state': PROVIDER_LISTED if trt_provider_listed else PROVIDER_UNAVAILABLE,
        'listed': trt_provider_listed,
        'reason': ('the ONNX Runtime TensorRT execution provider is not used for qualification; '
                   'edge.runtimes.TensorRTRuntime is exercised directly so the engine I/O dtypes can be read')}
    environment['onnx_provider_probes'] = provider_probes
    cuda_usable = cuda_report['state'] == PROVIDER_VERIFIED

    # ---- reference runtime --------------------------------------------------
    reference_name = arguments.reference
    if reference_name == 'auto':
        reference_name = 'onnx-cuda' if cuda_usable else 'onnx-cpu'
    reference_detector, reference_bench = None, None
    if reference_name in ('onnx-cpu', 'onnx-cuda'):
        runtime_name = CPU if reference_name == 'onnx-cpu' else CUDA
        try:
            reference_detector = Detector(arguments.onnx, digest, runtime=runtime_name)
            steps.ok('reference_runtime', runtime=reference_detector.runtime_name)
        except RuntimeUnavailable as exc:
            steps.skipped('reference_runtime', f'{reference_name} unavailable: {exc}')
            if reference_name == 'onnx-cuda':
                (out / 'onnx_cuda_baseline.json').parent.mkdir(parents=True, exist_ok=True)
                (out / 'onnx_cuda_baseline.json').write_text(json.dumps(
                    {'status': 'NOT VERIFIED IN THIS ENVIRONMENT', 'reason': str(exc)}, indent=2))
            reference_detector = Detector(arguments.onnx, digest, runtime=CPU)
            steps.ok('reference_runtime_substitute', runtime=reference_detector.runtime_name,
                     note='ONNX CPU used as the parity reference; this is a reference, not a TensorRT result')
    else:
        if not arguments.pytorch_checkpoint:
            raise SystemExit('--reference pytorch requires --pytorch-checkpoint')
        reference_detector = load_pytorch_reference(arguments.pytorch_checkpoint, contract)
        steps.ok('reference_runtime', runtime=reference_detector.runtime_name)

    reference_tensor, _ = letterbox(frames[0], tuple(contract.input_shape))
    reference_bench = benchmark_raw(reference_detector.runtime, reference_tensor,
                                    arguments.warmup, arguments.iterations)
    reference_bench['end_to_end'] = benchmark_detector(reference_detector, frames, arguments.warmup,
                                                       arguments.iterations)
    reference_bench.update({'runtime': reference_detector.runtime_name, 'precision': 'fp32',
                            'role': 'reference'})
    steps.ok('reference_benchmark', runtime=reference_detector.runtime_name, p50_ms=reference_bench['p50_ms'],
             fps_model_only=reference_bench['throughput_fps_model_only'])

    # ---- contract semantics -------------------------------------------------
    semantics = contract_semantics(reference_detector, frames, ids, contract)
    (out / 'contract-semantics.json').write_text(json.dumps(semantics, indent=2, default=str))
    steps.ok('canonical_contract_semantics', boxes=semantics['boxes_above_threshold'],
             would_differ=semantics['argmax_interpretations_that_would_differ'])

    # ---- TensorRT per precision --------------------------------------------
    engine_root = Path(arguments.engine_dir) if arguments.engine_dir \
        else Path(tempfile.mkdtemp(prefix='visionops-engines-'))
    engine_root.mkdir(parents=True, exist_ok=True)
    gpu_name = environment.get('torch_device_name') or (environment['gpu_names'][0] if environment['gpu_names'] else None)
    results, table, unbacked = {}, [], []
    for precision in [value.strip().lower() for value in arguments.precisions.split(',') if value.strip()]:
        precision_digest = model_record['sha256']
        model_for_precision = arguments.onnx
        if precision == 'fp16' and arguments.fp16_onnx and fp16_record:
            model_for_precision, precision_digest = arguments.fp16_onnx, fp16_record['sha256']
        if precision == 'fp16' and model_for_precision == arguments.onnx and not _builder_flag_fp16_available():
            # This is the exact defect the superseded record grew out of: an FP32 graph built
            # with an fp16 label. With no BuilderFlag.FP16 the label would be pure fiction, so
            # the precision is refused and the fix is named instead of a number being emitted.
            record = {'status': 'NOT VERIFIED',
                      'reason': 'fp16_requires_a_mixed_precision_onnx_graph',
                      'detail': ('this TensorRT build has no BuilderFlag.FP16, so precision must live in the '
                                 'graph: run scripts/convert_fp16_onnx.py then pass --fp16-onnx'),
                      'would_have_been': 'a logical fp16 label over an FP32 graph, which is not FP16 evidence'}
            steps.skipped('build_engine_fp16', record['detail'])
            results['fp16'] = record
            (out / 'parity_fp16.json').write_text(json.dumps(record, indent=2))
            continue
        engine_path = engine_root / f'visionops-{precision}.engine'
        try:
            runtime = create_runtime(TENSORRT, model_for_precision, contract, precision=precision,
                                     engine_path=str(engine_path), workspace_bytes=arguments.workspace_bytes)
            started = time.perf_counter()
            runtime.build_engine(workspace_bytes=arguments.workspace_bytes)
            build_seconds = round(time.perf_counter() - started, 2)
            runtime.load()
        except RuntimeUnavailable as exc:
            steps.failed(f'build_engine_{precision}', str(exc))
            results[precision] = {'status': 'FAILED', 'reason': str(exc)}
            (out / f'parity_{precision}.json').write_text(json.dumps(results[precision], indent=2))
            continue
        engine_record = {**runtime.describe(), 'build_seconds': build_seconds}
        # The engine's own tensor dtypes decide whether the label is real. A requested
        # precision is never evidence on its own.
        label_backed = runtime.precision_label_matches_engine()
        steps.ok(f'build_engine_{precision}', seconds=build_seconds, engine_bytes=engine_record['engine_bytes'],
                 engine_sha256=(engine_record['engine_sha256'] or '')[:16], precision_label=precision,
                 actual_engine_precision=runtime.actual_precision(),
                 engine_dtypes=engine_record.get('engine_dtypes'),
                 onnx_input_dtype=engine_record.get('onnx_input_dtype'), label_backed=label_backed)
        if not label_backed:
            steps.failed(f'precision_label_{precision}',
                         f'requested {precision} but the engine declares {runtime.actual_precision()}: '
                         f'{engine_record.get("engine_dtypes")}')

        engine_detector = Detector(model_for_precision, precision_digest, contract=contract,
                                   prebuilt_runtime=runtime)
        parity_result = parity(reference_detector, engine_detector, frames, ids, PARITY_SCORE_THRESHOLD)
        (out / f'parity_{precision}.json').write_text(json.dumps(
            {'precision': precision, 'precision_label': precision,
             'actual_engine_precision': runtime.actual_precision(),
             'label_backed_by_engine_dtypes': label_backed,
             'reference_runtime': reference_detector.runtime_name,
             'engine': engine_record, **parity_result}, indent=2, default=str))
        steps.ok(f'parity_{precision}', matched=parity_result['matched'], of=parity_result['reference_detections'],
                 min_iou=parity_result['min_iou'], mean_iou=parity_result['mean_iou'],
                 max_confidence_delta=parity_result['max_confidence_delta'], passed=parity_result['passed'])

        engine_tensor, _ = letterbox(frames[0], tuple(contract.input_shape))
        bench = benchmark_raw(runtime, engine_tensor, arguments.warmup, arguments.iterations)
        bench['end_to_end'] = benchmark_detector(engine_detector, frames, arguments.warmup, arguments.iterations)
        status = 'VERIFIED' if (parity_result['passed'] and label_backed) else (
            'PARITY_FAILED' if not parity_result['passed']
            else 'NOT VERIFIED - LABEL_NOT_BACKED_BY_ENGINE_DTYPES')
        results[precision] = {'status': status, 'precision_label': precision,
                              'actual_engine_precision': runtime.actual_precision(),
                              'label_backed_by_engine_dtypes': label_backed,
                              'engine': engine_record, 'parity': parity_result, 'benchmark': bench}
        steps.ok(f'benchmark_{precision}', p50_ms=bench['p50_ms'], p95_ms=bench['p95_ms'], mean_ms=bench['mean_ms'],
                 fps_model_only=bench['throughput_fps_model_only'])
        row = {'runtime': 'TENSORRT', 'gpu': gpu_name, 'precision': precision,
               'input': list(contract.input_shape), 'p50_ms': bench['p50_ms'], 'p95_ms': bench['p95_ms'],
               'mean_ms': bench['mean_ms'], 'throughput_fps_model_only': bench['throughput_fps_model_only'],
               'gpu_memory': 'NOT MEASURED'}
        if label_backed:
            table.append({**row, 'actual_engine_precision': runtime.actual_precision()})
        else:
            # Measured, recorded, and kept out of the authoritative table: a number whose
            # label the engine does not support must not be quoted as that precision.
            unbacked.append({**row, 'actual_engine_precision': runtime.actual_precision(),
                             'engine_dtypes': engine_record.get('engine_dtypes'),
                             'parity': parity_result, 'benchmark': bench,
                             'excluded_from_authoritative_table': True,
                             'reason': 'the engine does not declare the precision this label claims'})
        if arguments.trtexec:
            results[precision]['trtexec'] = run_trtexec(engine_path, out, arguments.iterations)
            steps.ok(f'trtexec_{precision}', trtexec_status=results[precision]['trtexec']['status'],
                     precision_label=precision)
        if not arguments.keep_engines:
            try:
                engine_path.unlink()
                engine_record['engine_deleted_after_run'] = True
            except OSError:
                engine_record['engine_deleted_after_run'] = False

    table.insert(0, {'runtime': reference_detector.runtime_name, 'precision': 'fp32', 'gpu': gpu_name,
                     'input': list(contract.input_shape), 'p50_ms': reference_bench['p50_ms'],
                     'p95_ms': reference_bench['p95_ms'], 'mean_ms': reference_bench['mean_ms'],
                     'throughput_fps_model_only': reference_bench['throughput_fps_model_only'],
                     'gpu_memory': 'NOT MEASURED'})

    # ---- ONNX Runtime CUDA baseline (only when the provider really works) ---
    if cuda_usable:
        try:
            cuda_detector = Detector(arguments.onnx, digest, runtime=CUDA)
            cuda_tensor, _ = letterbox(frames[0], tuple(contract.input_shape))
            cuda_bench = benchmark_raw(cuda_detector.runtime, cuda_tensor, arguments.warmup, arguments.iterations)
            (out / 'onnx_cuda_baseline.json').write_text(json.dumps(
                {'status': 'VERIFIED', 'runtime': CUDA, 'precision': 'fp32', 'provider_probe': cuda_report,
                 'benchmark': cuda_bench}, indent=2, default=str))
            table.insert(1, {'runtime': 'ONNX_CUDA', 'precision': 'fp32', 'gpu': gpu_name,
                             'input': list(contract.input_shape), 'p50_ms': cuda_bench['p50_ms'],
                             'p95_ms': cuda_bench['p95_ms'], 'mean_ms': cuda_bench['mean_ms'],
                             'throughput_fps_model_only': cuda_bench['throughput_fps_model_only'],
                             'gpu_memory': 'NOT MEASURED'})
            steps.ok('onnx_cuda_baseline', p50_ms=cuda_bench['p50_ms'],
                     fps_model_only=cuda_bench['throughput_fps_model_only'])
        except RuntimeUnavailable as exc:
            steps.failed('onnx_cuda_baseline', str(exc))
    else:
        (out / 'onnx_cuda_baseline.json').write_text(json.dumps(
            {'status': 'NOT VERIFIED IN THIS ENVIRONMENT',
             'meaning': ('no ONNX Runtime CUDA baseline was produced; a listed provider was not treated as an '
                         'operational one'),
             'provider_probe': cuda_report}, indent=2, default=str))
        table.insert(1, {'runtime': 'ONNX_CUDA', 'precision': 'fp32', 'p50_ms': 'NOT VERIFIED',
                         'p95_ms': 'NOT VERIFIED', 'mean_ms': 'NOT VERIFIED',
                         'throughput_fps_model_only': 'NOT VERIFIED', 'gpu_memory': 'NOT VERIFIED',
                         'note': (f"ONNX Runtime CUDA — NOT VERIFIED IN THIS ENVIRONMENT "
                                  f"(provider state {cuda_report['state']}: {cuda_report.get('reason')})")})

    verified = any(result.get('status') == 'VERIFIED' for result in results.values())
    # A blocked record from an earlier attempt must never sit next to a verified
    # result in the same evidence directory: that is exactly how a stale status
    # gets read as a current one.
    stale = []
    for name in ('blocked.json', 'preflight.json'):
        candidate = out / name
        if candidate.exists():
            candidate.unlink()
            stale.append(name)
    summary = {
        'status': ('VERIFIED ON NVIDIA GPU — PHYSICAL JETSON STILL NOT VERIFIED' if verified else 'FAILED'),
        'captured_at': utc(), 'environment': environment, 'model': model_record,
        'contract': contract.as_dict(), 'class_mapping_version': environment['class_mapping_version'],
        'samples': samples, 'contract_semantics': semantics, 'benchmark_table': table,
        'precisions': results,
        'mixed_fp16_graph': fp16_record,
        'fp32_vs_mixed_fp16_onnx_parity': graph_parity,
        'non_authoritative_precisions': unbacked,
        'onnx_provider_probes': provider_probes,
        'precision_policy': {
            'rule': ('a precision label is only VERIFIED when the engine declares matching tensor dtypes; '
                     'the requested label is never evidence'),
            'evidence_rule': {name: list(values)
                              for name, values in sorted(TensorRTRuntime.PRECISION_EVIDENCE.items())},
            'fp16_builder_flag_available': _builder_flag_fp16_available(),
            'fp16_requires_a_mixed_precision_graph': fp16_record is not None,
            'excluded_from_authoritative_table': [row['precision'] for row in unbacked]},
        'model_only_theoretical_throughput': {
            'formula': '1000 / mean_latency_ms',
            'values': {precision: result['benchmark']['throughput_fps_model_only']
                       for precision, result in results.items()
                       if result.get('benchmark') and result.get('status') == 'VERIFIED'},
            'caveat': ('MODEL-ONLY THEORETICAL THROUGHPUT. It excludes RTSP/network, decode, preprocessing, '
                       'tracking, temporal analysis, postprocessing and event handling, and is therefore higher '
                       'than achievable end-to-end camera FPS.')},
        'int8': {'status': 'NOT ATTEMPTED',
                 'reason': 'REAL CALIBRATION/VALIDATION REQUIRED — no representative calibration dataset exists '
                           'in this repository; FP16 is sufficient for this verification'},
        'engine_policy': {'plan': ENGINE_PLAN, 'engines_retained': bool(arguments.keep_engines),
                          'note': 'engines are target-runtime and hardware specific and are never committed'},
        'superseded_records_removed': stale,
        'not_verified': ['physical NVIDIA Jetson', 'JetPack on physical hardware',
                         'Jetson thermal/power/NVDEC behaviour', '10K physical Jetson fleet',
                         'model quality (no labeled dataset)'],
        'steps': steps.as_list()}
    (out / 'environment.json').write_text(json.dumps(environment, indent=2, default=str))
    (out / 'benchmark.json').write_text(json.dumps(
        {'captured_at': utc(), 'environment': environment, 'table': table,
         'reference': reference_bench, 'precisions': results,
         'non_authoritative_precisions': unbacked, 'precision_policy': summary['precision_policy'],
         'onnx_provider_probes': provider_probes,
         'model_only_theoretical_throughput': summary['model_only_theoretical_throughput']},
        indent=2, default=str))
    # Core evidence is written before anything is derived from it, so a failure in a
    # markdown renderer can never cost the measurements themselves.
    (out / 'tensorrt-verification.json').write_text(json.dumps(summary, indent=2, default=str))
    try:
        # The follow-up documentation work is generated from this run instead of being
        # left to memory: only a verified precision authorises the status flip.
        patch = write_status_patch(out, environment, table, results, model_record)
        steps.ok('write_status_patch', authorised=patch['authorised'],
                 verified_precisions=patch['verified_precisions'],
                 located=sum(1 for edit in patch['edits'] if edit['status'] == 'located'),
                 of=len(patch['edits']))
        summary['status_patch'] = {'path': 'status-patch.json', 'authorised': patch['authorised'],
                                   'verified_precisions': patch['verified_precisions'],
                                   'edits_located': sum(1 for edit in patch['edits']
                                                        if edit['status'] == 'located'),
                                   'edits_total': len(patch['edits'])}
        summary['gpu_run_result'] = write_gpu_run_result(out, summary, environment, table, results, patch)
    except Exception as exc:  # noqa: BLE001 - evidence already on disk must survive this
        steps.failed('write_status_patch', f'{type(exc).__name__}: {exc}')
        summary['status_patch'] = {'authorised': None, 'error': f'{type(exc).__name__}: {exc}',
                                   'note': 'measurements in tensorrt-verification.json and benchmark.json unaffected'}
    summary['evidence_files'] = sorted(path.name for path in out.iterdir() if path.is_file())
    (out / 'tensorrt-verification.json').write_text(json.dumps(summary, indent=2, default=str))
    print(json.dumps({'status': summary['status'], 'table': table,
                      'evidence_files': summary['evidence_files']}, indent=2, default=str))
    return 0 if verified else FAILED_EXIT


def load_pytorch_reference(checkpoint, contract):
    """Raw PyTorch forward pass decoded by the same canonical contract."""
    import supervision as sv
    import torch
    from ultralytics import YOLO

    from shared.model_contract import canonical_detections, letterbox

    class _PytorchReference:
        runtime_name = 'PYTORCH_CUDA'

        def __init__(self):
            self.module = YOLO(str(checkpoint)).model.float().eval()
            self.torch = torch
            self.contract = contract

            class _Runtime:
                def __init__(self, parent):
                    self.parent = parent

                def infer(self, tensor):
                    module = self.parent.module
                    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
                    with torch.no_grad():
                        output = module(torch.from_numpy(np.ascontiguousarray(tensor)).to(device))
                    raw = output[0] if isinstance(output, (list, tuple)) else output
                    return raw.detach().cpu().numpy()

            self.runtime = _Runtime(self)

        def infer(self, frame, score_threshold=None):
            tensor, geometry = letterbox(frame, tuple(contract.input_shape))
            raw = self.runtime.infer(tensor)
            boxes, confidence, class_id = canonical_detections(
                raw, contract, contract.score_threshold if score_threshold is None else score_threshold,
                geometry, contract.nms_iou)
            if not len(boxes):
                return sv.Detections.empty(), 0.0
            return sv.Detections(xyxy=boxes, confidence=confidence, class_id=class_id), 0.0

    return _PytorchReference()


if __name__ == '__main__':
    sys.exit(main())
