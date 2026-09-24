"""Expose persisted device observations without inventing unavailable values."""
import math
from prometheus_client.core import GaugeMetricFamily
from sqlalchemy import select
from .db import Session,Device,Heartbeat,Metric,Campaign,now
# Stream health and rollout inputs are exported when reported. A field the device did
# not report is omitted, never written as zero; GPU values appear only when measured.
FIELDS={'input_fps':'Input frames per second','inference_fps':'Inference frames per second','processed_fps':'Inference frames per second actually processed','decode_fps':'Decoded frames per second','queue_depth':'Decoder queue depth','outbox_depth':'Durable upload queue depth','dropped_frames':'Reported cumulative dropped frames','reconnect_count':'Reported cumulative reconnects','rtsp_connected':'Whether the source is connected and fresh','stream_age_seconds':'Age of the last decoded frame','inference_latency_ms_p95':'Window p95 inference latency','rss_bytes':'Reported resident memory bytes','cpu_percent':'Reported CPU percentage','gpu_utilization_ratio':'GPU utilization ratio, only when a provider measured it'}
class PipelineMetrics:
    def collect(self):
        available=GaugeMetricFamily('visionops_database_observations_available','Whether the persisted observation query completed')
        try:
            with Session() as db:
                inventory=list(db.scalars(select(Device)))
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
        # Fleet roll-ups computed from persisted device rows at scrape time. Liveness uses
        # the same 60 s server-receipt rule as the API's derived connectivity; a device that
        # has never reported is neither healthy nor unreachable, it is never_seen.
        stamp=now(); fresh=[d for d in inventory if d.last_heartbeat_at and (stamp-d.last_heartbeat_at).total_seconds()<=60]
        never_seen=sum(d.last_heartbeat_at is None for d in inventory)
        online=len(fresh); offline=len(inventory)-online-never_seen
        total=GaugeMetricFamily('visionops_fleet_devices_total','Persisted logical device records')
        total.add_metric([],len(inventory)); yield total
        connectivity=GaugeMetricFamily('visionops_fleet_devices','Logical devices by derived connectivity',labels=['state'])
        for state,value in (('online',online),('offline',offline),('never_seen',never_seen)):connectivity.add_metric([state],value)
        yield connectivity
        healthy=GaugeMetricFamily('visionops_fleet_healthy_devices','Devices with a fresh heartbeat reporting healthy state');healthy.add_metric([],sum(d.health_status=='healthy' for d in fresh));yield healthy
        unreachable=GaugeMetricFamily('visionops_fleet_unreachable_devices','Devices whose last heartbeat is older than the 60 s liveness threshold');unreachable.add_metric([],offline);yield unreachable
        stale=GaugeMetricFamily('visionops_fleet_stale_heartbeat_devices','Devices with a heartbeat older than a threshold',labels=['threshold'])
        for seconds in (60,300):stale.add_metric([f'{seconds}s'],sum(d.last_heartbeat_at is not None and (stamp-d.last_heartbeat_at).total_seconds()>seconds for d in inventory))
        yield stale
