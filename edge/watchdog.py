"""Persisted 3-crashes/5-minute supervision policy with bounded restart backoff."""
import time
class Watchdog:
    def __init__(self,state):self.state=state
    def failure(self,generation,stamp=None):
        stamp=time.time() if stamp is None else stamp
        record=self.state.get('watchdog',{})
        crashes=[v for v in record.get('crashes',[]) if stamp-300<=v<=stamp] if record.get('generation')==generation else []
        crashes.append(stamp)
        action='rollback' if len(crashes)>=3 else 'restart'
        retry_at=stamp+min(30,2**(len(crashes)-1))
        self.state.put('watchdog',{'generation':generation,'crashes':crashes,'retry_at':retry_at,'action':action})
        return action,retry_at
    def reset(self):self.state.put('watchdog',{})
