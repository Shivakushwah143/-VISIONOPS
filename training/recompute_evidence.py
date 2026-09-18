"""Recompute reports from real collected raw evidence; never generate measurements."""
import argparse,json
from pathlib import Path
from .quality import measured,gate
p=argparse.ArgumentParser();p.add_argument('--evidence',required=True);p.add_argument('--output',required=True);p.add_argument('--tracking-uri');a=p.parse_args()
evidence=json.loads(Path(a.evidence).read_text());metrics=measured(evidence);reasons=gate(metrics);report={'metrics':metrics,'failed_checks':reasons,'result':'failed' if reasons else 'passed','qualification_note':'Protocol/parity/lineage are additionally enforced by the backend importer'}
Path(a.output).write_text(json.dumps(report,indent=2))
if a.tracking_uri:
    import mlflow
    mlflow.set_tracking_uri(a.tracking_uri);mlflow.set_experiment('visionops-evaluation')
    with mlflow.start_run(tags={'run_kind':'real_evaluation'}) as run:
        mlflow.log_dict(evidence,'evaluation/evidence.json');mlflow.log_dict(report,'evaluation/report.json');print('mlflow:'+run.info.run_id+'/evaluation/evidence.json')
