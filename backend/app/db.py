"""PostgreSQL-only central persistence; no SQLite fallback."""
import os, uuid
from datetime import datetime, timezone
from sqlalchemy import create_engine, Column, String, Integer, Float, Boolean, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base, sessionmaker

def now(): return datetime.now(timezone.utc)
def uid(): return str(uuid.uuid4())
url = os.environ.get('DATABASE_URL', 'postgresql+psycopg://visionops:configure-password@localhost:5432/visionops')
if not url.startswith('postgresql'): raise RuntimeError('Central database must be PostgreSQL')
engine = create_engine(url, pool_pre_ping=True)
Session = sessionmaker(engine, expire_on_commit=False)
Base = declarative_base()
class Common:
    created_at = Column(DateTime(timezone=True), default=now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=now, onupdate=now, nullable=False)
def entity(name, key, fields, unique=()):
    attrs={'__tablename__':name, key:Column(String(36),primary_key=True,default=uid)}
    attrs.update(fields)
    if unique: attrs['__table_args__']=tuple(UniqueConstraint(*u) for u in unique)
    return type(name.title().replace('_',''), (Common,Base), attrs)
def S(default=None, nullable=False): return Column(String, default=default, nullable=nullable)
def J(default=dict): return Column(JSONB, default=default, nullable=False)
def I(default=0): return Column(Integer,default=default,nullable=False)
def D(): return Column(DateTime(timezone=True),nullable=True)
def F(table,key,nullable=False): return Column(String(36),ForeignKey(f'{table}.{key}'),nullable=nullable,index=True)
User=entity('users','user_id',dict(email=Column(String,unique=True,nullable=False),password_hash=S(),role=S(),status=S('active')))
UserSession=entity('sessions','session_id',dict(user_id=F('users','user_id'),token_hash=Column(String,unique=True),csrf_hash=S(),expires_at=D()))
Site=entity('sites','site_id',dict(name=S(),timezone=S('UTC'),status=S('active')))
Config=entity('config_versions','config_version_id',dict(hardware_profile=S(),schema_version=I(1),sha256=S(),settings=J(),created_by=F('users','user_id',True)))
Model=entity('models','model_id',dict(name=Column(String,unique=True),task=S('ppe_detection'),class_map=J(),status=S('active')))
Dataset=entity('dataset_versions','dataset_version_id',dict(name=S(),git_commit=S(),dvc_hash=S(),dvc_remote_ref=S(),taxonomy=J(),split_manifest_hash=S(),license_ref=S(),validation_evidence_ref=S(),status=S('draft')))
Training=entity('training_runs','training_run_id',dict(dataset_version_id=F('dataset_versions','dataset_version_id'),mlflow_run_id=S(),code_commit=S(),parameters=J(),status=S(),started_at=D(),finished_at=D()))
Version=entity('model_versions','model_version_id',dict(model_id=F('models','model_id'),training_run_id=F('training_runs','training_run_id'),mlflow_model_name=S(),mlflow_model_version=S(),version_label=S(),status=S('registered')), [('model_id','version_label')])
ModelArtifact=entity('model_artifacts','model_artifact_id',dict(model_version_id=F('model_versions','model_version_id'),format=S(),precision=S(),hardware_profile=S(),sha256=S(),size_bytes=I(),storage_key=S(),input_shape=J(list),class_map=J(),compatibility=J(),status=S('ready'),model_contract_profile=S(nullable=True)))
RuntimeArtifact=entity('runtime_artifacts','runtime_artifact_id',dict(version_label=S(),hardware_profile=S(),sha256=S(),size_bytes=I(),storage_key=S(),entrypoint=S('worker'),compatibility=J(),status=S('ready')))
Evaluation=entity('evaluation_reports','evaluation_report_id',dict(model_version_id=F('model_versions','model_version_id'),model_artifact_id=F('model_artifacts','model_artifact_id'),dataset_version_id=F('dataset_versions','dataset_version_id'),hardware_profile=S(),evidence_mode=S(),metrics=J(),gate_policy=J(),result=S(),evidence_ref=S()))
Release=entity('releases','release_id',dict(model_artifact_id=F('model_artifacts','model_artifact_id'),runtime_artifact_id=F('runtime_artifacts','runtime_artifact_id'),config_version_id=F('config_versions','config_version_id'),evaluation_report_id=F('evaluation_reports','evaluation_report_id'),hardware_profile=S(),evidence_mode=S(),manifest_sha256=S(),manifest=J(),signature=S(nullable=True),key_id=S(nullable=True),approved_by=F('users','user_id',True),approved_at=D(),status=S('draft')))
Campaign=entity('deployment_campaigns','deployment_campaign_id',dict(release_id=F('releases','release_id'),mode=S(),target_device_ids=J(list),status=S('draft'),current_ring=I(),ring_started_at=D(),observation_started_at=D(),failure_windows=I(),created_by=F('users','user_id'),reason=S(),policy_snapshot=J()))
Device=entity('devices','device_id',dict(site_id=F('sites','site_id'),name=S(),mode=S(),hardware_profile=S(),registration_status=S('registered'),credential_hash=Column(String,nullable=True,index=True),last_boot_id=S(nullable=True),last_sequence=I(-1),last_heartbeat_at=D(),desired_generation=I(1),desired_release_id=F('releases','release_id',True),desired_config_version_id=F('config_versions','config_version_id',True),actual_release_id=F('releases','release_id',True),actual_config_version_id=F('config_versions','config_version_id',True),applied_generation=I(),agent_state=S('unconfigured'),health_status=S('unknown'),active_deployment_campaign_id=F('deployment_campaigns','deployment_campaign_id',True)))
Camera=entity('cameras','camera_id',dict(site_id=F('sites','site_id'),device_id=F('devices','device_id'),name=S(),status=S('active')))
Source=entity('video_sources','video_source_id',dict(camera_id=Column(String(36),ForeignKey('cameras.camera_id'),unique=True,nullable=False),kind=S(),locator=S(),credential_ref=S(nullable=True),enabled=Column(Boolean,default=True,nullable=False),loop=Column(Boolean,default=False,nullable=False),source_revision=I(1)))
Enrollment=entity('enrollment_tokens','enrollment_token_id',dict(device_id=F('devices','device_id'),token_hash=Column(String,unique=True),expires_at=D(),consumed_at=D()))
Assignment=entity('device_assignments','device_assignment_id',dict(device_id=F('devices','device_id'),generation=I(),release_id=F('releases','release_id'),config_version_id=F('config_versions','config_version_id'),reason=S(),deployment_campaign_id=F('deployment_campaigns','deployment_campaign_id',True),created_by=S()), [('device_id','generation')])
Boot=entity('device_boots','device_boot_id',dict(device_id=F('devices','device_id'),boot_id=S(),retired_at=D()),[('device_id','boot_id')])
Heartbeat=entity('device_heartbeats','heartbeat_id',dict(device_id=F('devices','device_id'),boot_id=S(),sequence=I(),observed_at=D(),received_at=D(),actual_release_id=F('releases','release_id',True),actual_config_version_id=F('config_versions','config_version_id',True),applied_generation=I(),agent_state=S(),health_status=S(),source_states=J(list),capabilities=J(),rejected_generation=Column(Integer),last_error_code=S(nullable=True)),[('device_id','boot_id','sequence')])
Metric=entity('metric_summaries','metric_summary_id',dict(device_id=F('devices','device_id'),camera_id=F('cameras','camera_id',True),release_id=F('releases','release_id',True),window_start=D(),window_end=D(),sample_count=I(),evidence_mode=S(),values=J(),received_at=D()))
Target=entity('deployment_targets','deployment_target_id',dict(deployment_campaign_id=F('deployment_campaigns','deployment_campaign_id'),device_id=F('devices','device_id'),ring=I(),status=S('pending'),previous_release_id=F('releases','release_id',True),previous_config_version_id=F('config_versions','config_version_id',True),assigned_generation=Column(Integer),rollback_generation=Column(Integer),baseline_metrics=J(),permit_expires_at=D(),assigned_config_version_id=F('config_versions','config_version_id',True),last_error_code=S(nullable=True)),[('deployment_campaign_id','device_id')])
DeploymentEvent=entity('deployment_events','deployment_event_id',dict(deployment_campaign_id=F('deployment_campaigns','deployment_campaign_id'),device_id=F('devices','device_id',True),actor_type=S(),actor_id=S(),event_type=S(),generation=Column(Integer),before_state=J(),after_state=J(),reason=S(),evidence=J()))
event_fields=dict(device_id=F('devices','device_id'),camera_id=F('cameras','camera_id'),stream_session_id=S(),observed_at=D(),received_at=D(),release_id=F('releases','release_id'),model_version_id=F('model_versions','model_version_id'),config_version_id=F('config_versions','config_version_id'),evidence_mode=S(),payload_sha256=S())
# Columns cannot be shared between tables.
import copy
Detection=entity('detection_events','detection_event_id',{**{k:c._copy() for k,c in event_fields.items()},'frame_sequence':I(),'detections':J(list)})
Safety=entity('safety_events','safety_event_id',{**{k:c._copy() for k,c in event_fields.items()},'track_id':S(),'event_type':S(),'window_start':D(),'window_end':D(),'supporting_frames':I(),'confidence':Column(Float),'bbox':J(list),'review_status':S('new'),'acknowledged_by':F('users','user_id',True),'acknowledged_at':D(),'evidence_key':S(nullable=True)})
Audit=entity('audit_events','audit_event_id',dict(actor_type=S(),actor_id=S(),action=S(),entity_type=S(),entity_id=S(),reason=S(''),request_id=S()))
Idempotency=entity('idempotency_records','idempotency_record_id',dict(user_id=F('users','user_id'),route=S(),key=S(),request_sha256=S(),response_status=I(),response_body=J(),expires_at=D()),[('user_id','route','key')])
SECRET={'password_hash','token_hash','csrf_hash','credential_hash','storage_key','evidence_key','payload_sha256'}
def public(row):
    result={c.name:getattr(row,c.name) for c in row.__table__.columns if c.name not in SECRET}
    if isinstance(row,Device):
        age=(now()-row.last_heartbeat_at).total_seconds() if row.last_heartbeat_at else None
        result.update(last_seen_age_seconds=age,connectivity='never_seen' if age is None else 'online' if age<=60 else 'offline')
    if isinstance(row,Safety): result['evidence_available']=bool(row.evidence_key) and (now()-row.created_at).days<7
    return result
Drift=entity('drift_signals','drift_signal_id',dict(camera_id=F('cameras','camera_id'),model_version_id=F('model_versions','model_version_id'),reference_dataset_version_id=F('dataset_versions','dataset_version_id'),window_start=D(),window_end=D(),reference_window_start=D(),reference_window_end=D(),sample_count=I(),method=S(),score=Column(Float),threshold=Column(Float),status=S(),reason=S(nullable=True),reference_histogram=J(list),current_histogram=J(list)),[('camera_id','model_version_id','window_start','window_end','method')])
HardExample=entity('hard_examples','hard_example_id',dict(device_id=F('devices','device_id'),camera_id=F('cameras','camera_id'),safety_event_id=F('safety_events','safety_event_id',True),drift_signal_id=F('drift_signals','drift_signal_id',True),evidence_key=S(),observed_at=D(),release_id=F('releases','release_id'),confidence=Column(Float),capture_reason=S(),labels=J(list),review_reason=S(nullable=True),status=S('captured'),reviewed_by=F('users','user_id',True),exported_dataset_version_id=F('dataset_versions','dataset_version_id',True)))
Retraining=entity('retraining_requests','retraining_request_id',dict(drift_signal_id=F('drift_signals','drift_signal_id'),hard_example_ids=J(list),reason=S(),requested_by=F('users','user_id'),status=S('requested'),training_run_id=F('training_runs','training_run_id',True)))
