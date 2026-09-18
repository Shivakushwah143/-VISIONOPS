"""Base-2 Jensen-Shannon drift screen; human review remains mandatory."""
import math,argparse,json
from pathlib import Path
def js(reference,current):
    if len(reference)!=len(current) or not reference or any(x<0 or not math.isfinite(x) for x in reference+current):raise ValueError('invalid_histogram')
    p=[(v+1e-6)/(sum(reference)+len(reference)*1e-6) for v in reference];q=[(v+1e-6)/(sum(current)+len(current)*1e-6) for v in current];m=[(a+b)/2 for a,b in zip(p,q)]
    return .5*sum(a*math.log2(a/b) for a,b in zip(p,m))+.5*sum(a*math.log2(a/b) for a,b in zip(q,m))
def screen(reference,current,previous_score=None):
    if min(sum(reference),sum(current))<500:return {'status':'insufficient_data','score':None}
    score=js(reference,current)
    return {'status':'pending_review' if score>.1 and previous_score is not None and previous_score>.1 else 'insufficient_data','score':score}
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--histograms',required=True);p.add_argument('--output',required=True);a=p.parse_args();d=json.loads(Path(a.histograms).read_text());Path(a.output).write_text(json.dumps(screen(d['reference'],d['current'],d.get('previous_score')),indent=2))
