"""DeepStream file/RTSP -> nvinfer -> NvDCF -> canonical rule -> SQLite outbox.
Requires actual DeepStream, pyds, GStreamer, a compiled parser and qualified engine.
"""
import argparse,json,time,uuid,sys
from pathlib import Path
from datetime import datetime,timezone,timedelta
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
import gi
gi.require_version('Gst','1.0')
from gi.repository import Gst,GLib
from bridge import detections
from edge.rules import SafetyRule,associate
from edge.state import State
p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--nvinfer-config',required=True);p.add_argument('--tracker-library',required=True);p.add_argument('--tracker-config',required=True);p.add_argument('--metadata',required=True);p.add_argument('--state',required=True);a=p.parse_args()
metadata=json.loads(Path(a.metadata).read_text());state=State(a.state);session=str(uuid.uuid4());rule=SafetyRule();Gst.init(None);pipeline=Gst.Pipeline.new('visionops');loop=GLib.MainLoop()
def element(factory,name):
    e=Gst.ElementFactory.make(factory,name)
    if e is None:raise RuntimeError('Missing GStreamer element '+factory)
    pipeline.add(e);return e
source=element('uridecodebin','source');source.set_property('uri',a.source if a.source.startswith('rtsp://') else Path(a.source).resolve().as_uri())
mux=element('nvstreammux','mux');mux.set_property('width',640);mux.set_property('height',640);mux.set_property('batch-size',1);mux.set_property('batched-push-timeout',100000);mux.set_property('live-source',a.source.startswith('rtsp://'))
convert=element('nvvideoconvert','convert');infer=element('nvinfer','infer');infer.set_property('config-file-path',a.nvinfer_config)
tracker=element('nvtracker','tracker');tracker.set_property('ll-lib-file',a.tracker_library);tracker.set_property('ll-config-file',a.tracker_config);tracker.set_property('tracker-width',640);tracker.set_property('tracker-height',384)
sink=element('fakesink','sink');sink.set_property('sync',True)
for left,right in ((mux,convert),(convert,infer),(infer,tracker),(tracker,sink)):
    if not left.link(right):raise RuntimeError('GStreamer link failed')
def pad_added(_,pad):
    caps=pad.get_current_caps()
    if caps and caps.get_structure(0).get_name().startswith('video'):
        if pad.link(mux.request_pad_simple('sink_0'))!=Gst.PadLinkReturn.OK:raise RuntimeError('Decode pad link failed')
source.connect('pad-added',pad_added)
def probe(_,info,user_data):
    buffer=info.get_buffer()
    if buffer:
        for frame in detections(buffer,640,640):
            stamp=time.monotonic();observed=datetime.now(timezone.utc);heads=[('helmet' if d['class_id']==1 else 'no_helmet',d['confidence'],d['bbox']) for d in frame['detections'] if d['class_id'] in (1,2)];tracks=[]
            for person in (d for d in frame['detections'] if d['class_id']==0):
                tracks.append(person['track_id']);label,confidence=associate(person['bbox'],heads);event=rule.observe(session,person['track_id'],stamp,label,confidence,person['bbox'])
                if event:
                    span=event.pop('span_seconds');state.enqueue({**metadata,**event,'kind':'safety','safety_event_id':str(uuid.uuid4()),'stream_session_id':session,'event_type':'no_helmet_violation','observed_at':observed.isoformat(),'window_start':(observed-timedelta(seconds=span)).isoformat(),'window_end':observed.isoformat()})
            rule.retain(session,tracks)
    return Gst.PadProbeReturn.OK
tracker.get_static_pad('src').add_probe(Gst.PadProbeType.BUFFER,probe,None)
errors=[]
def message(_,msg):
    if msg.type==Gst.MessageType.ERROR:errors.append(str(msg.parse_error()[0]));loop.quit()
    elif msg.type==Gst.MessageType.EOS:loop.quit()
bus=pipeline.get_bus();bus.add_signal_watch();bus.connect('message',message)
try:pipeline.set_state(Gst.State.PLAYING);loop.run()
finally:pipeline.set_state(Gst.State.NULL)
if errors:raise RuntimeError(errors[0])
