"""Real PPE developer journey. Does not bypass signed fleet-release approval."""
import argparse,json
from edge.pipeline import Detector,Pipeline,Source
from edge.state import State
p=argparse.ArgumentParser();p.add_argument('--model',required=True);p.add_argument('--sha256',required=True);p.add_argument('--video',required=True);p.add_argument('--metadata',required=True,help='JSON file with registered camera/release/model/config IDs');p.add_argument('--state',required=True);p.add_argument('--preview');p.add_argument('--external-baseline',action='store_true');a=p.parse_args()
d=Detector(a.model,a.sha256,{11:0,3:1,8:2} if a.external_baseline else None);pipeline=Pipeline(d,Source(a.video),json.load(open(a.metadata)),State(a.state),a.preview);pipeline.run()
print(json.dumps({'inference_frames':pipeline.processed,'latency_ms':pipeline.latencies,'dropped_frames':pipeline.source.dropped,'source_status':pipeline.source.status}))
