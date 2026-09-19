"""Detector -> Tracker -> TemporalAnalyzer -> durable event, for every runtime.

One canonical model contract, one decoder and one NMS live in
`shared/model_contract.py`; this module never re-implements them. Everything the
CPU worker does here is also what the NVIDIA/DeepStream adapter must do, so the
two paths cannot silently diverge.

Frame ingestion is delegated to `edge.video` (OpenCV or GStreamer) and behaviour
to `edge.temporal` (deterministic analyzers). The previously verified synthetic
journey is preserved: the same bounded latest-frame queue, the same 5 FPS
inference throttle, the same 500 ms freshness check and the same ByteTrack plus
sustained bare-head rule.
"""
import hashlib
import queue
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import numpy as np
import supervision as sv

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared import model_contract
from shared.model_contract import (CANONICAL_CLASSES, CANONICAL_LABELS, ModelContract, canonical_detections,
                                   decode, letterbox, nms, normalize_label, parse_names)
from .runtimes import CPU, create_runtime
from .rules import associate  # noqa: F401 - re-exported for existing importers
from .temporal import (DEFAULT_TEMPORAL_SETTINGS, TrackObservation, TemporalPipeline,  # noqa: F401
                       build_analyzers, canonical_event)
from .video import OpenCVSource, VideoSource, create_source  # noqa: F401

DEFAULT_SETTINGS = {'score_threshold': model_contract.DEFAULT_SCORE_THRESHOLD,
                    'nms_iou': model_contract.DEFAULT_NMS_IOU,
                    'inference_fps': 5.0, 'queue_capacity': 2, 'stale_frame_ms': 500,
                    'input_width': model_contract.DEFAULT_INPUT[0],
                    'input_height': model_contract.DEFAULT_INPUT[1]}
Source = OpenCVSource


class Detector:
    """Hash-verified model + canonical contract + the selected inference runtime."""

    def __init__(self, path, expected_sha256, class_mapping=None, profile=None, runtime=CPU,
                 contract=None, engine_path=None, prebuilt_runtime=None):
        self.path = Path(path)
        actual = hashlib.sha256(self.path.read_bytes()).hexdigest()
        if actual != expected_sha256:
            raise ValueError('model_hash_mismatch')
        declared = self._declared_names()
        head_width = _head_width(declared)
        if contract is not None:
            self.contract = contract
        elif profile is not None:
            self.contract = model_contract.profile(profile)
        elif class_mapping is not None:
            # Historical callers supply only the source->canonical mapping; the
            # score-head width comes from the artifact itself, never from max(index).
            self.contract = ModelContract('explicit_mapping', class_mapping, source_count=head_width)
        else:
            # No hardcoded assumption: the model's own declared labels decide.
            self.contract = model_contract.ModelContract.detect(declared)
        if head_width and self.contract.source_count != head_width:
            raise ValueError('contract_source_count_mismatch')
        self.mapping = dict(self.contract.mapping)
        self.sources = tuple(self.contract.sources)
        if prebuilt_runtime is not None:
            # An engine that was built and loaded by the caller (TensorRT); never
            # re-created here, because a freshly built engine may already be gone.
            self.runtime = prebuilt_runtime
        else:
            if runtime == CPU:
                self.runtime = create_runtime(runtime, self.path, self.contract)
            else:
                kwargs = {'engine_path': engine_path} if engine_path else {}
                self.runtime = create_runtime(runtime, self.path, self.contract, **kwargs)
            self.runtime.load()
        self.runtime_name = self.runtime.name
        self.input = self.runtime.session.get_inputs()[0] if getattr(self.runtime, 'session', None) else None

    def _declared_names(self):
        names = _peek_metadata(self.path).get('names')
        if not names:
            raise ValueError('model_missing_class_metadata')
        return parse_names(names)

    def infer(self, frame, score_threshold=None):
        """Return `(supervision.Detections, latency_ms)` in canonical classes."""
        tensor, geometry = letterbox(frame, self.contract.input_shape)
        started = time.perf_counter()
        raw = self.runtime.infer(tensor)
        latency = (time.perf_counter() - started) * 1000
        self.contract.detected_geometry(raw)
        boxes, confidence, class_id = canonical_detections(
            raw, self.contract, self.contract.score_threshold if score_threshold is None else score_threshold,
            geometry, self.contract.nms_iou)
        if not len(boxes):
            return sv.Detections.empty(), latency
        return sv.Detections(xyxy=boxes, confidence=confidence, class_id=class_id), latency

    def describe(self):
        return {**self.runtime.describe(), 'contract': self.contract.as_dict(),
                'sha256': hashlib.sha256(self.path.read_bytes()).hexdigest(),
                'runtime_available': True}


def _head_width(declared):
    """Number of score classes the artifact publishes (index width, not mapped count)."""
    if not declared:
        return None
    try:
        return max(int(index) for index in declared) + 1
    except (TypeError, ValueError):
        return len(declared)


def _peek_metadata(path):
    """Read only the model's declared class metadata without creating a session."""
    import onnx
    model = onnx.load(str(path), load_external_data=False)
    return {prop.key: prop.value for prop in model.metadata_props}


class Pipeline:
    """Bounded decode -> throttle -> infer -> track -> temporal -> durable outbox."""

    def __init__(self, detector, source, metadata, state, preview=None, settings=None, telemetry=None):
        self.detector = detector
        self.source = source
        self.metadata = metadata
        self.state = state
        self.preview = preview
        self.settings = {**DEFAULT_SETTINGS, **(settings or {})}
        self.telemetry = telemetry
        self.latencies = []
        self.processed = 0
        self.dropped = 0
        self.events = 0
        self.last_frame = None
        self.session = None
        self.analyzers = None
        self.started_at = time.monotonic()

    def _analyzers(self):
        settings = self.settings.get('temporal') or {}
        names = settings.get('analyzers') or ['ppe_sustained']
        return TemporalPipeline(build_analyzers({**DEFAULT_TEMPORAL_SETTINGS, **settings, 'analyzers': names}))

    def run(self):
        decode_thread = threading.Thread(target=self.source.run, daemon=True)
        decode_thread.start()
        session = None
        tracker = None
        analyzers = self._analyzers()
        self.analyzers = analyzers
        interval = 1.0 / max(float(self.settings['inference_fps']), 0.01)
        last_inference = 0.0
        stale_seconds = float(self.settings['stale_frame_ms']) / 1000.0
        while not self.source.stop.is_set():
            try:
                sid, sequence, decoded, observed, frame = self.source.queue.get(timeout=1)
            except queue.Empty:
                # Never spin forever if the decoder thread died unexpectedly.
                if self.source.status in ('ended', 'error') or not decode_thread.is_alive():
                    return
                continue
            age = time.monotonic() - decoded
            if age > stale_seconds or time.monotonic() - last_inference < interval:
                self.source.dropped += 1
                self.dropped += 1
                continue
            last_inference = time.monotonic()
            if sid != session:
                session = sid
                tracker = sv.ByteTrack(frame_rate=max(1, int(self.settings['inference_fps'])))
                analyzers = self._analyzers()
                self.analyzers = analyzers
                self.session = sid
            detections, latency = self.detector.infer(frame)
            self.latencies.append(latency)
            self.latencies = self.latencies[-1024:]
            self.processed += 1
            self.last_frame = observed
            persons = tracker.update_with_detections(detections[detections.class_id == 0])
            height, width = frame.shape[:2]
            heads = []
            for box, confidence, class_id in zip(detections.xyxy, detections.confidence, detections.class_id):
                if class_id in (1, 2):
                    heads.append(('helmet' if class_id == 1 else 'no_helmet', float(confidence),
                                  (box / np.array([width, height, width, height])).tolist()))
            tracks = []
            for box, track_id in zip(persons.xyxy, persons.tracker_id):
                tracks.append(track_id)
                bbox = (box / np.array([width, height, width, height])).tolist()
                label, confidence = associate(bbox, heads)
                observation = TrackObservation(session=sid, track_id=str(track_id), timestamp=decoded,
                                               observed_at=observed, bbox=bbox, label=label,
                                               confidence=confidence, frame_sequence=sequence)
                for event in analyzers.observe(observation):
                    self._publish(event, sid, observed)
                if self.preview:
                    cv2.rectangle(frame, tuple(box[:2].astype(int)), tuple(box[2:].astype(int)),
                                  (0, 200, 0) if label == 'helmet' else (0, 100, 255), 2)
            analyzers.retain(sid, tracks)
            if self.preview:
                cv2.imwrite(str(self.preview), frame)

    def _publish(self, event, session, observed):
        span = float(event.pop('span_seconds'))
        payload = {**self.metadata, **event, 'kind': 'safety',
                   'safety_event_id': str(uuid.uuid4()), 'stream_session_id': session,
                   'observed_at': observed.isoformat(),
                   'window_start': (observed - timedelta(seconds=span)).isoformat(),
                   'window_end': observed.isoformat()}
        self.state.enqueue(payload)
        self.events += 1

    def processed_fps(self, window_seconds=10.0):
        elapsed = max(time.monotonic() - self.started_at, 1e-6)
        return round(self.processed / min(elapsed, max(window_seconds, 1.0)), 3)

    def latency_percentiles(self):
        if not self.latencies:
            return {'p50': None, 'p95': None}
        values = sorted(self.latencies)
        return {'p50': round(values[max(0, int(len(values) * 0.5) - 1)], 3),
                'p95': round(values[max(0, int(len(values) * 0.95) - 1)], 3)}

    def status(self):
        """Single observability payload consumed by the worker status file."""
        source = self.source.metrics() if isinstance(self.source, VideoSource) else {
            'status': self.source.status, 'queue_depth': self.source.queue.qsize()}
        percentiles = self.latency_percentiles()
        payload = {'camera_id': self.metadata.get('camera_id'), 'status': source.get('status'),
                   'last_frame_at': self.last_frame.isoformat() if self.last_frame else None,
                   'stream_session_id': self.session,
                   'input_frames': self.source.input_frames, 'inference_frames': self.processed,
                   'dropped_frames': self.source.dropped, 'stale_dropped_frames': self.dropped,
                   'reconnect_count': self.source.reconnects,
                   'processed_fps': self.processed_fps(), 'inference_latency_ms': self.latencies,
                   'inference_latency_ms_p50': percentiles['p50'], 'inference_latency_ms_p95': percentiles['p95'],
                   'temporal_analyzers': self.analyzers.names if self.analyzers else None,
                   'safety_events_total': self.events, 'runtime': self.detector.runtime_name,
                   'contract_profile': self.detector.contract.profile, **source}
        if self.telemetry is not None:
            try:
                payload['hardware'] = self.telemetry.sample()
            except Exception as exc:  # noqa: BLE001 - telemetry must never stop the pipeline
                payload['hardware'] = {'error': type(exc).__name__}
        return payload


__all__ = ['Detector', 'Pipeline', 'Source', 'OpenCVSource', 'VideoSource', 'create_source',
           'SafetyRule', 'associate', 'CANONICAL_CLASSES', 'CANONICAL_LABELS', 'normalize_label',
           'ModelContract', 'decode', 'nms', 'canonical_detections', 'letterbox', 'parse_names']


def __getattr__(name):
    # `SafetyRule` stays importable from here for the existing verification scripts.
    if name == 'SafetyRule':
        from .rules import SafetyRule
        return SafetyRule
    raise AttributeError(name)
