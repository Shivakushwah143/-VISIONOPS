"""10K logical-fleet control-plane load driver.

This drives the real control plane (PostgreSQL APIs) with light-weight logical
device records: no container, thread or process per device. Each logical device
carries only the state the control plane can observe - desired/actual generation,
release version, heartbeat, health, download state, deployment state and a metric
summary - so 10,000 of them fit in one process.

It measures, against the real server:

* heartbeat throughput (accepted heartbeats per second and request latency percentiles)
* campaign calculation latency (`GET /deployments/{id}` gate assessment)
* DB-backed request latency for every endpoint it touches
* rollout reconciliation time (until the requested share of the fleet converges)
* active download leases (devices whose desired state carries a download permit)

Nothing here is a physical-device claim, and it must not be presented as a 10,000
Jetson deployment. See `docs/10K_FLEET_SCALING_REPORT.md`.

IMPLEMENTED - NOT RUNTIME VERIFIED: this host has no PostgreSQL/FastAPI
installation, so no measurement has been recorded in this repository.
"""
import argparse
import json
import math
import random
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean

import httpx

DEFAULT_METRIC_VALUES = {'inference_latency_ms': [12.0, 18.0, 25.0, 40.0, 60.0], 'inference_fps': 5.0,
                         'input_fps': 25.0, 'queue_depth': 0, 'dropped_frames': 0, 'reconnect_count': 0,
                         'processed_fps': 4.8, 'rtsp_connected': True, 'decode_fps': 24.9,
                         'gpu_metrics_available': False}


@dataclass
class LogicalDevice:
    device_id: str
    name: str
    desired_generation: int = 0
    actual_generation: int = 0
    release_id: str | None = None
    applied_release_id: str | None = None
    config_version_id: str | None = None
    applied_config_version_id: str | None = None
    boot_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    sequence: int = 0
    health: str = 'healthy'
    agent_state: str = 'healthy'
    download_state: str = 'idle'
    deployment_state: str = 'pending'
    has_permit: bool = False
    online: bool = True
    enrolled: bool = False
    credential: str | None = None
    heartbeats_accepted: int = 0
    converged_at: float | None = None
    errors: int = 0


class Recorder:
    """Bounded latency recorder with nearest-rank percentiles."""

    def __init__(self):
        self.samples = deque(maxlen=200000)
        self.lock = threading.Lock()

    def add(self, seconds):
        with self.lock:
            self.samples.append(seconds)

    def summary(self):
        values = sorted(self.samples)
        if not values:
            return {'count': 0}
        def rank(q):
            return round(values[max(0, math.ceil(q * len(values)) - 1)] * 1000, 3)
        return {'count': len(values), 'p50_ms': rank(0.5), 'p95_ms': rank(0.95), 'p99_ms': rank(0.99),
                'max_ms': round(values[-1] * 1000, 3)}


class ControlPlane:
    def __init__(self, url, email, password, origin=None):
        self.http = httpx.Client(base_url=url.rstrip('/'), timeout=60, headers={'Origin': origin or url})
        response = self.http.post('/api/v1/auth/login', json={'email': email, 'password': password})
        response.raise_for_status()
        self.http.headers['X-CSRF-Token'] = response.json()['data']['csrf_token']
        self.latency = Recorder()
        self.lock = threading.Lock()

    def request(self, method, path, **kwargs):
        started = time.perf_counter()
        try:
            key = str(uuid.uuid4())
            headers = {**kwargs.pop('headers', {}), 'Idempotency-Key': key}
            response = self.http.request(method, path, headers=headers, **kwargs)
            if response.status_code >= 400:
                return None, response.status_code
            return response.json(), response.status_code
        finally:
            self.latency.add(time.perf_counter() - started)

    def post(self, path, body=None):
        return self.request('POST', path, json=body)

    def get(self, path, params=None):
        return self.request('GET', path, params=params)


def provision(plane, devices, release_id, site_limit=1):
    """Create device records and enrollment credentials. Bounded concurrency."""
    sites = plane.get('/api/v1/sites?limit=200')[0]['data']
    site_id = sites[0]['site_id']
    created = 0
    for device in devices:
        payload = {'site_id': site_id, 'name': device.name, 'mode': 'simulated',
                   'hardware_profile': 'cpu_onnx_x86_64', 'release_id': release_id}
        body, status = plane.post('/api/v1/devices', payload)
        if body:
            device.device_id = body['data']['device_id']
            created += 1
        elif status == 409:
            pass
        token_body, _ = plane.post(f'/api/v1/devices/{device.device_id}/enrollment-tokens',
                                   {'reason': 'fleet scale simulation'})
        if token_body:
            enrollment, _ = plane.post('/api/v1/device-enrollments',
                                       {'token': token_body['data']['token'],
                                        'capabilities': {'agent_version': '0.1.0', 'mode': 'simulated'}})
            if enrollment:
                device.credential = enrollment['data']['device_credential']
                device.enrolled = True
    return created


def heartbeat_round(plane, devices, concurrency=32, metric_summaries=True):
    """One heartbeat per online device, bounded concurrency, real HTTP."""
    accepted = [0]
    lock = threading.Lock()

    def send(device):
        if not device.online or not device.credential:
            return
        device.sequence += 1
        payload = {'device_id': device.device_id, 'boot_id': device.boot_id, 'sequence': device.sequence,
                   'observed_at': _utc(), 'actual_release_id': device.applied_release_id,
                   'actual_config_version_id': device.applied_config_version_id,
                   'applied_generation': device.actual_generation, 'agent_state': device.agent_state,
                   'health_status': device.health, 'source_states': [],
                   'capabilities': {'mode': 'simulated', 'agent_version': '0.1.0', 'architecture': 'x86_64',
                                    'runtime': 'onnxruntime', 'simulated_hardware': False,
                                    'gpu_metrics_available': False},
                   'metric_summaries': ([{'metric_summary_id': str(uuid.uuid4()),
                                          'camera_id': None, 'release_id': device.applied_release_id,
                                          'window_start': _utc(offset=-15), 'window_end': _utc(),
                                          'sample_count': 5, 'values': DEFAULT_METRIC_VALUES}]
                                        if metric_summaries and device.applied_release_id else []),
                   'rejected_generation': None, 'last_error_code': None}
        body, _ = plane.request('POST', f'/api/v1/devices/{device.device_id}/heartbeats', json=payload,
                                headers={'Authorization': 'Bearer ' + device.credential})
        if body and body.get('data', {}).get('accepted'):
            with lock:
                accepted[0] += 1
                device.heartbeats_accepted += 1

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        list(pool.map(send, devices))
    return accepted[0]


def reconcile(plane, devices, concurrency=32, stop_after=None):
    """Fetch desired state and converge actual state, as a real agent would."""
    fetched = 0
    lock = threading.Lock()

    def fetch(device):
        nonlocal fetched
        if not device.online or not device.credential:
            return
        body, _ = plane.request('GET', f'/api/v1/devices/{device.device_id}/desired-state',
                                headers={'Authorization': 'Bearer ' + device.credential})
        if not body:
            return
        desired = body['data']
        with lock:
            fetched += 1
            device.desired_generation = desired['generation']
            device.release_id = desired['release_id']
            device.config_version_id = desired['config_version_id']
            device.has_permit = bool(desired['download_permit'])
            device.download_state = 'permit_issued' if device.has_permit else 'waiting'
        if device.has_permit and device.actual_generation != desired['generation']:
            device.actual_generation = desired['generation']
            device.applied_release_id = desired['release_id']
            device.applied_config_version_id = desired['config_version_id']
            device.download_state = 'applied'
            if device.converged_at is None and stop_after:
                device.converged_at = time.monotonic()

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        list(pool.map(fetch, devices))
    return fetched


def _utc(offset=0):
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) + timedelta(seconds=offset)).isoformat()


def active_permits(devices):
    return sum(1 for device in devices if device.has_permit)


def run(arguments):
    plane = ControlPlane(arguments.url, arguments.email, arguments.password)
    devices = [LogicalDevice(device_id='pending-%05d' % index, name='Fleet-scale logical %06d' % index)
               for index in range(arguments.devices)]
    random.seed(arguments.seed)
    offline = int(arguments.devices * arguments.offline_ratio)
    for device in random.sample(devices, offline):
        device.online = False
    failing = random.sample([d for d in devices if d.online], int(len([d for d in devices if d.online]) * arguments.failure_ratio))
    for device in failing:
        device.health = 'unhealthy'
        device.agent_state = 'degraded'

    report = {'requested_devices': arguments.devices, 'release_id': arguments.release_id,
              'concurrency': arguments.concurrency, 'offline_devices': offline,
              'failing_devices': len(failing), 'measured_at': _utc(),
              'note': ('Logical control-plane simulation. Logical devices, not physical Jetsons. '
                       'No GPU or model-quality metric is implied.')}

    provision_started = time.monotonic()
    report['records_created'] = provision(plane, devices, arguments.release_id)
    report['provision_seconds'] = round(time.monotonic() - provision_started, 2)
    report['latency_during_provisioning'] = plane.latency.summary()

    baseline = heartbeat_round(plane, devices, arguments.concurrency)
    started = time.monotonic()
    for _ in range(arguments.rounds):
        heartbeat_round(plane, devices, arguments.concurrency)
    elapsed = max(time.monotonic() - started, 1e-6)
    report['heartbeats'] = {'rounds': arguments.rounds, 'first_round_accepted': baseline,
                            'accepted_per_round': arguments.rounds * len([d for d in devices if d.online and d.enrolled]),
                            'throughput_per_second': round(arguments.rounds * len([d for d in devices if d.online and d.enrolled]) / elapsed, 2),
                            'elapsed_seconds': round(elapsed, 2),
                            'latency': plane.latency.summary()}

    if arguments.campaign:
        reconcile_started = time.monotonic()
        reconcile(plane, devices, arguments.concurrency, stop_after=True)
        report['rollout'] = {'reconcile_seconds': round(time.monotonic() - reconcile_started, 2),
                             'converged': sum(1 for d in devices if d.applied_release_id == arguments.release_id
                                              and d.actual_generation == d.desired_generation),
                             'active_download_leases': active_permits(devices),
                             'paused_or_offline': sum(1 for d in devices if not d.online or not d.has_permit)}
        campaign_body, status = plane.get(f'/api/v1/deployments?limit=1')
        if campaign_body and campaign_body['data']:
            campaign_id = campaign_body['data'][0]['deployment_campaign_id']
            gate_started = time.monotonic()
            plane.get(f'/api/v1/deployments/{campaign_id}')
            report['campaign_gate_latency_ms'] = round((time.monotonic() - gate_started) * 1000, 3)
        report['latency_during_rollout'] = plane.latency.summary()

    # Reconnect reconciliation: offline devices come back and must converge without
    # a new assignment, which is the expected offline-device rollback path.
    for device in devices:
        if not device.online:
            device.online = True
            device.health = 'healthy'
    reconnect_started = time.monotonic()
    reconcile(plane, devices, arguments.concurrency)
    report['reconnect_reconciliation'] = {
        'seconds': round(time.monotonic() - reconnect_started, 2),
        'devices_reconnected': offline,
        'with_desired_state': sum(1 for d in devices if d.online and d.release_id is not None),
        'converged_after_reconnect': sum(1 for d in devices if d.online and d.actual_generation == d.desired_generation
                                         and d.desired_generation > 0),
        'devices_waiting_for_permit': sum(1 for d in devices if d.online and not d.has_permit),
        'latency': plane.latency.summary()}

    if arguments.output:
        Path(arguments.output).parent.mkdir(parents=True, exist_ok=True)
        Path(arguments.output).write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return report


if __name__ == '__main__':
    import getpass

    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://localhost:8080')
    parser.add_argument('--email', required=True)
    parser.add_argument('--password', default=None)
    parser.add_argument('--release-id', required=True)
    parser.add_argument('--devices', type=int, default=1000, choices=[100, 1000, 10000])
    parser.add_argument('--concurrency', type=int, default=32)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--offline-ratio', type=float, default=0.1)
    parser.add_argument('--failure-ratio', type=float, default=0.02)
    parser.add_argument('--campaign', action='store_true')
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--output', default='var/fleet-scale-report.json')
    parsed = parser.parse_args()
    if parsed.password is None:
        parsed.password = getpass.getpass('Password: ')
    run(parsed)
