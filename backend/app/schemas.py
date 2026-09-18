from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator
class Strict(BaseModel): model_config=ConfigDict(extra='forbid')
class Login(Strict):
    email: str
    password: str
class SiteInput(Strict):
    name: str=Field(min_length=1,max_length=120)
    timezone: str='UTC'
class DeviceInput(Strict):
    site_id: UUID
    name: str=Field(min_length=1,max_length=120)
    mode: Literal['real','simulated']
    hardware_profile: str
    release_id: UUID
class CameraInput(Strict):
    site_id: UUID
    device_id: UUID
    name: str=Field(min_length=1,max_length=120)
class SourceInput(Strict):
    camera_id: UUID
    kind: Literal['file','rtsp']
    locator: str
    credential_ref: str|None=None
    enabled: bool=True
    loop: bool=False
class MetricInput(Strict):
    metric_summary_id: UUID
    camera_id: UUID|None=None
    release_id: UUID|None=None
    window_start: datetime
    window_end: datetime
    sample_count: int=Field(ge=0,le=1024)
    values: dict
    @model_validator(mode='after')
    def valid(self):
        samples=self.values.get('inference_latency_ms',[])
        if len(samples)>1024 or any(not isinstance(v,(float,int)) or not 0<=v<1e7 for v in samples): raise ValueError('invalid_latency_samples')
        if self.window_end<=self.window_start: raise ValueError('invalid_window')
        return self
class HeartbeatInput(Strict):
    device_id: UUID
    boot_id: UUID
    sequence: int=Field(ge=0)
    observed_at: datetime
    actual_release_id: UUID|None=None
    actual_config_version_id: UUID|None=None
    applied_generation: int=Field(ge=0)
    agent_state: Literal['unconfigured','idle','fetching','validating','staging','activating','observing','healthy','rolling_back','degraded']
    health_status: Literal['unknown','healthy','degraded','unhealthy']
    source_states: list[dict]=Field(default_factory=list,max_length=4)
    capabilities: dict=Field(default_factory=dict)
    metric_summaries: list[MetricInput]=Field(default_factory=list,max_length=4)
    rejected_generation: int|None=None
    last_error_code: str|None=None
class SafetyInput(Strict):
    kind: Literal['safety']
    safety_event_id: UUID
    camera_id: UUID
    stream_session_id: UUID
    track_id: str
    event_type: Literal['no_helmet_violation']
    observed_at: datetime
    window_start: datetime
    window_end: datetime
    supporting_frames: int=Field(ge=5)
    confidence: float=Field(ge=0,le=1)
    bbox: list[float]=Field(min_length=4,max_length=4)
    release_id: UUID
    model_version_id: UUID
    config_version_id: UUID
    @model_validator(mode='after')
    def valid(self):
        x,y,X,Y=self.bbox
        if not (0<=x<X<=1 and 0<=y<Y<=1): raise ValueError('bbox')
        if (self.window_end-self.window_start).total_seconds()<2: raise ValueError('event_span')
        return self
class DetectionInput(Strict):
    kind: Literal['detection']
    detection_event_id: UUID
    camera_id: UUID
    stream_session_id: UUID
    frame_sequence: int=Field(ge=0)
    observed_at: datetime
    release_id: UUID
    model_version_id: UUID
    config_version_id: UUID
    detections: list[dict]=Field(max_length=200)
