"""Expose persisted device observations without inventing unavailable values."""
import math
from prometheus_client.core import GaugeMetricFamily
from sqlalchemy import select
from .db import Session,Device,Heartbeat,Metric,Campaign,now
FIELDS={'input_fps':'Input frames per second','inference_fps':'Inference frames per second','queue_depth':'Decoder queue depth','outbox_depth':'Durable upload queue depth','dropped_frames':'Reported cumulative dropped frames','reconnect_count':'Reported cumulative reconnects','rss_bytes':'Reported resident memory bytes','cpu_percent':'Reported CPU percentage'}
class PipelineMetrics:
    def collect(self):
        available=GaugeMetricFamily('visionops_database_observations_available','Whether the persisted observation query completed')
        try:
            with Session() as db:
                devices=list(db.scalars(select(Device).where(Device.last_heartbeat_at!=None)))
                windows=list(db.scalars(select(Metric).distinct(Metric.device_id,Metric.camera_id).order_by(Metric.device_id,Metric.camera_id,Metric.received_at.desc())))
                heartbeats=list(db.scalars(select(Heartbeat).distinct(Heartbeat.device_id).order_by(Heartbeat.device_id,Heartbeat.received_at.desc())))
                campaigns=list(db.scalars(select(Campaign)))
        except Exception:
            available.add_metric([],0);yield available;return
        available.add_metric([],1);yield available
        labels=['device_id','camera_id','mode'];families={k:GaugeMetricFamily('visionops_camera_'+k,v,labels=labels) for k,v in FIELDS.items()}
        age=GaugeMetricFamily('visionops_camera_observation_age_seconds','Age of last received metric window',labels=labels)
        quantile=GaugeMetricFamily('visionops_camera_inference_latency_ms','Nearest rank window latency quantile',labels=labels+['quantile'])
        for row in windows:
            tag=[row.device_id,row.camera_id or 'none',row.evidence_mode];seconds=max(0,(now()-row.received_at).total_seconds());age.add_metric(tag,seconds)
            if seconds>30:continue
            for field,family in families.items():
                value=row.values.get(field)
                if isinstance(value,(int,float)) and math.isfinite(value):family.add_metric(tag,value)
            values=sorted(v for v in row.values.get('inference_latency_ms',[]) if isinstance(v,(int,float)) and math.isfinite(v))
            if values:
                for q in (.5,.95):quantile.add_metric(tag+[str(q)],values[math.ceil(q*len(values))-1])
        yield age;yield quantile
        yield from families.values()
        health=GaugeMetricFamily('visionops_device_health','Fresh reported device health',labels=['device_id','mode','health'])
        heartbeat_age=GaugeMetricFamily('visionops_device_heartbeat_age_seconds','Server receipt age',labels=['device_id','mode'])
        for d in devices:
            seconds=max(0,(now()-d.last_heartbeat_at).total_seconds());heartbeat_age.add_metric([d.device_id,d.mode],seconds);health.add_metric([d.device_id,d.mode,d.health_status if seconds<=60 else 'unknown'],1)
        yield health;yield heartbeat_age
        versions=GaugeMetricFamily('visionops_device_version_info','Versions from actual device heartbeat',labels=['device_id','application_version','model_version','artifact_version'])
        for h in heartbeats:
            if (now()-h.received_at).total_seconds()<=60:versions.add_metric([h.device_id,*[str(h.capabilities.get(k) or 'unknown') for k in ('application_version','model_version','artifact_version')]],1)
        yield versions
        campaign=GaugeMetricFamily('visionops_campaign_status','Persisted campaign state',labels=['campaign_id','mode','status'])
        for c in campaigns:campaign.add_metric([c.deployment_campaign_id,c.mode,c.status],1)
        yield campaign
