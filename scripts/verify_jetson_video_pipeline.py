"""Run the real VisionOps video pipeline on the target device and measure it.

Model-only latency is not a camera result. This script runs the whole path -

    source (GStreamer or OpenCV) -> bounded latest-frame queue -> letterbox
        -> TensorRT/ONNX -> ByteTrack -> temporal rule -> durable SQLite outbox

- and reports decode FPS, processed FPS, drops, queue depth, model percentiles and
the durable events that came out the far end. The bounded latest-frame behaviour is
the existing production one: a stale frame is dropped, never queued indefinitely.

Hardware decode is *inspected* (`gst-inspect-1.0`) and, when requested, *tested*:
`HARDWARE_DECODE_RUNTIME_VERIFIED` requires that the pipeline which actually ran used
a hardware element and produced frames. Installing JetPack does not by itself prove
NVDEC is active, so a plugin that exists but was never exercised stays
`NOT VERIFIED`.

RTSP is optional. Point `--rtsp-url` at a real camera or a server you run; with
`--publish-local` the bundled sample video is published to that URL by
`scripts/publish_rtsp.py` and the publisher is stopped and restarted to force a real
outage, so reconnect and recovery are measured rather than asserted. Without a URL
the result is `rtsp/NOT_VERIFIED.json` - never an empty success.

    python3 -m scripts.verify_jetson_video_pipeline \
        --onnx model/hansung-p3.onnx --video samples/ppe.mp4 --seconds 60 --out evidence/jetson
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.verify_tensorrt_gpu import utc  # noqa: E402

# Elements that mean "this device can decode in hardware". `nvdec` is the generic
# entry point; the others are the concrete decoders a pipeline string would use.
NVIDIA_DECODE_ELEMENTS = ('nvdec', 'nvh264dec', 'nvh265dec', 'nvv4l2decoder', 'nvjpegdec',
                          'nvvidconv', 'nvvideoconvert')
HARDWARE_DECODE_TOKENS = ('nvh264dec', 'nvh265dec', 'nvv4l2decoder', 'nvjpegdec')


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def process(command, timeout=30):
    executable = shutil.which(command[0])
    if not executable:
        return {'command': ' '.join(command), 'error': 'not_on_path', 'returncode': None,
                'stdout': None, 'stderr': None}
    try:
        result = subprocess.run([executable, *command[1:]], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {'command': ' '.join(command), 'error': 'timeout', 'returncode': None,
                'stdout': None, 'stderr': None}
    except Exception as exc:  # noqa: BLE001
        return {'command': ' '.join(command), 'error': type(exc).__name__, 'returncode': None,
                'stdout': None, 'stderr': None}
    return {'command': ' '.join(command), 'error': None, 'returncode': result.returncode,
            'stdout': (result.stdout or '')[:40000], 'stderr': (result.stderr or '')[:4000]}


def inspect_gstreamer_decode():
    """What decode capability the device exposes. Existence is not activity."""
    record = {'gst_launch': shutil.which('gst-launch-1.0'), 'gst_inspect': shutil.which('gst-inspect-1.0'),
              'elements': {}, 'nvidia_elements_in_registry': [], 'state': None,
              'runtime_verified': False, 'runtime_verified_by': None}
    if not record['gst_inspect']:
        record['state'] = 'GSTREAMER_NOT_AVAILABLE'
        record['reason'] = 'gst-inspect-1.0 not on PATH'
        return record

    record['gst_inspect_version'] = (process(['gst-inspect-1.0', '--version'])['stdout'] or '').strip()[:400]
    listing = process(['gst-inspect-1.0'], timeout=60)
    record['registry_scan'] = {'returncode': listing['returncode'], 'error': listing['error'],
                              'stdout_bytes': len(listing['stdout'] or '')}
    names = []
    for line in (listing['stdout'] or '').splitlines():
        name = line.split(':', 1)[0].strip()
        if name and ' ' not in name:
            names.append(name)
    record['nvidia_elements_in_registry'] = sorted(name for name in names if 'nv' in name.lower())[:200]
    record['registry_element_count'] = len(names)
    for element in NVIDIA_DECODE_ELEMENTS:
        record['elements'][element] = element in names
    available = [name for name, present in record['elements'].items() if present]
    record['available_decode_elements'] = available
    record['state'] = 'NVIDIA_DECODE_PLUGIN_AVAILABLE' if available else 'NVIDIA_DECODE_NOT_FOUND'
    if not available:
        record['reason'] = ('no NVIDIA decode element in the GStreamer registry; JetPack being installed '
                            'does not by itself put one there')
    return record


def mark_decode_runtime_verified(inspection, pipeline_description, decoded_frames):
    """Hardware decode counts as verified only if it ran and produced frames."""
    used = [token for token in HARDWARE_DECODE_TOKENS if token in (pipeline_description or '')]
    inspection['pipeline_used'] = pipeline_description
    inspection['hardware_elements_used'] = used
    inspection['decoded_frames'] = decoded_frames
    inspection['runtime_verified'] = bool(used) and decoded_frames > 0
    inspection['runtime_verified_by'] = (('the selected pipeline used ' + '+'.join(used)
                                          + f' and produced {decoded_frames} frames')
                                         if inspection['runtime_verified'] else None)
    if inspection['runtime_verified']:
        inspection['state'] = 'HARDWARE_DECODE_RUNTIME_VERIFIED'
    elif used:
        inspection['state'] = 'HARDWARE_DECODE_SELECTED_BUT_PRODUCED_NO_FRAMES'
    return inspection


def resolve_runtime(requested, fp16_onnx):
    """Which inference runtime this run will actually use, and why."""
    from edge.runtimes import CPU, TENSORRT, detect_capabilities
    capabilities = detect_capabilities()
    if requested == 'tensorrt':
        return TENSORRT, 'requested'
    if requested == 'onnx-cpu':
        return CPU, 'requested'
    if capabilities[TENSORRT]['available']:
        return TENSORRT, 'auto: TensorRT is available on this device'
    return CPU, f"auto: TensorRT unavailable ({capabilities[TENSORRT]['reason']}), ONNX CPU used"


def build_detector(onnx, runtime, fp16_onnx, engine, precision):
    """Detector on the chosen runtime. A TensorRT engine is built here, never copied."""
    from edge.pipeline import Detector
    from edge.runtimes import CPU, TENSORRT, create_runtime

    if runtime == CPU:
        return Detector(onnx, sha256_file(onnx), runtime=CPU), None
    model = fp16_onnx if (precision == 'fp16' and fp16_onnx) else onnx
    # The canonical contract comes from the qualified FP32 artifact even when the
    # engine is built from the mixed-precision graph, so both share one interpretation.
    contract = Detector(onnx, sha256_file(onnx), runtime=CPU).contract
    workspace = 1 << 30
    runtime_object = create_runtime(TENSORRT, model, contract, precision=precision,
                                   engine_path=str(engine), workspace_bytes=workspace)
    if Path(engine).exists():
        runtime_object.load()
    else:
        runtime_object.build_engine(workspace_bytes=workspace)
        runtime_object.load()
    return Detector(model, sha256_file(model), contract=contract, prebuilt_runtime=runtime_object), \
        runtime_object.actual_precision()


def run_pipeline(options, seconds, source_locator=None):
    """One real pipeline run. Returns the measured status plus the durable events."""
    from edge.pipeline import Detector, Pipeline
    from edge.state import State
    from edge.temporal import DEFAULT_TEMPORAL_SETTINGS
    from edge.video import VideoBackendUnavailable, create_source, selected_backend

    locator = source_locator or options.video
    runtime_name, runtime_reason = resolve_runtime(options.runtime, options.fp16_onnx)
    engine = Path(options.engine) if options.engine else Path(tempfile.gettempdir()) / \
        f'visionops-jetson-video-{options.precision}.engine'
    try:
        detector, engine_precision = build_detector(options.onnx, runtime_name, options.fp16_onnx,
                                                   engine, options.precision)
    except Exception as exc:  # noqa: BLE001 - reported as evidence, not a traceback
        return {'status': 'RUNTIME_UNAVAILABLE', 'runtime_requested': runtime_name,
                'runtime_reason': runtime_reason, 'reason': f'{type(exc).__name__}: {exc}'}

    resolved_backend = selected_backend(locator, options.backend)
    # `hardware_acceleration` is a GStreamer-only kwarg; passing it to the OpenCV
    # source would be a TypeError, so it is only supplied where it applies.
    source_kwargs = {'hardware_acceleration': options.hardware_decode} \
        if resolved_backend == 'gstreamer' else {}
    try:
        source = create_source(locator, backend=resolved_backend, **source_kwargs)
    except VideoBackendUnavailable as exc:
        return {'status': 'BACKEND_UNAVAILABLE', 'reason': str(exc),
                'backend_requested': options.backend, 'backend_resolved': resolved_backend,
                'runtime': detector.runtime_name, 'engine_precision': engine_precision}

    metadata = {'camera_id': 'jetson-validation', 'site_id': 'jetson-validation',
                'release_id': 'jetson-validation', 'model_version_id': 'hansung-p3',
                'config_version_id': 'jetson-validation'}
    zone = {'zone_id': 'jetson-yard', 'bbox': [0.0, 0.0, 1.0, 1.0], 'kind': 'restricted',
            'min_dwell_seconds': 1.0, 'min_presence_frames': 5, 'cooldown_seconds': 5}
    settings = {'inference_fps': options.inference_fps, 'stale_frame_ms': 500, 'queue_capacity': 2,
                'temporal': {**DEFAULT_TEMPORAL_SETTINGS,
                             'analyzers': ['ppe_sustained', 'zone_dwell'], 'zones': [zone]}}
    errors = []
    with tempfile.TemporaryDirectory(prefix='visionops-jetson-pipeline-') as folder:
        state = State(Path(folder) / 'state.sqlite')
        pipeline = Pipeline(detector, source, metadata, state, settings=settings)

        def guarded():
            try:
                pipeline.run()
            except Exception as exc:  # noqa: BLE001
                errors.append(f'{type(exc).__name__}: {exc}')

        worker = threading.Thread(target=guarded, daemon=True)
        started = time.monotonic()
        worker.start()
        time.sleep(max(seconds, 0.1))
        source.stop.set()
        worker.join(timeout=15)
        try:
            events = state.batch()
        finally:
            state.db.close()

    status = pipeline.status()
    description = getattr(source, 'pipeline_description_used', None)
    if description is None and hasattr(source, 'pipeline_description'):
        try:
            description = source.pipeline_description()
        except Exception:  # noqa: BLE001
            description = None
    return {'status': 'VERIFIED' if pipeline.processed else 'NO_FRAMES_PROCESSED',
            'backend': source.backend, 'backend_resolved': resolved_backend,
            'pipeline_description': description,
            'runtime': detector.runtime_name, 'runtime_reason': runtime_reason,
            'engine_precision': engine_precision,
            'engine': str(engine) if Path(engine).exists() else None,
            'contract_profile': detector.contract.profile,
            'elapsed_seconds': round(time.monotonic() - started, 3),
            'source_fps_declared': status.get('declared_fps'),
            'decode_fps': status.get('decode_fps'), 'input_fps': status.get('input_fps'),
            'processed_fps': status.get('processed_fps'), 'inference_frames': status.get('inference_frames'),
            'input_frames': status.get('input_frames'), 'dropped_frames': status.get('dropped_frames'),
            'stale_dropped_frames': status.get('stale_dropped_frames'),
            'queue_depth': status.get('queue_depth'), 'queue_capacity': status.get('queue_capacity'),
            'rtsp_connected': status.get('rtsp_connected'),
            'rtsp_reconnect_total': status.get('rtsp_reconnect_total'),
            'model_latency_ms_p50': status.get('inference_latency_ms_p50'),
            'model_latency_ms_p95': status.get('inference_latency_ms_p95'),
            'temporal_analyzers': status.get('temporal_analyzers'),
            'events_durable': len(events),
            'events': [{'event_type': event.get('event_type'), 'track_id': event.get('track_id'),
                        'supporting_frames': event.get('supporting_frames')} for event in events],
            'worker_errors': errors, 'source_metrics': source.metrics(),
            'scope': ('full pipeline on this device: decode -> bounded queue -> infer -> ByteTrack -> '
                      'temporal rule -> SQLite outbox. Not a sustained thermal run.')}


def verify_rtsp(arguments, out):
    """Connect / stream / disconnect / reconnect / resume, measured. Optional."""
    directory = out / 'rtsp'
    directory.mkdir(parents=True, exist_ok=True)
    if not arguments.rtsp_url:
        payload = {'status': 'NOT_VERIFIED', 'reason': 'no --rtsp-url was supplied',
                   'what_was_done': ['nothing: RTSP is optional and this run had no stream to test'],
                   'how_to_test': ('python3 -m scripts.verify_jetson_video_pipeline --rtsp-url '
                                   'rtsp://<host>/<stream> [--publish-local --simulate-outage] ...'),
                   'note': 'the local MP4/GStreamer path is the guaranteed fallback and is unaffected'}
        (directory / 'NOT_VERIFIED.json').write_text(json.dumps(payload, indent=2))
        return payload

    published = {'attempted': bool(arguments.publish_local), 'command': None, 'error': None}
    publisher = None
    command = None
    if arguments.publish_local:
        command = [sys.executable, '-m', 'scripts.publish_rtsp', '--video', str(arguments.video),
                   '--url', arguments.rtsp_url]
        published['command'] = ' '.join(command)
        try:
            publisher = subprocess.Popen(command, cwd=str(ROOT), stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, text=True)
        except Exception as exc:  # noqa: BLE001
            published['error'] = type(exc).__name__
            publisher = None
        time.sleep(5)  # let the publisher ANNOUNCE and start pushing

    first = run_pipeline(arguments, arguments.rtsp_seconds, source_locator=arguments.rtsp_url)

    outage = None
    if publisher is not None and arguments.simulate_outage:
        # A real outage: the publisher feeding the server goes away, then comes back.
        before = (first.get('source_metrics') or {}).get('rtsp_reconnect_total')
        publisher.terminate()
        try:
            publisher.wait(timeout=10)
        except subprocess.TimeoutExpired:
            publisher.kill()
        down_started = time.monotonic()
        time.sleep(arguments.outage_seconds)
        restarted = subprocess.Popen(command, cwd=str(ROOT), stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True)
        recovered_at = time.monotonic()
        second = run_pipeline(arguments, arguments.rtsp_seconds, source_locator=arguments.rtsp_url)
        after = (second.get('source_metrics') or {}).get('rtsp_reconnect_total')
        outage = {'stream_down_seconds_requested': arguments.outage_seconds,
                  'publisher_restarted': True,
                  'downtime_to_publisher_restart_seconds': round(recovered_at - down_started, 3),
                  'reconnect_total_before': before, 'reconnect_total_after': after,
                  'frames_after_recovery': second.get('inference_frames'),
                  'resumed': bool(second.get('inference_frames')),
                  'second_session': second}
        try:
            restarted.terminate()
        except Exception:  # noqa: BLE001
            pass
    elif publisher is not None:
        try:
            publisher.terminate()
        except Exception:  # noqa: BLE001
            pass
        outage = {'status': 'NOT RUN', 'reason': '--simulate-outage was not requested',
                  'note': 'connect/stream were still measured; only the outage step is absent'}

    payload = {'status': 'VERIFIED' if first.get('inference_frames') else 'NOT VERIFIED',
               'url': arguments.rtsp_url, 'publisher': published, 'first_session': first,
               'outage_test': outage,
               'scope': ('real RTSP session on this device; the outage is a real publisher restart, not a '
                         'simulated counter')}
    (directory / 'rtsp-reconnect.json').write_text(json.dumps(payload, indent=2, default=str))
    return payload


def main(argv=None):
    parser = argparse.ArgumentParser(description='Run the full video pipeline on this device.')
    parser.add_argument('--onnx', default='model/hansung-p3.onnx')
    parser.add_argument('--fp16-onnx', default=None)
    parser.add_argument('--engine', default=None, help='reuse an engine built on this device')
    parser.add_argument('--precision', default='fp32', choices=['fp32', 'fp16'])
    parser.add_argument('--runtime', default='auto', choices=['auto', 'tensorrt', 'onnx-cpu'])
    parser.add_argument('--video', default='samples/ppe.mp4')
    parser.add_argument('--seconds', type=float, default=60.0)
    parser.add_argument('--inference-fps', type=float, default=5.0)
    parser.add_argument('--backend', default='auto', choices=['auto', 'gstreamer', 'opencv'])
    parser.add_argument('--hardware-decode', action='store_true',
                       help='request NVDEC; verification still requires frames to come out')
    parser.add_argument('--rtsp-url', default=None)
    parser.add_argument('--rtsp-seconds', type=float, default=20.0)
    parser.add_argument('--publish-local', action='store_true',
                       help='publish --video to --rtsp-url with scripts/publish_rtsp.py')
    parser.add_argument('--simulate-outage', action='store_true',
                       help='stop and restart the local publisher to measure reconnect and recovery')
    parser.add_argument('--outage-seconds', type=float, default=10.0)
    parser.add_argument('--out', default='evidence/jetson')
    arguments = parser.parse_args(argv)

    out = Path(arguments.out)
    (out / 'video').mkdir(parents=True, exist_ok=True)
    inspection = inspect_gstreamer_decode()
    report = {'captured_at': utc(), 'locator': str(arguments.video), 'backend_requested': arguments.backend,
              'hardware_decode_requested': bool(arguments.hardware_decode),
              'hardware_decode_inspection': inspection}

    if not Path(arguments.video).is_file():
        report['status'] = 'NOT VERIFIED'
        report['reason'] = f'video not found: {arguments.video}'
    else:
        result = run_pipeline(arguments, arguments.seconds)
        report['result'] = result
        report['status'] = result.get('status')
        inspection = mark_decode_runtime_verified(
            inspection, result.get('pipeline_description'),
            (result.get('source_metrics') or {}).get('decoded_frames_total', 0))
        report['hardware_decode_inspection'] = inspection

    (out / 'video' / 'hardware-decode.json').write_text(json.dumps(inspection, indent=2, default=str))
    (out / 'video' / 'video-pipeline.json').write_text(json.dumps(report, indent=2, default=str))
    verify_rtsp(arguments, out)

    print(json.dumps({'status': report['status'],
                      'hardware_decode_state': inspection.get('state'),
                      'hardware_decode_runtime_verified': inspection.get('runtime_verified'),
                      'runtime': (report.get('result') or {}).get('runtime'),
                      'processed_fps': (report.get('result') or {}).get('processed_fps'),
                      'events_durable': (report.get('result') or {}).get('events_durable'),
                      'evidence': str(out / 'video' / 'video-pipeline.json')}, indent=2, default=str))
    return 0 if report['status'] == 'VERIFIED' else 4


if __name__ == '__main__':
    sys.exit(main())
