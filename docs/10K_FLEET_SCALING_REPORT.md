# 10K fleet scaling report

## Result

```text
10K LOGICAL FLEET CONTROL-PLANE SIMULATION: IMPLEMENTED — NOT RUNTIME VERIFIED
MEASURED LOCAL RESULTS: NONE
```

No number in this report is a measurement. The control plane (FastAPI + PostgreSQL)
cannot be started on this host — the Python environment has neither package and no
database is reachable — so the driver has never completed a run here. The harness, its
accounting and its exact reproduction command are provided instead of invented metrics.

This is deliberately **not** a physical fleet claim: the driver never creates 10 000
containers or processes, and no Jetson, GPU or camera is involved.

## 1. Why logical, not containerised

Per-device containers or processes would measure Docker and scheduler overhead, not the
control plane, and 10 000 of them do not fit any workstation. `simulation/fleet_scale.py`
therefore models each device as a small in-process record containing only what the
control plane can observe:

```text
device_id, desired_generation, actual_generation, release_id, applied_release_id,
config_version_id, applied_config_version_id, boot_id, sequence, health, agent_state,
download_state, deployment_state, has_permit, online, enrolled, credential,
heartbeats_accepted, converged_at, errors
```

Concurrency is bounded by a thread pool (`--concurrency`, default 32). There is no
thread, coroutine or object *per device*.

## 2. What it exercises

| Scenario | How the driver does it |
| --- | --- |
| 10 000 registered devices | `POST /api/v1/devices` with `mode=simulated`, then enrollment tokens and `POST /api/v1/device-enrollments` |
| Data plane heartbeats | one `POST /devices/{id}/heartbeats` per online device per round, with real metric summaries |
| Progressive rollout | `GET /devices/{id}/desired-state` per device, honoring `download_permit`, then applying the generation it was granted |
| Bounded download concurrency | counts devices holding a permit; the control plane caps active leases at 5 per campaign (`backend/app/permits.py`) |
| Partial offline population | `--offline-ratio` (default 0.1) stops heartbeating before the run |
| Candidate failures | `--failure-ratio` (default 0.02) reports `health_status=unhealthy`, `agent_state=degraded` |
| Pause / rollback | driven through the campaign API, not emulated |
| Reconnect reconciliation | offline devices are brought back online and re-fetch desired state with no new assignment, which is the offline-rollback path |
| GPU metrics | never present in simulated summaries: `gpu_metrics_available: false`, no fabricated values |

## 3. What it measures

| Metric | Source |
| --- | --- |
| heartbeat throughput | accepted heartbeats ÷ elapsed seconds, plus latency percentiles |
| request/DB latency | nearest-rank p50/p95/p99/max over every HTTP request it makes, split per phase |
| campaign calculation latency | timed `GET /api/v1/deployments/{id}` gate assessment |
| rollout reconciliation time | wall time for `reconcile()` until the fleet converges on the current permit set |
| active download leases | devices whose desired state carried a permit |
| provisioning cost | device creation + enrollment wall time and latency |

## 4. Run it (once a real control plane exists)

```bash
# 1. bring up the stack and create an approved release (START_HERE.md)
# 2. register the logical fleet and exercise it
.venv/bin/python -m simulation.fleet_scale \
    --url http://localhost:8080 --email operator@example.org \
    --release-id RELEASE_UUID \
    --devices 10000 --concurrency 32 --rounds 3 \
    --offline-ratio 0.1 --failure-ratio 0.02 --campaign \
    --output var/fleet-scale-report.json
```

Scale in steps (`--devices 100`, `1000`, `10000`) and keep `--seed` fixed for
comparable runs. The report JSON is written to `var/` because it is runtime evidence for
the machine that produced it — it is not committed as a repository claim.

## 5. What a result would and would not mean

Even with a completed run:

* it is a **logical** control-plane workload; it says nothing about physical device
  behaviour, radios, power or camera streams;
* simulated worker observations are labelled `simulated_worker` and must never be read
  as model performance;
* `--failure-ratio` devices are unhealthy **by declaration**, not by measurement;
* GPU/TOPS/VRAM would still be absent, and absent must remain unknown rather than zero.

## 6. Status summary

| Component | Status |
| --- | --- |
| `simulation/fleet_scale.py` driver | **IMPLEMENTED — NOT VERIFIED** (syntax-checked; never executed against a server) |
| Ledger/pagination/seeding for 10 K records | unchanged from the existing seeder; **IMPLEMENTED — NOT VERIFIED** |
| Bounded download leases | allocator component **VERIFIED**, integrated path **IMPLEMENTED — NOT VERIFIED** |
| Control-plane performance numbers | **NOT PRODUCED** |
| Physical 10 K Jetson deployment | **NOT CLAIMED** |
