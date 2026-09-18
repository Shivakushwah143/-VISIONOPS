"""YOLO11n training with explicit local dataset and MLflow lineage."""
import argparse,json,hashlib,subprocess
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--data',required=True);p.add_argument('--dataset-manifest',required=True);p.add_argument('--base-weights',required=True,help='Locally verified YOLO11n .pt path');p.add_argument('--epochs',type=int,default=30);p.add_argument('--device',default='cpu');p.add_argument('--tracking-uri',required=True);p.add_argument('--output',default='var/training');a=p.parse_args()
import yaml,mlflow
from ultralytics import YOLO
manifest=json.loads(Path(a.dataset_manifest).read_text());data=yaml.safe_load(Path(a.data).read_text())
names=list(data['names'].values()) if isinstance(data['names'],dict) else data['names']
if names!=['person','helmet','no_helmet']:raise ValueError('taxonomy mismatch')
if not manifest.get('license') or not manifest.get('split_manifest_sha256'):raise ValueError('validated license and split manifest required')
mlflow.set_tracking_uri(a.tracking_uri);mlflow.set_experiment('visionops-ppe')
with mlflow.start_run() as run:
    mlflow.log_params({'architecture':'yolo11n','epochs':a.epochs,'device':a.device,'dataset_manifest_sha256':hashlib.sha256(Path(a.dataset_manifest).read_bytes()).hexdigest(),'base_weights_sha256':hashlib.sha256(Path(a.base_weights).read_bytes()).hexdigest()})
    model=YOLO(a.base_weights);result=model.train(data=a.data,epochs=a.epochs,imgsz=640,device=a.device,project=a.output,name=run.info.run_id,seed=42,deterministic=True)
    mlflow.log_metrics({k:float(v) for k,v in result.results_dict.items()});onnx=model.export(format='onnx',imgsz=640,dynamic=False,simplify=False,opset=17);mlflow.log_artifact(onnx,'model');mlflow.log_artifact(a.dataset_manifest,'dataset');print(json.dumps({'mlflow_run_id':run.info.run_id,'onnx':onnx}))
