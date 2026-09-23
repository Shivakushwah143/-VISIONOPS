# VisionOps — interview demo flow

The shortest high-impact demo of the platform as it actually exists. Sixteen steps,
15–25 minutes, **no full Docker rebuild required**.

Read [VISIONOPS_END_TO_END_ARCHITECTURE.md](VISIONOPS_END_TO_END_ARCHITECTURE.md) for the
architecture behind these steps and
[VISIONOPS_ARCHITECTURE_AUDIT.md](VISIONOPS_ARCHITECTURE_AUDIT.md) for the known defects
(one of which — the missing `.dockerignore` — is why you should *not* rebuild casually).

---

## 0. Preconditions on this machine (verified)

| Fact | Value | How it was verified |
| --- | --- | --- |
| OS / arch | Windows 11, x86_64 | `uname -a` |
| Docker | 29.2.1, Compose v5.0.2, daemon running (`linux/amd64`) | `docker info` |
| Images present | `visionops-backend`, `-controller`, `-migrate`, `-mlflow`, `-frontend` | `docker images` |
| **Images match HEAD** | sha256 of `backend/app/main.py`, `backend/app/campaigns.py`, `edge/pipeline.py` **byte-identical** inside the image and in the working tree | `docker run --rm --entrypoint sh visionops-backend:latest -c sha256sum …` |
| `.env` | present and populated (no placeholder values) | `grep -c replace-with .env` → 0 |
| Host venv | Python 3.12.13 with `onnxruntime 1.30.0`, `opencv 5.0.0`, `supervision 0.30.2`, `torch 2.14.0+cpu`, `ultralytics`, `onnx`, `httpx`, `cryptography` | `.venv/Scripts/python.exe -c "import …"` |
| Host venv **lacks** | `fastapi`, `sqlalchemy`, `mlflow`, `alembic`, `prometheus_client` | same |
| NVIDIA | **none** — no `nvidia-smi`, no `/dev/nvidia*`, no TensorRT | `nvidia-smi` → not found |

**Two consequences, both load-bearing for the demo:**

1. The **control plane runs only in Docker** here. The native venv cannot import
   `backend.app.main`. Do not promise a native uvicorn demo on this machine.
2. The **edge and offline verification scripts run natively** and are the honest local
   deep-dive (steps 11 and 16).

Conventions used below:

```bash
DS="docker compose --env-file .env -f infrastructure/compose.yaml"   # stack
PY=.venv/Scripts/python.exe                                          # host interpreter (Windows)
SHOTS=docs/evidence/demo/screenshots                                 # where to capture images
```

---

## Step 1 — Show the architecture

**WHAT TO SHOW** `docs/VISIONOPS_END_TO_END_ARCHITECTURE.md` — the §18 summary and the
§1 mermaid diagram. Two facts to say out loud: the edge is **outbound-only** (deployment is
desired state at a monotonically increasing generation, so an offline device needs no
special path), and there is **one canonical model contract** shared by the CPU adapter, the
offline export gate and the generated DeepStream parser.

**COMMAND**

```bash
sed -n '1,60p' docs/VISIONOPS_END_TO_END_ARCHITECTURE.md
```

**EXPECTED OUTPUT** The status vocabulary table (`VERIFIED` / `IMPLEMENTED_NOT_RUNTIME_VERIFIED`
/ `SIMULATED` / `BLOCKED`) and the executive flowchart.

**WHAT IT PROVES** The design is documented against real modules, and the status words are
defined as evidence-bound — not as a claim that the code works.

**SCREENSHOT** `01-architecture.png` — the rendered mermaid diagram.

---

## Step 2 — Show Docker services

**WHAT TO SHOW** The eight services coming up and reaching healthy, **reusing existing
images**.

**COMMAND**

```bash
$DS up -d                 # no --build
$DS ps
```

**EXPECTED OUTPUT** `postgres` healthy, `migrate` exited 0 (Alembic at head), `backend`
healthy, `controller`, `frontend`, `mlflow`, `prometheus`, `grafana` running. Ports:
console `127.0.0.1:8080`, MLflow `127.0.0.1:5000`, Grafana `127.0.0.1:3000`.
`postgres` and `prometheus` are intentionally **not** published to the host.

**WHAT IT PROVES** The control plane is real and starts from the repository's own Compose
file, with migrations as a gate (`service_completed_successfully`) rather than an
afterthought.

**SCREENSHOT** `02-docker-services.png` — `$DS ps` output showing health states.

> If you *do* rebuild, read the audit first: with no `.dockerignore` the build context
> currently includes `.venv` and `var/` (831 MB), which is exactly the "224 MB and growing"
> symptom.

**Troubleshooting**

```bash
$DS logs --tail=50 backend          # start-up errors
curl -s localhost:8080/health/live  # {"status":"alive"}
curl -s localhost:8080/health/ready # {"status":"ready"} or 503 database_unavailable
```

`/health/live` returning 200 while `/health/ready` returns 503 is the designed behaviour:
liveness is never readiness.

---

## Step 3 — Show the real model identity

**WHAT TO SHOW** The model is real, its hash is pinned, and the pipeline refuses anything
else. The `Detector` constructor raises on a hash mismatch **before** creating an inference
session.

**COMMAND**

```bash
sha256sum var/model/hansung-p3.onnx
$PY -c "import json;d=json.load(open('var/model/hansung-p3.json'));print(d['artifact']['sha256']);print(d['source_index_to_canonical_id']);print(d['output'])"
```

**EXPECTED OUTPUT** `b239aa7e881fc590633944cf37df46ed477f3a819d09ec7a7bdb19d052ca2422`;
mapping `{5: 0, 0: 1, 2: 2}`; output `[1, 14, 8400]` = 4 box values + 10 class scores.

**WHAT IT PROVES** The artifact is the qualified Hansung PPE detector, its contract is
recorded, and the canonical interpretation is `Person/Hardhat/NO-Hardhat (5/0/2) →
person/helmet/no_helmet`. No synthetic fixture is standing in for the model.

**SCREENSHOT** `03-model-identity.png` — hash + contract side by side.

---

## Step 4 — Run video inference

**WHAT TO SHOW** The same artifact running on the existing clip.

**COMMAND**

```bash
$PY -m scripts.verify_edge_platform            # writes docs/evidence/edge-platform-runtime.json
```

**EXPECTED OUTPUT** Runtime section: `ONNX_CPU` available, `preferred: ONNX_CPU`,
`ONNX_CUDA`/`TENSORRT` unavailable with reason codes (`gpu_not_detected`,
`tensorrt_module_not_installed`) and **refusing to load**. `pipeline_end_to_end`:
~38 inference frames, p50 ≈ 97 ms / p95 ≈ 168 ms, 168 dropped by design, 4 durable
`restricted_zone_dwell` events.

**WHAT IT PROVES** Real ONNX inference executes, and an unavailable accelerator is reported
as unavailable instead of silently falling back to CPU under a CUDA label.

**SCREENSHOT** `04-inference-runtime.png` — the `inference_runtimes` + `pipeline_end_to_end`
JSON sections.

---

## Step 5 — Show detections and tracking

**WHAT TO SHOW** Per-class detections, byte tracks and the annotated evidence video already
produced from the real clip.

**COMMAND**

```bash
$PY -c "import json;d=json.load(open('var/evidence/hansung-ppe-evidence.json'));print({k:(v['count'],v['max_conf']) for k,v in d['canonical_detections'].items()});print('tracks',d['unique_tracks'],{k:(v['first_frame'],v['last_frame'],v['frames']) for k,v in d['tracks'].items()})"
ls -la var/evidence/
```

**EXPECTED OUTPUT** person 414 (max 0.921), helmet 412 (max 0.916), **no_helmet 1**
(max 0.419); 3 unique tracks (ids 1 and 2 span frames 0–205, id 3 is a single-frame
`unknown`); `hansung-ppe-tracked.mp4` plus five annotated PNG frames.

**WHAT IT PROVES** Detection and tracking are real and inspectable frame by frame, with the
model's own confidence values — not a diagram.

**SCREENSHOT** `05-tracking.png` — an annotated frame (e.g. `hansung-ppe-frame-100.png`).

---

## Step 6 — Show a generated safety event

**WHAT TO SHOW** A deterministic rule firing and becoming a durable event.

**COMMAND**

```bash
$PY -c "import json;d=json.load(open('docs/evidence/edge-platform-runtime.json'));print(json.dumps(d['temporal_analyzers'],indent=2));print([e for e in d['pipeline_end_to_end']['durable_events']])"
```

**EXPECTED OUTPUT** `ppe_sustained` VERIFIED (5 frames spanning 2 s, cooldown enforced,
helmet produces nothing), `restricted_zone_dwell` VERIFIED (leave-and-re-enter restarts the
timer), `low_motion_heuristic` fires for a static track and stays silent for a moving one,
`unknown_temporal_analyzer` rejected. Durable events: `restricted_zone_dwell` with
`supporting_frames` 6/6/24/24.

**WHAT IT PROVES** Behaviour rules are deterministic, replayable geometry+time — not a
learned classifier — and they produce real events.

**SCREENSHOT** `06-temporal-rules.png` — the `temporal_analyzers` block.

> **Say this out loud:** on this clip the PPE rule correctly produces **zero** violations.
> There is one bare-head observation (conf 0.419) and the rule needs ≥5 frames spanning
> ≥2 s. The thresholds were not lowered to manufacture an alert.

---

## Step 7 — Show backend persistence and the real UI

**WHAT TO SHOW** A login, then Overview / Events / Fleet with real persisted data.

**COMMAND**

```bash
# create an operator once (inside the container, since the host venv has no fastapi).
# Needs an interactive terminal: bootstrap_users prompts with getpass for ≥12 characters.
$DS exec -it backend python -m scripts.bootstrap_users --email operator@example.org --role mlops_engineer

# then open the console (Windows: start http://localhost:8080)
start http://localhost:8080
```

**EXPECTED OUTPUT** The console signs in; Overview shows inventory cards
(inventory / online / never seen / state mismatch) with the explicit caption
"*n* real device records · *m* simulated records. Inventory records do not represent active
physical installations." Fleet lists devices with `desired_generation` vs
`applied_generation`. The footer shows `Realtime <event> #<sequence>` once the socket
connects.

**WHAT IT PROVES** The frontend is a real authenticated client of the real API, and the
console is careful about what inventory means.

**SCREENSHOT** `07-console-login.png`, `08-console-overview.png`, `09-console-fleet.png`.

**API-only alternative** (if you prefer no browser)

```bash
$DS exec backend python -c "
import json,urllib.request
print(urllib.request.urlopen('http://localhost:8000/health/ready').read().decode())"
```

---

## Step 8 — Show Prometheus and Grafana

**WHAT TO SHOW** The scrape target is up and the dashboard renders runtime values.
Prometheus is not published to the host, so query it from inside the container.

**COMMAND**

```bash
$DS exec prometheus wget -qO- 'http://localhost:9090/api/v1/targets?state=active' | head -c 600
$DS exec prometheus wget -qO- 'http://localhost:9090/api/v1/query?query=up{job="visionops"}' | head -c 400
$DS exec prometheus wget -qO- 'http://localhost:9090/api/v1/query?query=visionops_database_observations_available' | head -c 400
open http://localhost:3000        # Grafana; password is GRAFANA_ADMIN_PASSWORD in .env
```

**EXPECTED OUTPUT** Target `visionops` with `health: up`; `up{job="visionops"} = 1`;
`visionops_database_observations_available = 1`; the "VisionOps application metrics"
dashboard (uid `visionops`) with 16 panels.

**WHAT IT PROVES** Prometheus really scrapes this application and Grafana renders
application-generated values — including the custom DB-backed collector, not just HTTP
counters.

**SCREENSHOT** `10-prometheus-target.png`, `11-grafana-dashboard.png`.

> **Be precise about what the panels mean.** The device under test is `simulated`, so
> `visionops_camera_inference_fps` / `..._latency_ms` carry the *simulator's* observation
> window, not the measured 118 ms CPU inference. `visionops_camera_outbox_depth` is only
> emitted by real-mode workers and is therefore **absent** here. Absent means unknown.

---

## Step 9 — Show MLflow lineage

**WHAT TO SHOW** The tracking server holding the run that registered the artifact.

**COMMAND**

```bash
open http://localhost:5000
$PY -c "import json;d=json.load(open('var/hansung-release.json'));print(d['mlflow_run_id'], d['model_sha256'])"
```

**EXPECTED OUTPUT** MLflow UI with experiment `visionops-hansung`, a run carrying
`model/hansung-p3.onnx`, `contract/hansung-p3.json`,
`evidence/hansung-onnx-qualification.json` and `provenance.json`; registered model
`ppe-hansung`. The backend refuses `POST /training-runs` if MLflow does not agree on the
run status (`lineage_mismatch` 422 / `registry_unavailable` 503).

**WHAT IT PROVES** Lineage is enforced live by the server, not merely recorded — and the
absence of `mlflow` in the host venv is why `scripts/mlflow_rest.py` exists.

**SCREENSHOT** `12-mlflow-run.png` — the run's artifact tree.

---

## Step 10 — Show the release lifecycle

**WHAT TO SHOW** An approved, signed release and its manifest identity.

**COMMAND**

```bash
$PY -c "
import json;d=json.load(open('var/hansung-release.json'))
print('release', d['release_id'], d['version_label'], d['status'], d['evidence_mode'])
print('manifest sha256', d['manifest_sha256'])
for k,v in d['manifest'].items(): print('  ',k,'=',v)"
```

**EXPECTED OUTPUT** `release 3741c6b3-b9e6-4ad2-910c-c86ec601-a8dc1 ppe-hansung-v1 approved
simulated`, the `manifest_sha256`, and the **16 manifest keys**: `schema_version`,
`release_id`, `hardware_profile`, `evidence_mode`, `model_artifact_id`, `model_sha256`
(`b239aa7e…`), `model_size_bytes` (12 245 976), `runtime_artifact_id`, `runtime_sha256`,
`runtime_size_bytes`, `config_version_id`, `config_sha256`, `evaluation_report_id`,
`compatibility`, `entrypoint`.

**WHAT IT PROVES** A release pins model + runtime + config + evaluation identity by hash,
so any change to any of them invalidates the signature.

> **State the skew, do not paper over it.** `lifecycle.py::release_identity()` adds
> `class_mapping_version`, `architecture`, `runtime`, `input_shape`,
> `model_head_channels`, dataset identity and the source MLflow run to every **new**
> manifest — and the worker asserts the released mapping version before loading a
> candidate — but this retained release predates that code, so those keys are **not** in
> `var/hansung-release.json` (audit finding P1-7). Show the fields by creating a fresh
> release, not by claiming they are here.

**SCREENSHOT** `13-release-detail.png`.

**Tamper check to show live** — recompute the manifest hash from the bytes and compare it
with the recorded `manifest_sha256`. The Ed25519 signature itself lives in PostgreSQL, not
in this file, and is checked in step 15.

```bash
$PY -c "
import json,hashlib
release=json.load(open('var/hansung-release.json'))
manifest=release['manifest']
canonical=json.dumps(manifest,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
print('recorded  ', release['manifest_sha256'])
print('recomputed', hashlib.sha256(canonical).hexdigest())
print('match     ', hashlib.sha256(canonical).hexdigest()==release['manifest_sha256'])
# any change to these invalidates the signature
print('model_sha256 in manifest', manifest['model_sha256'])
print('mapping version         ', manifest['class_mapping_version'])"
```

**Say:** the signing private key `var/keys/qual.key` never leaves the machine, is gitignored
(`*.key`), and is excluded from every archive audit; only `var/trusted_keys/qual.pub` is
installed as the trust anchor.

---

## Step 11 — Show the durable outbox (native, no Docker)

**WHAT TO SHOW** An event surviving a process restart, and a duplicate acknowledgement
removing exactly the right row.

**COMMAND**

```bash
$PY -m scripts.verify_components --output var/engineering/component-runtime.json
```

**EXPECTED OUTPUT** `synthetic_video_onnx_bytetrack_rule_outbox: VERIFIED` (one durable
event), `outbox_restart_and_duplicate_ack: VERIFIED`, `model_tampering: VERIFIED
outcome rejected`, `unknown_and_conflicting_head_suppression: VERIFIED`.
Exit code 0 — no `WinError 32`.

**WHAT IT PROVES** The edge outbox is durable across a reopen, duplicate acks are handled,
a tampered model digest is refused, and conflicting head evidence is suppressed.

**SCREENSHOT** `14-outbox-durability.png`.

> This run uses an explicitly synthetic constant-output fixture. Label it as such.

---

## Step 12 — Inject a failure and show it observed

**WHAT TO SHOW** The real, supported fault mechanism — not hand-edited database rows.

**COMMAND**

```bash
# fault a simulated identity's state directory
$PY -m scripts.simulate_fault --state var/simulation/hansung-agent --fault offline
ls var/simulation/hansung-agent/fault-offline

# the control plane observes it on the next ticks
$DS exec backend python -c "
import json,urllib.request
print(urllib.request.urlopen('http://localhost:8000/api/v1/fleet/summary').read().decode())" 2>/dev/null || echo "use the console Fleet view"
```

**EXPECTED OUTPUT** The marker file appears; the agent stops heartbeating
(`agent.run()` skips delivery while `fault-offline` exists); after ~30 s the device's
heartbeat age exceeds the freshness window, `campaigns.signals()` emits
`HEARTBEAT_STALE`, and `pipeline_metrics` stops reporting a stale `healthy` — after 60 s it
forces `visionops_device_health … "unknown"`.

**WHAT IT PROVES** Unknown telemetry degrades to *unknown*, never to *healthy*, and the
failure is surfaced with a machine-readable reason code.

**SCREENSHOT** `15-failure-injected.png`, `16-health-degraded.png`.

**Recover afterwards**

```bash
$PY -m scripts.simulate_fault --state var/simulation/hansung-agent --fault offline --recover
```

---

## Step 13 — Show the canary rollout and pause

**WHAT TO SHOW** A real campaign created and started against PostgreSQL, with exactly one
ring-0 canary, and the gate reason codes.

**COMMAND**

```bash
$PY -c "
import json;d=json.load(open('var/hansung-canary-campaign.json'))
print('campaign', d['campaign_id']); print('canary', d['canary_device_id'], 'assigned generation', d['canary_assigned_generation'])
print('eligible devices', sum(1 for e in d['eligibility'] if e['eligible']), 'of', len(d['eligibility']))"
```

Then open the console → **Deployments** → open a campaign: the detail panel shows
`Gate: <status>` with the reason-code list, each code rendered with its human description
from `REASON_CODES`, plus a per-target signal report.

**EXPECTED OUTPUT** `campaign <uuid>`, `canary 1c88609a-… assigned generation 4`,
`eligible devices 10 of 10`. In the console: gate status, reason codes,
`N target signal report(s)`, and the state-appropriate command buttons
(`start`/`cancel` for draft, `advance`/`pause`/`rollback` for running, `resume`/`rollback`
for paused, `rollback` for completed), each prompting for a mandatory reason.

**WHAT IT PROVES** Rollout is a persisted, gated state machine driven by real API calls —
the UI cannot even offer an invalid transition.

**SCREENSHOT** `17-canary-created.png`, `18-canary-gate-reasons.png`.

---

## Step 14 — Show the rollback and recovery

**WHAT TO SHOW** The retained evidence of the highest-value journey:
`paused → rolled_back` with 10/10 devices back on `v1`, healthy and converged.

**COMMAND**

```bash
$PY -c "
import json;d=json.load(open('var/hansung-canary-cleanup.json'))
print(d['campaign_id'], d['status_before'], '->', d['status_after'], 'rolled_back=',d['rolled_back'])
print('distribution', d['distribution'])
print('all converged', all(r['converged'] for r in d['fleet']), '| all healthy', all(r['health_status']=='healthy' for r in d['fleet']))"
```

**EXPECTED OUTPUT** `3d28133e-… paused -> rolled_back rolled_back= True`;
`distribution {'v1': 10}`; `all converged True | all healthy True`.

**WHAT IT PROVES** Central rollback assigns the **previous** release and config at a *newer*
generation through the append-only ledger; the controller finalises only when every device
reports the previous release + config + `healthy` + a heartbeat fresher than 30 s; and
recovery converges without any new assignment.

**SCREENSHOT** `19-rollback-complete.png`, `20-recovered-fleet.png`.

**Honesty note to state:** the fleet is 10 **simulated** devices (3 real agent processes,
real SQLite, real HTTP). Failure injection used the supported `scripts.simulate_fault`
marker. The *mechanism* is production code; the *devices* are not physical. The retained
`phaseA`/`phaseB` evidence files that would show the pause cause-code chain are referenced
by the script but absent from the tree (audit finding P1).

---

## Step 15 — Show the security checks

**WHAT TO SHOW** Valid signature accepted; tampered manifest, tar traversal and symlink all
rejected. Run it **inside the container** (the host venv has no `fastapi`, which
`backend/app/security.py` imports).

**COMMAND**

```bash
$DS exec backend python -m scripts.verify_security
cat docs/evidence/security-runtime.json
```

**EXPECTED OUTPUT**

```json
{
  "valid_ed25519_signature": "VERIFIED",
  "tampered_manifest_rejected": "VERIFIED",
  "archive_path_traversal_rejected": "VERIFIED",
  "archive_symlink_rejected": "VERIFIED"
}
```

**WHAT IT PROVES** Release trust is cryptographic and fail-closed, and archives are validated
before extraction. Also show, in the same breath: events are rejected without an assignment
for that release+config and a matching `model_version_id` (provenance enforcement in
`main.py::ingest`), and `db.public()` strips password/token/CSRF/credential hashes and
storage keys from every response.

**SCREENSHOT** `21-security-runtime.png`.

---

## Step 16 — Explain the Jetson boundary

**WHAT TO SHOW** The hardware matrix and the physical-hardware gate — the honest boundary.

**COMMAND**

```bash
$PY -m scripts.detect_environment          # writes docs/evidence/environment.json
$PY -c "
import json;d=json.load(open('docs/evidence/environment.json'))
print({k:v.get('status') for k,v in d['commands'].items()})
print(d['nvidia_runtime'])"
$PY -m scripts.verify_physical_jetson --bundle-root . ; echo "exit=$?"
```

**EXPECTED OUTPUT** `nvidia-smi`, `nvcc`, `gst-launch-1.0`, `ffmpeg` all `BLOCKED`
(`executable_not_available`); `nvidia_runtime.status = BLOCKED`; the physical-Jetson
orchestrator exits **3** `BLOCKED_NOT_PHYSICAL_JETSON` and writes `blocked.json` while
claiming nothing.

Then show the boundary table:

```text
CPU ONNX inference (this host)          VERIFIED      118 ms p50 / 320 ms p95, 5.5 FPS
TensorRT on a real NVIDIA GPU           VERIFIED      Tesla T4, TensorRT 11.3.0.99 (EXTERNAL host)
TensorRT on this machine                BLOCKED       no NVIDIA device/driver/CUDA/TensorRT
Physical Jetson / JetPack / ARM64       BLOCKED       physical hardware required
Jetson TensorRT / DeepStream / NVDEC    BLOCKED       physical hardware required
Jetson power / thermal                  BLOCKED       physical hardware required
GPU utilization / VRAM / power / TOPS   BLOCKED       never measured anywhere -> never reported
Model quality (mAP, no_helmet P/R)      BLOCKED       labeled evaluation data required
```

**WHAT IT PROVES** The project states its verified boundary precisely. The Tesla T4 result
is real and separately measured (`docs/evidence/tensorrt/final/`), and it is **never**
translated into a Jetson claim: a T4 is not a Jetson. The Jetson qualification path is
*prepared* — one bundle, one command, exit 3 on any non-Jetson host — but preparation moves
no verification line.

**SCREENSHOT** `22-hardware-boundary.png` — the matrix above.

---

## Closing summary (say this, do not embellish)

```text
VERIFIED on this machine
  Control plane over PostgreSQL · backend API · frontend build + HTTP
  MLflow scrape-side lineage · Prometheus + Grafana
  Canonical model contract · real ONNX CPU inference · video decode
  ByteTrack tracking · deterministic temporal rules · durable SQLite outbox
  Event transport -> PostgreSQL -> API · release signing + tamper rejection
  Canary rollout · failure pause · central rollback · recovery

VERIFIED on an external GPU host (not this machine)
  TensorRT FP32 and true mixed-FP16 on a Tesla T4

SIMULATED (mechanism real, data synthetic)
  Fleet inventory · canary device fleet · engineering fixtures · failure injection

BLOCKED
  Physical Jetson · JetPack · Jetson TensorRT · Jetson DeepStream · NVDEC
  Jetson power/thermal · ARM64 execution · GPU utilization/VRAM/power/TOPS
  Model quality (no labeled PPE benchmark) · WebSocket delivery · GStreamer runtime
  Real-RTSP server · 10K fleet numbers · native control-plane execution on this host
```

Nothing above is rounded up, and no blocked item is described as working.
