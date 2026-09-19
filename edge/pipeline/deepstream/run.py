"""DeepStream file/RTSP -> nvinfer -> NvDCF -> temporal analyzers -> SQLite outbox.

BLOCKED BY HARDWARE: this launcher requires an actual DeepStream SDK, `gi`/PyGObject,
`pyds`, a parser compiled against the target SDK and a qualified engine. It was not
run in the development environment and no GPU result is claimed.

Behaviour is intentionally shared with the CPU worker: NvDCF supplies tracks, the
custom parser supplies canonical detections, and `edge.temporal` decides events.
Only the ingest/inference half differs.
"""
import argparse
import json
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from bridge import detections  # noqa: E402  (resolved relative to this directory)
from edge.rules import associate  # noqa: E402
from edge.state import State  # noqa: E402
from edge.temporal import TemporalPipeline, TrackObservation, build_analyzers  # noqa: E402

CANONICAL_CLASSES = {0: 'person', 1: 'helmet', 2: 'no_helmet'}

parser = argparse.ArgumentParser()
parser.add_argument('--source', required=True)
parser.add_argument('--nvinfer-config', required=True)
parser.add_argument('--tracker-library', required=True)
parser.add_argument('--tracker-config', required=True)
parser.add_argument('--metadata', required=True)
parser.add_argument('--state', required=True)
parser.add_argument('--temporal-settings')
arguments = parser.parse_args()

metadata = json.loads(Path(arguments.metadata).read_text())
temporal_settings = json.loads(Path(arguments.temporal_settings).read_text()) if arguments.temporal_settings else {}
state = State(arguments.state)
session = str(uuid.uuid4())
analyzers = TemporalPipeline(build_analyzers(temporal_settings))

import gi  # noqa: E402
gi.require_version('Gst', '1.0')
from gi.repository import GLib, Gst  # noqa: E402

Gst.init(None)
pipeline = Gst.Pipeline.new('visionops')
loop = GLib.MainLoop()


def element(factory, name):
    item = Gst.ElementFactory.make(factory, name)
    if item is None:
        raise RuntimeError('Missing GStreamer element ' + factory)
    pipeline.add(item)
    return item


source = element('uridecodebin', 'source')
source.set_property('uri', arguments.source if arguments.source.startswith('rtsp://')
                    else Path(arguments.source).resolve().as_uri())
mux = element('nvstreammux', 'mux')
mux.set_property('width', 640)
mux.set_property('height', 640)
mux.set_property('batch-size', 1)
mux.set_property('batched-push-timeout', 100000)
mux.set_property('live-source', arguments.source.startswith('rtsp://'))
convert = element('nvvideoconvert', 'convert')
infer = element('nvinfer', 'infer')
infer.set_property('config-file-path', arguments.nvinfer_config)
tracker = element('nvtracker', 'tracker')
tracker.set_property('ll-lib-file', arguments.tracker_library)
tracker.set_property('ll-config-file', arguments.tracker_config)
tracker.set_property('tracker-width', 640)
tracker.set_property('tracker-height', 384)
sink = element('fakesink', 'sink')
sink.set_property('sync', True)
for left, right in ((mux, convert), (convert, infer), (infer, tracker), (tracker, sink)):
    if not left.link(right):
        raise RuntimeError('GStreamer link failed')


def on_pad_added(_, pad):
    caps = pad.get_current_caps()
    if caps and caps.get_structure(0).get_name().startswith('video'):
        if pad.link(mux.request_pad_simple('sink_0')) != Gst.PadLinkReturn.OK:
            raise RuntimeError('Decode pad link failed')


source.connect('pad-added', on_pad_added)


def publish(event, track_id, observed):
    span = float(event.pop('span_seconds'))
    state.enqueue({**metadata, **event, 'kind': 'safety', 'safety_event_id': str(uuid.uuid4()),
                   'track_id': str(track_id), 'stream_session_id': session,
                   'observed_at': observed.isoformat(),
                   'window_start': (observed - timedelta(seconds=span)).isoformat(),
                   'window_end': observed.isoformat()})


def probe(_, info, __):
    buffer = info.get_buffer()
    if buffer:
        for frame in detections(buffer, 640, 640):
            decoded = time.monotonic()
            observed = datetime.now(timezone.utc)
            heads = [(CANONICAL_CLASSES[d['class_id']], d['confidence'], d['bbox'])
                     for d in frame['detections'] if d['class_id'] in (1, 2)]
            for person in (d for d in frame['detections'] if d['class_id'] == 0):
                label, confidence = associate(person['bbox'], heads)
                observation = TrackObservation(session=session, track_id=person['track_id'],
                                               timestamp=decoded, observed_at=observed,
                                               bbox=person['bbox'], label=label, confidence=confidence,
                                               frame_sequence=frame['frame_sequence'])
                for event in analyzers.observe(observation):
                    publish(event, person['track_id'], observed)
            analyzers.retain(session, [d['track_id'] for d in frame['detections'] if d['class_id'] == 0])
    return Gst.PadProbeReturn.OK


tracker.get_static_pad('src').add_probe(Gst.PadProbeType.BUFFER, probe, None)
errors = []


def on_message(_, message):
    if message.type == Gst.MessageType.ERROR:
        errors.append(str(message.parse_error()[0]))
        loop.quit()
    elif message.type == Gst.MessageType.EOS:
        loop.quit()


bus = pipeline.get_bus()
bus.add_signal_watch()
bus.connect('message', on_message)
try:
    pipeline.set_state(Gst.State.PLAYING)
    loop.run()
finally:
    pipeline.set_state(Gst.State.NULL)
if errors:
    raise RuntimeError(errors[0])
