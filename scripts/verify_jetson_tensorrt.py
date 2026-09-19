"""Build and qualify TensorRT engines **on the target device**, then measure them.

An engine is hardware- and runtime-specific. A plan built on a Tesla T4 is not
valid on Jetson, so nothing here is ever copied, downloaded or reused from another
host: every engine is built from the ONNX graph on the machine this script runs on,
measured, and then deleted. The evidence references it by SHA-256 and size instead.

Reuses `scripts/verify_tensorrt_gpu.py` for the parts that must be identical to the
T4 qualification - model identity, sample loading, the canonical decoder, the parity
algorithm and the timing method - so a Jetson number and a T4 number are produced by
the same code and are legitimately comparable.

No precision claim is taken from a label. After `load()` the engine's own declared
tensor dtypes are read, and a precision the engine does not back is recorded as
`NOT VERIFIED - LABEL_NOT_BACKED_BY_ENGINE_DTYPES` and kept out of the table.

Exit codes
    0  at least one precision qualified (parity + benchmark, label backed by dtypes)
    3  BLOCKED_BY_NVIDIA_HARDWARE (no GPU / no TensorRT / no torch device buffers)
    4  ran but a precision failed parity or the benchmark

    python3 -m scripts.verify_jetson_tensorrt \
        --onnx model/hansung-p3.onnx --fp16-onnx model/hansung-p3-fp16.onnx \
        --samples-dir samples --precisions fp32,fp16 --out evidence/jetson
"""
import argparse
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Shared with the T4 qualification: same identity check, same loader, same decoder,
# same parity algorithm, same timing method. Divergence here would make the two
# result sets incomparable, which is the whole point of the comparison document.
from scripts.verify_tensorrt_gpu import (  # noqa: E402
    BLOCKED_EXIT, FAILED_EXIT, PARITY_SCORE_THRESHOLD, Step, benchmark_raw, contract_semantics,
    git_commit, load_samples, parity, sha256_file, utc, verify_fp16_graph, verify_model)

from scripts.detect_jetson_environment import detect as detect_jetson  # noqa: E402


def collect_environment():
    """Device facts, from the device. Absent values stay None."""
    jetson = detect_jetson()
    environment = {'captured_at': utc(), 'host': jetson['host'], 'os_release': jetson['os_release'],
                   'jetson': jetson['jetson'], 'physical_jetson': jetson['physical_jetson'],
                   'jetson_state': jetson['state'], 'jetson_verdict': jetson['verdict'],
                   'versions': jetson['versions'], 'tools': jetson['tools'],
                   'power_and_clocks': jetson['power_and_clocks'],
                   'software': dict(jetson['software']),
                   'not_measured': jetson['not_measured'],
                   'git': dict(zip(('commit', 'working_tree'), git_commit()))}
    try:
        import tensorrt as trt
        environment['tensorrt'] = trt.__version__
    except Exception as exc:  # noqa: BLE001
        environment['tensorrt'] = f'unavailable: {type(exc).__name__}'
    try:
        import torch
        environment['torch'] = torch.__version__
        environment['torch_cuda_available'] = bool(torch.cuda.is_available())
        if torch.cuda.is_available():
            environment['torch_device_name'] = torch.cuda.get_device_name(0)
            properties = torch.cuda.get_device_properties(0)
            environment['torch_total_memory_bytes'] = int(properties.total_memory)
            environment['torch_compute_capability'] = f'{properties.major}.{properties.minor}'
    except Exception as exc:  # noqa: BLE001
        environment.setdefault('torch', f'unavailable: {type(exc).__name__}')
        environment['torch_cuda_available'] = False
    environment['cudnn'] = environment['versions'].get('cudnn')
    return environment, jetson


def blocker_report(out, environment, blockers, next_step):
    out.mkdir(parents=True, exist_ok=True)
    (out / 'jetson_environment.json').write_text(json.dumps(environment, indent=2, default=str))
    report = {'status': 'BLOCKED_BY_NVIDIA_HARDWARE', 'blockers': blockers, 'captured_at': utc(),
              'hardware': {'jetson_state': environment.get('jetson_state'),
                           'architecture': environment['host']['architecture'],
                           'tensorrt': environment.get('tensorrt'), 'torch': environment.get('torch'),
                           'torch_cuda_available': environment.get('torch_cuda_available')},
              'did_not_do': ['no engine was copied from another host',
                             'no CPU number was substituted for a TensorRT result',
                             'no precision label was accepted without engine tensor dtypes'],
              'next_step': next_step, 'environment': environment}
    (out / 'blocked.json').write_text(json.dumps(report, indent=2, default=str))
    print('BLOCKED_BY_NVIDIA_HARDWARE: ' + ', '.join(blockers), flush=True)
    return BLOCKED_EXIT


def main(argv=None):
    parser = argparse.ArgumentParser(description='Build and qualify TensorRT engines on this device.')
    parser.add_argument('--onnx', default='model/hansung-p3.onnx')
    parser.add_argument('--fp16-onnx', default=None,
                       help='mixed-FP16 ONNX (ModelOpt) on this device; required for true FP16 on TensorRT 11+')
    parser.add_argument('--contract-record', default='model/hansung-p3.json')
    parser.add_argument('--samples-dir', default='samples')
    parser.add_argument('--video', default=None)
    parser.add_argument('--frames', default='0,25,50,100,150,200')
    parser.add_argument('--precisions', default='fp32,fp16')
    parser.add_argument('--warmup', type=int, default=20)
    parser.add_argument('--iterations', type=int, default=100)
    parser.add_argument('--workspace-bytes', type=int, default=1 << 30)
    parser.add_argument('--out', default='evidence/jetson')
    parser.add_argument('--engine-dir', default=None)
    parser.add_argument('--keep-engines', action='store_true',
                       help='leave engines on disk; they are device specific and must not be shipped')
    parser.add_argument('--require-jetson', action='store_true',
                       help='refuse to run unless this host is a confirmed physical Jetson')
    arguments = parser.parse_args(argv)

    out = Path(arguments.out)
    out.mkdir(parents=True, exist_ok=True)
    steps = Step()
    environment, jetson = collect_environment()
    print(json.dumps({key: environment.get(key) for key in
                      ('jetson_state', 'physical_jetson', 'tensorrt', 'torch', 'torch_cuda_available')},
                     indent=2, default=str), flush=True)

    if arguments.require_jetson and not jetson['physical_jetson']:
        return blocker_report(out, environment, [f"not_a_physical_jetson:{jetson['state']}"],
                             'run this on the Jetson itself (scripts/verify_physical_jetson.py enforces it)')

    # ---- gate: a device that can build engines, or an honest blocker ----------
    blockers = []
    if not environment.get('torch_cuda_available'):
        blockers.append('no_cuda_device_for_tensorrt_buffers')
    if not str(environment.get('tensorrt', '')).replace('.', '').isdigit():
        blockers.append('tensorrt_unavailable')
    if blockers:
        return blocker_report(out, environment, blockers,
                             'install the JetPack-provided CUDA/TensorRT and a CUDA-enabled torch; do not '
                             'pip-upgrade the NVIDIA stack (see RUN_ON_JETSON.md)')

    from edge.pipeline import Detector
    from edge.runtimes import CPU, TENSORRT, RuntimeUnavailable, create_runtime
    from shared import model_contract
    from shared.model_contract import letterbox

    # ---- model identity ------------------------------------------------------
    try:
        model_record = verify_model(arguments.onnx, arguments.contract_record)
    except SystemExit as exc:
        steps.failed('verify_model_identity', str(exc))
        (out / 'blocked.json').write_text(json.dumps({'status': 'MODEL_ARTIFACT_MISMATCH', 'reason': str(exc),
                                                      'environment': environment}, indent=2, default=str))
        return FAILED_EXIT
    environment['onnx_sha256'] = model_record['sha256']
    environment['onnx_bytes'] = model_record['bytes']
    steps.ok('verify_model_identity', sha256=model_record['sha256'][:16], bytes=model_record['bytes'],
             input=model_record['input_shape'], output=model_record['output_shape'],
             opset=model_record['opset'])

    digest = model_record['sha256']
    contract = Detector(arguments.onnx, digest, profile=None).contract
    environment['contract'] = contract.as_dict()
    environment['class_mapping_version'] = model_contract.mapping_version(contract)
    steps.ok('canonical_contract', profile=contract.profile, source_count=contract.source_count,
             raw_channels=contract.channels, mapping={str(k): v for k, v in sorted(contract.mapping.items())},
             mapping_version=environment['class_mapping_version'])

    fp16_record = None
    if arguments.fp16_onnx:
        try:
            fp16_record = verify_fp16_graph(arguments.fp16_onnx, model_record, arguments.contract_record)
            steps.ok('verify_fp16_graph', sha256=fp16_record['sha256'][:16],
                     input_dtype=fp16_record['input_dtype'],
                     class_metadata_restored=fp16_record['class_metadata_restored'])
        except SystemExit as exc:
            steps.failed('verify_fp16_graph', str(exc))
            fp16_record = None

    samples, frames = load_samples(arguments)
    ids = [item['id'] for item in samples['samples']]
    (out / 'samples.json').write_text(json.dumps(samples, indent=2, default=str))
    steps.ok('load_evaluation_samples', count=samples['count'], source=str(samples['source']))

    # ---- reference runtime --------------------------------------------------
    # ONNX Runtime CPU is the reference because it is an independent implementation.
    # If it is not installed on this device, the fallback is stated as what it is
    # rather than silently presented as an independent reference.
    reference_detector, reference_scope = None, None
    try:
        reference_detector = Detector(arguments.onnx, digest, runtime=CPU)
        reference_scope = 'ONNX_CPU (independent implementation)'
        steps.ok('reference_runtime', runtime=reference_detector.runtime_name)
    except RuntimeUnavailable as exc:
        steps.skipped('reference_runtime', f'onnx-cpu unavailable: {exc}')
        reference_scope = 'TENSORRT_FP32_ON_THIS_DEVICE (not an independent implementation)'
    reference_bench = None
    if reference_detector is not None:
        reference_tensor, _ = letterbox(frames[0], tuple(contract.input_shape))
        reference_bench = benchmark_raw(reference_detector.runtime, reference_tensor,
                                        arguments.warmup, arguments.iterations)
        reference_bench.update({'runtime': reference_detector.runtime_name, 'precision': 'fp32',
                                'role': 'reference'})
        steps.ok('reference_benchmark', runtime=reference_detector.runtime_name,
                 p50_ms=reference_bench['p50_ms'], fps_model_only=reference_bench['throughput_fps_model_only'])

    if reference_detector is not None:
        semantics = contract_semantics(reference_detector, frames, ids, contract)
        (out / 'contract-semantics.json').write_text(json.dumps(semantics, indent=2, default=str))
        steps.ok('canonical_contract_semantics', boxes=semantics['boxes_above_threshold'],
                 would_differ=semantics['argmax_interpretations_that_would_differ'])

    # ---- per-precision engine build, parity, benchmark ----------------------
    engine_root = Path(arguments.engine_dir) if arguments.engine_dir \
        else Path(tempfile.mkdtemp(prefix='visionops-jetson-engines-'))
    engine_root.mkdir(parents=True, exist_ok=True)
    results, table, unbacked = {}, [], []
    fp32_engine_detector = None

    for precision in [value.strip().lower() for value in arguments.precisions.split(',') if value.strip()]:
        model_for_precision, precision_digest = arguments.onnx, model_record['sha256']
        if precision == 'fp16' and fp16_record:
            model_for_precision, precision_digest = arguments.fp16_onnx, fp16_record['sha256']
        elif precision == 'fp16':
            record = {'status': 'NOT VERIFIED', 'reason': 'fp16_requires_a_mixed_precision_onnx_graph',
                      'detail': ('TensorRT 11 removed BuilderFlag.FP16, so a true FP16 engine needs a '
                                 'strongly-typed graph; supply --fp16-onnx from scripts/convert_fp16_onnx.py')}
            steps.skipped('build_engine_fp16', record['detail'])
            results['fp16'] = record
            precision_out = out / 'fp16'
            precision_out.mkdir(parents=True, exist_ok=True)
            (precision_out / 'parity.json').write_text(json.dumps(record, indent=2))
            continue

        precision_out = out / precision
        precision_out.mkdir(parents=True, exist_ok=True)
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
            (precision_out / 'parity.json').write_text(json.dumps(results[precision], indent=2))
            continue

        engine_record = {**runtime.describe(), 'build_seconds': build_seconds,
                         'built_on': 'this_device', 'source_onnx_sha256': sha256_file(model_for_precision),
                         'built_on_sha256': sha256_file(model_for_precision),
                         'device': environment.get('torch_device_name'),
                         'jetson_state': environment.get('jetson_state'),
                         'tensorrt': environment.get('tensorrt'),
                         'portability': ('device and TensorRT-version specific; never shipped, never reused '
                                         'from another host')}
        (precision_out / 'engine-metadata.json').write_text(json.dumps(engine_record, indent=2, default=str))
        label_backed = runtime.precision_label_matches_engine()
        steps.ok(f'build_engine_{precision}', seconds=build_seconds,
                 engine_bytes=engine_record['engine_bytes'], engine_dtypes=engine_record.get('engine_dtypes'),
                 precision_label=precision, actual_engine_precision=runtime.actual_precision(),
                 label_backed=label_backed)
        if not label_backed:
            steps.failed(f'precision_label_{precision}',
                         f'requested {precision} but the engine declares {runtime.actual_precision()}')

        engine_detector = Detector(model_for_precision, precision_digest, contract=contract,
                                   prebuilt_runtime=runtime)
        if precision == 'fp32':
            fp32_engine_detector = engine_detector
        if reference_detector is None and fp32_engine_detector is not None and precision != 'fp32':
            reference_detector = fp32_engine_detector

        engine_tensor, _ = letterbox(frames[0], tuple(contract.input_shape))
        bench = benchmark_raw(runtime, engine_tensor, arguments.warmup, arguments.iterations)
        bench['end_to_end'] = benchmark_pipeline_latency(engine_detector, frames, arguments.warmup,
                                                         arguments.iterations)

        if reference_detector is not None and reference_detector is not engine_detector:
            parity_result = parity(reference_detector, engine_detector, frames, ids, PARITY_SCORE_THRESHOLD)
            parity_result['reference_scope'] = reference_scope
        else:
            parity_result = {'status': 'NOT VERIFIED', 'reason': 'no_independent_reference_runtime_on_this_device',
                             'reference_scope': reference_scope,
                             'criterion': 'parity needs a reference that is not the engine under test',
                             'passed': False, 'matched': None, 'reference_detections': None,
                             'min_iou': None, 'mean_iou': None, 'max_confidence_delta': None}
        status = 'VERIFIED' if (parity_result.get('passed') and label_backed) else (
            'PARITY_FAILED' if parity_result.get('matched') is not None and not parity_result.get('passed')
            else 'NOT VERIFIED - LABEL_NOT_BACKED_BY_ENGINE_DTYPES' if not label_backed
            else 'NOT VERIFIED - NO_INDEPENDENT_REFERENCE')

        (precision_out / 'parity.json').write_text(json.dumps(
            {'precision_label': precision, 'actual_engine_precision': runtime.actual_precision(),
             'label_backed_by_engine_dtypes': label_backed, 'engine': engine_record,
             **parity_result}, indent=2, default=str))
        (precision_out / 'benchmark.json').write_text(json.dumps(
            {'captured_at': utc(), 'precision_label': precision,
             'actual_engine_precision': runtime.actual_precision(), 'engine': engine_record,
             'benchmark': bench, 'thermal_and_power': 'see ../jetson_telemetry.json (captured around this run)',
             'model_only_theoretical_throughput': {
                 'formula': '1000 / mean_latency_ms',
                 'value': bench['throughput_fps_model_only'],
                 'caveat': ('MODEL-ONLY. Excludes RTSP/network, decode, preprocess, tracking, temporal '
                            'analysis and event handling, so it is higher than achievable camera FPS.')}},
            indent=2, default=str))

        results[precision] = {'status': status, 'precision_label': precision,
                              'actual_engine_precision': runtime.actual_precision(),
                              'label_backed_by_engine_dtypes': label_backed,
                              'engine': engine_record, 'parity': parity_result, 'benchmark': bench}
        steps.ok(f'benchmark_{precision}', p50_ms=bench['p50_ms'], p95_ms=bench['p95_ms'],
                 mean_ms=bench['mean_ms'], fps_model_only=bench['throughput_fps_model_only'])
        row = {'runtime': 'TENSORRT', 'precision': precision, 'device': environment.get('torch_device_name'),
               'input': list(contract.input_shape), 'p50_ms': bench['p50_ms'], 'p95_ms': bench['p95_ms'],
               'mean_ms': bench['mean_ms'], 'throughput_fps_model_only': bench['throughput_fps_model_only'],
               'engine_bytes': engine_record['engine_bytes'], 'build_seconds': build_seconds,
               'engine_dtypes': engine_record.get('engine_dtypes'),
               'gpu_memory': 'NOT MEASURED'}
        if label_backed:
            table.append(row)
        else:
            unbacked.append({**row, 'excluded_from_authoritative_table': True,
                             'reason': 'the engine does not declare the precision this label claims'})

        if not arguments.keep_engines:
            try:
                engine_path.unlink()
                results[precision]['engine']['engine_deleted_after_run'] = True
            except OSError:
                results[precision]['engine']['engine_deleted_after_run'] = False

    if reference_bench is not None:
        table.insert(0, {'runtime': reference_detector.runtime_name if reference_detector else 'reference',
                         'precision': 'fp32', 'device': environment.get('torch_device_name'),
                         'input': list(contract.input_shape), 'p50_ms': reference_bench['p50_ms'],
                         'p95_ms': reference_bench['p95_ms'], 'mean_ms': reference_bench['mean_ms'],
                         'throughput_fps_model_only': reference_bench['throughput_fps_model_only'],
                         'gpu_memory': 'NOT MEASURED'})

    verified = any(result.get('status') == 'VERIFIED' for result in results.values())
    summary = {'status': 'NOT VERIFIED',
               'captured_at': utc(), 'environment': environment, 'model': model_record,
               'mixed_fp16_graph': fp16_record, 'contract': contract.as_dict(),
               'class_mapping_version': environment['class_mapping_version'], 'samples': samples,
               'benchmark_table': table, 'precisions': results,
               'non_authoritative_precisions': unbacked,
               'reference_scope': reference_scope,
               'engine_policy': {'built_on': 'this device', 'retained': bool(arguments.keep_engines),
                                 'note': ('engines are device and TensorRT-version specific; they are deleted '
                                          'after measuring and referenced by SHA-256 only')},
               'not_verified_by_this_run': [name for name, value in
                                            (('physical Jetson', environment.get('physical_jetson')),
                                             ('TensorRT', environment.get('tensorrt')),
                                             ('true mixed FP16', bool(fp16_record))) if not value],
               'steps': steps.as_list()}
    summary['status'] = ('VERIFIED ON THIS DEVICE' if verified else 'NOT VERIFIED')
    summary['device_scope'] = ('a confirmed physical Jetson' if environment.get('physical_jetson') else
                               f"NOT a physical Jetson ({environment.get('jetson_state')}); results describe "
                               'this host only and must not be presented as Jetson validation')
    (out / 'jetson_environment.json').write_text(json.dumps(environment, indent=2, default=str))
    (out / 'tensorrt-qualification.json').write_text(json.dumps(summary, indent=2, default=str))
    print(json.dumps({'status': summary['status'], 'table': table,
                      'excluded': [row['precision'] for row in unbacked],
                      'evidence': str(out)}, indent=2, default=str))
    return 0 if verified else FAILED_EXIT


def benchmark_pipeline_latency(detector, frames, warmup, iterations):
    """Letterbox + runtime + canonical decode. Not a video-pipeline number."""
    for _ in range(warmup):
        detector.infer(frames[0])
    samples = []
    for index in range(iterations):
        started = time.perf_counter()
        detector.infer(frames[index % len(frames)])
        samples.append((time.perf_counter() - started) * 1000)
    mean = float(statistics.fmean(samples))
    return {'iterations': iterations, 'warmup': warmup,
            'p50_ms': round(sorted(samples)[max(0, int(len(samples) * 0.5) - 1)], 3),
            'p95_ms': round(sorted(samples)[max(0, int(len(samples) * 0.95) - 1)], 3),
            'mean_ms': round(mean, 3), 'throughput_fps_end_to_end': round(1000.0 / mean, 2),
            'scope': 'letterbox + runtime + canonical decode; excludes video decode, tracking and events'}


if __name__ == '__main__':
    sys.exit(main())
