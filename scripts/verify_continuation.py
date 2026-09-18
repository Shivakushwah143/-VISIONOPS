"""Actual subprocess/SQLite recovery and production allocator component exercises.
No central DB or fleet integration claim is made by this experiment.
"""
import json,tempfile,time
from pathlib import Path
from datetime import datetime,timezone,timedelta
from types import SimpleNamespace
from edge.agent import Agent
from backend.app.permits import allocate

def permits():
    stamp=datetime.now(timezone.utc);targets=[];devices={}
    for i in range(10):
        id=str(i);targets.append(SimpleNamespace(device_id=id,assigned_generation=2,rollback_generation=3,previous_release_id='v1',previous_config_version_id='c1',assigned_config_version_id='c2',permit_expires_at=stamp+timedelta(minutes=10)))
        devices[id]=SimpleNamespace(applied_generation=2,actual_release_id='v2',actual_config_version_id='c2',desired_release_id='v1',last_heartbeat_at=stamp,agent_state='idle')
    count=allocate(targets,devices,stamp,rollback=True)
    if count!=5:raise RuntimeError('rollback permit over-allocation')
    devices['0'].applied_generation=3;devices['0'].actual_release_id='v1';devices['0'].actual_config_version_id='c1'
    count=allocate(targets,devices,stamp,rollback=True)
    if count!=5 or targets[0].permit_expires_at is not None or targets[5].permit_expires_at is None:raise RuntimeError('permit not transferred after convergence')
    devices['1'].last_heartbeat_at=stamp-timedelta(seconds=61)
    allocate(targets,devices,stamp,rollback=True,paused=True)
    if targets[1].permit_expires_at is not None:raise RuntimeError('offline lease retained')
    return {'status':'VERIFIED','scope':'production allocator component; no PostgreSQL transaction exercised','maximum_observed_active_leases':5}

def watchdog():
    with tempfile.TemporaryDirectory(prefix='visionops-watchdog-') as temp:
        root=Path(temp);agent=Agent(root/'agent','http://localhost:8000',root/'trust')
        good=root/'good';bad=root/'bad'
        for slot in (good,bad):(slot/'bundle').mkdir(parents=True)
        (bad/'bundle/worker.py').write_text('raise SystemExit(17)\n')
        (good/'bundle/worker.py').write_text("import argparse,time,json\nfrom pathlib import Path\np=argparse.ArgumentParser();p.add_argument('--slot');p.add_argument('--state');p.add_argument('--status');a=p.parse_args()\nwhile True:\n Path(a.status).write_text(json.dumps({'ready':True,'cameras':[]}));time.sleep(.1)\n")
        previous={'generation':1,'release_id':'v1','config_version_id':'c1','slot':str(good)};candidate={'generation':2,'release_id':'v2','config_version_id':'c2','slot':str(bad)}
        agent.state.put_many({'actual':candidate,'last_good':previous});agent.start_worker(candidate);started=time.monotonic()
        try:
            while time.monotonic()-started<75:
                agent.supervise()
                if agent.phase=='healthy' and agent.state.get('actual')['release_id']=='v1':break
                time.sleep(.1)
            else:raise RuntimeError('watchdog recovery deadline')
            actual=agent.state.get('actual')
            if actual['generation']!=2 or agent.state.get('rejected_generation')!=2:raise RuntimeError('local rollback generation contract')
            if not agent.process_alive():raise RuntimeError('last-good worker not running')
            result={'status':'VERIFIED','scope':'real child-process crashes and restart/backoff/60-second recovery with SQLite; engineering worker fixture, not CV or fleet','crashes':len(agent.state.get('watchdog')['crashes']),'restored_release':actual['release_id'],'retained_applied_generation':actual['generation'],'rejected_generation':agent.state.get('rejected_generation'),'elapsed_seconds':time.monotonic()-started}
        finally:agent.stop_worker();agent.client.close();agent.state.db.close()
        return result
if __name__=='__main__':
    report={'rollback_permits':permits(),'post_commit_watchdog':watchdog()};Path('docs/evidence/continuation-runtime.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
