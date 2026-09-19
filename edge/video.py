"""Frame ingestion backends: one VideoSource contract, two decoders.

`VISIONOPS_VIDEO_BACKEND=opencv|gstreamer|auto` selects the decoder. A frame
source knows nothing about the model: it publishes
`(session_id, sequence, decode_monotonic, observed_utc, BGR frame)` tuples into a
bounded latest-frame queue and keeps its own truthful counters.

Reconnection is bounded and never terminates the worker: an RTSP outage moves the
source through connecting -> running -> reconnecting with exponential backoff
(1s .. 30s), and the backoff resets once a session actually delivers frames.
"""
import os
import queue
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone

import cv2

RTSP_SCHEMES = ('rtsp://', 'rtsps://')
BACKENDS = ('opencv', 'gstreamer', 'auto')

# The GStreamer pipeline this module actually builds for RTSP. Documented here so
# the exact command is reproducible with gst-launch-1.0 outside VisionOps.
GSTREAMER_RTSP_PIPELINE = (
    'rtspsrc location={locator} protocols=tcp latency={latency} name=src'
    ' ! rtph264depay ! h264parse ! avdec_h264'
    ' ! videoconvert ! video/x-raw,format=BGR'
    ' ! appsink name=sink emit-signals=true max-buffers={buffers} drop=true sync=false'
)
GSTREAMER_FILE_PIPELINE = (
    'uridecodebin uri={uri} name=src'
    ' ! videoconvert ! video/x-raw,format=BGR'
    ' ! appsink name=sink emit-signals=true max-buffers={buffers} drop=true sync=false'
)
# Optional hardware-accelerated decode. Only selected when the caller passes
# hardware_acceleration=True AND the elements exist; never required locally.
GSTREAMER_HW_DECODE_ELEMENTS = ('nvdec', 'nvh264dec')


class VideoBackendUnavailable(RuntimeError):
    """Raised when the requested backend cannot run in this environment."""


def gstreamer_available():
    """True only when the Python GStreamer bindings and core registry load."""
    try:
        import gi
        gi.require_version('Gst', '1.0')
        from gi.repository import Gst
    except Exception:
        return False
    try:
        Gst.init(None)
        return Gst.ElementFactory.find('appsink') is not None
    except Exception:
        return False


def gstreamer_hardware_decode_available():
    if not gstreamer_available():
        return False
    from gi.repository import Gst
    return all(Gst.ElementFactory.find(name) is not None for name in GSTREAMER_HW_DECODE_ELEMENTS)


def rtsp_pipeline(locator, latency=200, buffers=2, decode_element='avdec_h264'):
    """The exact GStreamer pipeline VisionOps builds for an RTSP stream."""
    return GSTREAMER_RTSP_PIPELINE.format(locator=locator, latency=latency, buffers=buffers).replace(
        'avdec_h264', decode_element)


def selected_backend(locator, backend=None):
    """Resolve the effective backend without importing an unavailable decoder."""
    backend = (backend or os.environ.get('VISIONOPS_VIDEO_BACKEND') or 'opencv').lower()
    if backend not in BACKENDS:
        raise ValueError('unknown_video_backend')
    if backend == 'auto':
        live = str(locator).lower().startswith(RTSP_SCHEMES)
        return 'gstreamer' if live and gstreamer_available() else 'opencv'
    return backend


class VideoSource:
    """Bounded latest-frame decoder with honest stream health counters."""

    backend = 'abstract'

    def __init__(self, locator, loop=False, queue_capacity=2, stale_frame_ms=500,
                 rtsp_timeout_seconds=10, reconnect_initial_seconds=1.0,
                 reconnect_max_seconds=30.0, preview_interval_seconds=0.0):
        self.locator = str(locator)
        self.loop = bool(loop)
        self.live = self.locator.lower().startswith(RTSP_SCHEMES)
        self.queue_capacity = max(1, int(queue_capacity))
        self.stale_frame_ms = float(stale_frame_ms)
        self.timeout_ms = int(float(rtsp_timeout_seconds) * 1000)
        self.reconnect_initial_seconds = float(reconnect_initial_seconds)
        self.reconnect_max_seconds = float(reconnect_max_seconds)
        self.queue = queue.Queue(self.queue_capacity)
        self.stop = threading.Event()
        self.status = 'connecting'
        self.error = None
        self.session = None
        self.declared_fps = 0.0
        self.input_frames = 0
        self.decoded_frames = 0
        self.dropped = 0
        self.read_failures = 0
        self.reconnects = 0
        self.sessions = 0
        self.last_frame_monotonic = None
        self.last_observed_at = None
        self.connected_since = None
        self._frame_times = deque(maxlen=512)
        self._next_frame = 0.0

    # -- decoder hooks -----------------------------------------------------
    def _open(self):
        raise NotImplementedError

    def _read(self):
        raise NotImplementedError

    def _close(self):
        raise NotImplementedError

    # -- shared run loop ---------------------------------------------------
    def run(self):
        delay = self.reconnect_initial_seconds
        while not self.stop.is_set():
            session = str(uuid.uuid4())
            self.session = session
            try:
                self._open()
                opened = True
            except VideoBackendUnavailable as exc:
                # A missing decoder cannot be fixed by reconnecting; report and stop
                # so the worker marks the camera unhealthy instead of spinning.
                self.status = 'error'
                self.error = str(exc)
                return
            except Exception as exc:  # noqa: BLE001 - transient, reported, never fatal
                opened = False
                self.error = type(exc).__name__
            self.status = 'running' if opened else 'error'
            if opened:
                self.sessions += 1
                self.connected_since = time.monotonic()
                self._next_frame = time.monotonic()
            delivered = 0
            sequence = 0
            while opened and not self.stop.is_set():
                try:
                    frame = self._read()
                except Exception as exc:  # noqa: BLE001
                    self.error = type(exc).__name__
                    frame = None
                if frame is None:
                    break
                self._pace()
                sequence += 1
                delivered += 1
                self._publish(session, sequence, frame)
            self._close()
            if delivered:
                # A session that actually decoded resets the backoff.
                delay = self.reconnect_initial_seconds
            else:
                self.read_failures += 1
            if self.stop.is_set():
                return
            if not self.live and not self.loop:
                self.status = 'ended' if delivered else 'error'
                return
            self.status = 'reconnecting'
            self.connected_since = None
            self.reconnects += 1
            self.stop.wait(delay)
            delay = min(delay * 2, self.reconnect_max_seconds)

    def _pace(self):
        """Real-time pacing for recorded sources; live sources are never throttled."""
        if self.live or self.declared_fps <= 0:
            return
        wait = self._next_frame - time.monotonic()
        if wait > 0:
            self.stop.wait(wait)
        self._next_frame += 1.0 / self.declared_fps

    def _publish(self, session, sequence, frame):
        now = time.monotonic()
        self.input_frames += 1
        self.decoded_frames += 1
        self.last_frame_monotonic = now
        self.last_observed_at = datetime.now(timezone.utc)
        self._frame_times.append(now)
        if self.queue.full():
            try:
                self.queue.get_nowait()
                self.dropped += 1
            except queue.Empty:
                pass
        self.queue.put_nowait((session, sequence, now, self.last_observed_at, frame))

    def stale(self, age_seconds):
        return age_seconds * 1000.0 > self.stale_frame_ms

    # -- health ------------------------------------------------------------
    @property
    def stream_age_seconds(self):
        return None if self.last_frame_monotonic is None else max(0.0, time.monotonic() - self.last_frame_monotonic)

    @property
    def rtsp_connected(self):
        age = self.stream_age_seconds
        if self.status != 'running' or age is None:
            return False
        return age <= max(self.stale_frame_ms / 1000.0, 1.0)

    def decode_fps(self, window_seconds=5.0):
        return _rate(self._frame_times, window_seconds)

    def metrics(self):
        return {'backend': self.backend, 'status': self.status, 'live': self.live,
                'rtsp_connected': self.rtsp_connected,
                'rtsp_reconnect_total': self.reconnects,
                'sessions_total': self.sessions,
                'input_frames_total': self.input_frames,
                'decoded_frames_total': self.decoded_frames,
                'dropped_frames_total': self.dropped,
                'decode_failures_total': self.read_failures,
                'queue_depth': self.queue.qsize(),
                'queue_capacity': self.queue_capacity,
                'input_fps': round(self.decode_fps(), 3),
                'decode_fps': round(self.decode_fps(), 3),
                'declared_fps': self.declared_fps or None,
                'stream_age_seconds': self.stream_age_seconds,
                'last_frame_at': self.last_observed_at.isoformat() if self.last_observed_at else None,
                'last_error': self.error}


def _rate(times, window_seconds):
    if len(times) < 2:
        return 0.0
    newest = times[-1]
    oldest = next((t for t in times if newest - t <= window_seconds), times[0])
    span = newest - oldest
    if span <= 0:
        return 0.0
    count = sum(1 for t in times if newest - t <= window_seconds)
    return (count - 1) / span


class OpenCVSource(VideoSource):
    """OpenCV/FFmpeg decoder. Local CPU default; unchanged fallback behaviour."""

    backend = 'opencv'

    def _open(self):
        self._capture = cv2.VideoCapture(
            self.locator, cv2.CAP_FFMPEG,
            [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, self.timeout_ms,
             cv2.CAP_PROP_READ_TIMEOUT_MSEC, self.timeout_ms])
        if not self._capture.isOpened():
            raise RuntimeError('capture_open_failed')
        self.declared_fps = float(self._capture.get(cv2.CAP_PROP_FPS) or 0.0)
        self.error = None

    def _read(self):
        ok, frame = self._capture.read()
        return frame if ok else None

    def _close(self):
        try:
            self._capture.release()
        except Exception:  # noqa: BLE001 - a failed release must not stop the loop
            pass


class GStreamerSource(VideoSource):
    """GStreamer appsink decoder (depay -> parse -> decode -> convert -> appsink)."""

    backend = 'gstreamer'

    def __init__(self, locator, loop=False, hardware_acceleration=False, latency=200, **kwargs):
        super().__init__(locator, loop=loop, **kwargs)
        self.hardware_acceleration = bool(hardware_acceleration)
        self.latency = int(latency)
        # Fail fast and loudly: an explicitly requested backend that cannot run is a
        # configuration error, not a transient stream outage to retry forever.
        if not gstreamer_available():
            raise VideoBackendUnavailable('gstreamer_bindings_unavailable')
        if self.hardware_acceleration and not gstreamer_hardware_decode_available():
            raise VideoBackendUnavailable('hardware_decode_unavailable')

    def pipeline_description(self):
        buffers = self.queue_capacity
        if self.live:
            description = rtsp_pipeline(self.locator, self.latency, buffers,
                                        'nvh264dec' if self.hardware_acceleration else 'avdec_h264')
        else:
            from pathlib import Path
            uri = self.locator if '://' in self.locator else Path(self.locator).resolve().as_uri()
            description = GSTREAMER_FILE_PIPELINE.format(uri=uri, buffers=buffers)
            if self.hardware_acceleration:
                description = description.replace('avdec_h264', 'nvh264dec')
        return description

    def _open(self):
        if not gstreamer_available():
            raise VideoBackendUnavailable('gstreamer_bindings_unavailable')
        from gi.repository import Gst
        self._Gst = Gst
        description = self.pipeline_description()
        self.pipeline_description_used = description
        self._pipeline = Gst.parse_launch(description)
        self._appsink = self._pipeline.get_by_name('sink')
        if self._appsink is None:
            raise VideoBackendUnavailable('gstreamer_appsink_missing')
        self._appsink.set_property('emit-signals', False)
        state = self._pipeline.set_state(Gst.State.PLAYING)
        if state == Gst.StateChangeReturn.FAILURE:
            raise VideoBackendUnavailable('gstreamer_pipeline_start_failed')
        self.error = None

    def _read(self, timeout_ns=10_000_000_000):
        Gst = self._Gst
        sample = self._appsink.try_pull_sample(timeout_ns)
        if sample is None:
            return None
        structure = sample.get_caps().get_structure(0)
        height, width = structure.get_value('height'), structure.get_value('width')
        buffer = sample.get_buffer()
        ok, info = buffer.map(Gst.MapFlags.READ)
        if not ok:
            return None
        try:
            import numpy as np
            return np.frombuffer(info.data, np.uint8).reshape(height, width, 3).copy()
        finally:
            buffer.unmap(info)

    def _close(self):
        try:
            self._pipeline.set_state(self._Gst.State.NULL)
        except Exception:  # noqa: BLE001
            pass


def create_source(locator, loop=False, backend=None, **kwargs):
    """Factory honouring `VISIONOPS_VIDEO_BACKEND`; raises for an unavailable backend."""
    resolved = selected_backend(locator, backend)
    if resolved == 'gstreamer':
        return GStreamerSource(locator, loop=loop, **kwargs)
    return OpenCVSource(locator, loop=loop, **kwargs)


# Backwards-compatible name used by the existing worker and verification scripts.
Source = OpenCVSource
