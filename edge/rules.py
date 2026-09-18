"""Deterministic positive bare-head evidence, never absence-of-helmet inference."""
from dataclasses import dataclass
@dataclass
class Window:
    start: float
    last: float
    count: int
    confidence: float
class SafetyRule:
    def __init__(self):self.windows={};self.cooldowns={}
    def observe(self,session,track,stamp,label,confidence,bbox):
        key=(session,str(track));w=self.windows.get(key)
        if label!='no_helmet':self.windows.pop(key,None);return None
        if not w or stamp-w.last>1:w=Window(stamp,stamp,0,confidence);self.windows[key]=w
        w.last=stamp;w.count+=1;w.confidence=min(w.confidence,confidence)
        if w.count>=5 and stamp-w.start>=2 and stamp-self.cooldowns.get(key,-1e9)>=30:
            self.cooldowns[key]=stamp
            return {'track_id':str(track),'supporting_frames':w.count,'confidence':w.confidence,'bbox':bbox,'span_seconds':stamp-w.start}
        return None
    def retain(self,session,tracks):
        allowed={(session,str(t)) for t in tracks}
        self.windows={k:v for k,v in self.windows.items() if k in allowed}
        self.cooldowns={k:v for k,v in self.cooldowns.items() if k in allowed}

def associate(person,heads):
    x,y,X,Y=person; candidates=[]
    for label,confidence,box in heads:
        a,b,A,B=box;cx=(a+A)/2;cy=(b+B)/2
        if not(x<=cx<=X and y<=cy<=y+.35*(Y-y)):continue
        ratio=max(0,min(X,A)-max(x,a))*max(0,min(Y,B)-max(y,b))/max((A-a)*(B-b),1e-12)
        if ratio>=.5:candidates.append((ratio,label,confidence))
    if not candidates or len({c[1] for c in candidates})>1:return 'unknown',0.
    candidates.sort(reverse=True)
    if len(candidates)>1 and abs(candidates[0][0]-candidates[1][0])<1e-9:return 'unknown',0.
    return candidates[0][1],candidates[0][2]
