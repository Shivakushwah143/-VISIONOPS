"""10K logical-fleet control-plane load driver and sustained simulator.

This drives the real control plane (PostgreSQL APIs) with light-weight logical
device records: no container, thread or process per device. Each logical device
carries only the state the control plane can observe - desired/actual generation,
release version, heartbeat, health, download state, deployment state and a metric
summary - so 10,000 of them fit in one process.

Two modes share the same device model and the same real HTTP contracts:

* ``run()`` (default): a bounded measurement pass. It measures heartbeat
  throughput, request/DB latency percentiles, campaign gate latency, rollout
  reconciliation time and active download leases against the real server.
* ``serve()``: a long-lived heartbeat service. Every online device sends one real
  heartbeat per round so the fleet stays observable, while a hold file removes
  selected devices from heartbeating - the UNREACHABLE/OFFLINE fault path.

Nothing here is a physical-device claim, and it must not be presented as a 10,000
Jetson deployment. See ``docs/10K_FLEET_SCALING_REPORT.md`` and
``docs/evidence/10k-fleet-demo/10K_FLEET_EVIDENCE.md``.
"""
import argparse
import json
import math
import os
import random
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

DEFAULT_METRIC_VALUES = {'inference_latency_ms': [12.0, 18.0, 25.0, 40.0, 60.0], 'inference_fps': 5.0,
                         'input_fps': 25.0, 'queue_depth': 0, 'dropped_frames': 0, 'reconnect_count': 0,
                         'processed_fps': 4.8, 'rtsp_connected': True, 'decode_fps': 24.9,
                         'gpu_metrics_available': False}


@dataclass
class LogicalDevice:
    """One logical device: only the fields the control plane can observe."""

    index: int
    name: str
    device_id: str = ''
    site_id: str = ''
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
        # The API enforces one configured Origin. Supply it explicitly when this runs from
        # inside the Compose network, where the request host is a service DNS name.
        self.http = httpx.Client(base_url=url.rstrip('/'), timeout=60,
                                 headers={'Origin': origin or os.environ.get('VISIONOPS_CLIENT_ORIGIN') or url})
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


def _utc(offset=0):
    return (datetime.now(timezone.utc) + timedelta(seconds=offset)).isoformat()


def ensure_sites(plane, count):
    """Reuse or create the deterministic sites the logical fleet is distributed over."""
    sites = plane.get('/api/v1/sites?limit=200')[0]['data']
    by_name = {site['name']: site for site in sites}
    site_ids = []
    for index in range(count):
        name = 'Fleet Site %02d' % (index + 1)
        site = by_name.get(name)
        if not site:
            body, _ = plane.post('/api/v1/sites', {'name': name, 'timezone': 'UTC'})
            site = body['data'] if body else None
        if not site:
            raise RuntimeError('site_create_failed:' + name)
        by_name[name] = site
        site_ids.append(site['site_id'])
    return site_ids


def existing_devices(plane):
    """Map (site_id, name) -> device_id so re-seeding is idempotent by logical name."""
    rows = {}
    cursor = None
    while True:
        params = {'limit': 200}
        if cursor:
            params['cursor'] = cursor
        body, _ = plane.get('/api/v1/devices', params=params)
        if not body:
            break
        for device in body['data']:
            rows[(device['site_id'], device['name'])] = device['device_id']
        cursor = body['page']['next_cursor']
        if not cursor:
            break
    return rows


def provision(plane, devices, release_id, site_count=1, concurrency=8, timeout=60):
    """Create device records, enroll them and let each learn its desired state.

    Bounded concurrency: one worker per in-flight device, never one per device.
    """
    site_ids = ensure_sites(plane, site_count)
    known = existing_devices(plane)
    created = [0]
    lock = threading.Lock()

    def setup(device):
        device.site_id = site_ids[min(len(site_ids) - 1, device.index * len(site_ids) // len(devices))]
        device_id = known.get((device.site_id, device.name))
        if not device_id:
            payload = {'site_id': device.site_id, 'name': device.name, 'mode': 'simulated',
                       'hardware_profile': 'cpu_onnx_x86_64', 'release_id': release_id}
            body, _ = plane.post('/api/v1/devices', payload)
            if body:
                device_id = body['data']['device_id']
                with lock:
                    created[0] += 1
        if not device_id:
            device.errors += 1
            return
        device.device_id = device_id
        token_body, _ = plane.post('/api/v1/devices/%s/enrollment-tokens' % device_id,
                                   {'reason': '10K logical fleet simulation'})
        if not token_body:
            device.errors += 1
            return
        enrollment, _ = plane.post('/api/v1/device-enrollments',
                                   {'token': token_body['data']['token'],
                                    'capabilities': {'agent_version': '0.1.0', 'mode': 'simulated'}})
        if not enrollment:
            device.errors += 1
            return
        device.credential = enrollment['data']['device_credential']
        device.enrolled = True
        # A real agent fetches desired state and reports what it applied. Applying the
        # assigned generation immediately is the pre-campaign baseline, not a rollout.
        desired, _ = plane.request('GET', '/api/v1/devices/%s/desired-state' % device_id,
                                   headers={'Authorization': 'Bearer ' + device.credential})
        if desired:
            state = desired['data']
            device.desired_generation = state['generation']
            device.release_id = state['release_id']
            device.config_version_id = state['config_version_id']
            device.actual_generation = state['generation']
            device.applied_release_id = state['release_id']
            device.applied_config_version_id = state['config_version_id']
            device.download_state = 'applied'
            device.deployment_state = 'converged'

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        list(pool.map(setup, devices))
    return created[0]


def heartbeat_round(plane, devices, concurrency=32, include_metrics=False):
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
                                        if include_metrics and device.applied_release_id else []),
                   'rejected_generation': None, 'last_error_code': None}
        body, _ = plane.request('POST', '/api/v1/devices/%s/heartbeats' % device.device_id, json=payload,
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
        body, _ = plane.request('GET', '/api/v1/devices/%s/desired-state' % device.device_id,
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


def read_hold(path):
    """Devices whose heartbeat is deliberately withheld (the simulated fault set)."""
    file = Path(path)
    if not file.is_file():
        return set()
    try:
        data = json.loads(file.read_text())
    except Exception:
        return set()
    values = data.get('down', []) if isinstance(data, dict) else data
    return {str(value) for value in values}


def active_permits(devices):
    return sum(1 for device in devices if device.has_permit)


def build_devices(count, prefix='device-', first_index=0):
    """Deterministic logical devices: index N is always named ``device-N+1``.

    ``first_index`` only shifts which slice of that one global numbering this
    process owns, so a large fleet can be split across several driver processes
    (``--first-index``) without renaming or duplicating a single logical device.
    The default keeps the original behaviour exactly.
    """
    return [LogicalDevice(index=first_index + offset, name='%s%05d' % (prefix, first_index + offset + 1))
            for offset in range(count)]


def run(arguments):
    plane = ControlPlane(arguments.url, arguments.email, arguments.password)
    devices = build_devices(arguments.devices, arguments.name_prefix, arguments.first_index)
    random.seed(arguments.seed)
    offline = int(arguments.devices * arguments.offline_ratio)
    for device in random.sample(devices, offline):
        device.online = False
    online = [d for d in devices if d.online]
    failing = random.sample(online, int(len(online) * arguments.failure_ratio))
    for device in failing:
        device.health = 'unhealthy'
        device.agent_state = 'degraded'

    report = {'requested_devices': arguments.devices, 'release_id': arguments.release_id,
              'concurrency': arguments.concurrency, 'offline_devices': offline,
              'failing_devices': len(failing), 'measured_at': _utc(),
              'note': ('Logical control-plane simulation. Logical devices, not physical Jetsons. '
                       'No GPU or model-quality metric is implied.')}

    provision_started = time.monotonic()
    report['records_created'] = provision(plane, devices, arguments.release_id, arguments.sites, arguments.concurrency)
    report['provision_seconds'] = round(time.monotonic() - provision_started, 2)
    report['latency_during_provisioning'] = plane.latency.summary()

    baseline = heartbeat_round(plane, devices, arguments.concurrency, include_metrics=True)
    started = time.monotonic()
    for _ in range(arguments.rounds):
        heartbeat_round(plane, devices, arguments.concurrency)
    elapsed = max(time.monotonic() - started, 1e-6)
    active = len([d for d in devices if d.online and d.enrolled])
    report['heartbeats'] = {'rounds': arguments.rounds, 'first_round_accepted': baseline,
                            'accepted_per_round': arguments.rounds * active,
                            'throughput_per_second': round(arguments.rounds * active / elapsed, 2),
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
        campaign_body, status = plane.get('/api/v1/deployments?limit=1')
        if campaign_body and campaign_body['data']:
            campaign_id = campaign_body['data'][0]['deployment_campaign_id']
            gate_started = time.monotonic()
            plane.get('/api/v1/deployments/%s' % campaign_id)
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


def serve(arguments):
    """Sustained heartbeat service; devices in the hold file stop heartbeating."""
    plane = ControlPlane(arguments.url, arguments.email, arguments.password)
    devices = build_devices(arguments.devices, arguments.name_prefix, arguments.first_index)
    provision_started = time.monotonic()
    created = provision(plane, devices, arguments.release_id, arguments.sites, arguments.concurrency)
    enrolled = [d for d in devices if d.enrolled]
    print(json.dumps({'event': 'fleet_provisioned', 'requested': arguments.devices, 'created': created,
                      'enrolled': len(enrolled), 'errors': sum(1 for d in devices if d.errors),
                      'sites': arguments.sites, 'seconds': round(time.monotonic() - provision_started, 2),
                      'note': 'Logical devices simulated through the VisionOps control plane.'}), flush=True)
    if len(enrolled) != arguments.devices:
        raise SystemExit('provisioning incomplete: %d of %d enrolled' % (len(enrolled), arguments.devices))
    rounds = 0
    while True:
        started = time.monotonic()
        held = read_hold(arguments.hold_file)
        active = [d for d in enrolled if d.name not in held]
        accepted = heartbeat_round(plane, active, arguments.concurrency, include_metrics=(rounds < arguments.metric_rounds))
        elapsed = round(time.monotonic() - started, 2)
        rounds += 1
        print(json.dumps({'event': 'fleet_heartbeat_round', 'round': rounds, 'held': sorted(held),
                          'heartbeating': len(active), 'accepted': accepted, 'seconds': elapsed,
                          'latency_p95_ms': plane.latency.summary().get('p95_ms')}), flush=True)
        time.sleep(max(0.0, arguments.interval - elapsed))


if __name__ == '__main__':
    import getpass

    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://localhost:8080')
    parser.add_argument('--email', required=True)
    parser.add_argument('--password', default=None)
    parser.add_argument('--release-id', required=True)
    parser.add_argument('--devices', type=int, default=1000)
    parser.add_argument('--first-index', type=int, default=0,
                        help='first logical device index this process owns; lets one fleet '
                             'be split across several driver processes (device-N+1 naming)')
    parser.add_argument('--sites', type=int, default=1)
    parser.add_argument('--name-prefix', default='device-')
    parser.add_argument('--concurrency', type=int, default=32)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--offline-ratio', type=float, default=0.1)
    parser.add_argument('--failure-ratio', type=float, default=0.02)
    parser.add_argument('--campaign', action='store_true')
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--serve', action='store_true')
    parser.add_argument('--interval', type=float, default=30)
    parser.add_argument('--metric-rounds', type=int, default=2)
    parser.add_argument('--hold-file', default='var/fleet-hold.json')
    parser.add_argument('--output', default='var/fleet-scale-report.json')
    parsed = parser.parse_args()
    if parsed.password is None:
        parsed.password = getpass.getpass('Password: ')
    (serve if parsed.serve else run)(parsed)
