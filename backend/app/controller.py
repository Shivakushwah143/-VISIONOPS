import time, json
from .campaigns import tick
if __name__=='__main__':
    while True:
        try:tick()
        except Exception as exc:print(json.dumps({'level':'error','event':'controller_tick_failed','error_type':type(exc).__name__}),flush=True)
        time.sleep(15)
