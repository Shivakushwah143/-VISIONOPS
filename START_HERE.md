# Start here

This is an **incomplete implementation release**, not an end-to-end verified platform. Read [docs/CURRENT_VERIFIED_STATE.md](docs/CURRENT_VERIFIED_STATE.md) first — it lists, defect by defect, what was verified, what is implemented but unverified, what is blocked by hardware or data, and what is not implemented — then `docs/KNOWN_LIMITATIONS.md` before deployment. Actual runtime evidence covers the synthetic CPU plumbing, real PPE CPU inference through the canonical contract, tracking, deterministic temporal rules, the outbox, signature/archive rejection, backend liveness/metrics and frontend HTTP startup/build. **NVIDIA inference is now verified too** — TensorRT FP32 and a true mixed-FP16 engine on a real Tesla T4 — but on an external GPU host, not this machine: see [docs/evidence/tensorrt/final](docs/evidence/tensorrt/final/README.md) and [docs/interview/11_VERIFIED_VS_UNVERIFIED.md](docs/interview/11_VERIFIED_VS_UNVERIFIED.md). On this development host there is still no NVIDIA runtime at all. PostgreSQL-backed journeys, live RTSP, WebSocket delivery and fleet campaigns remain unverified here.

## Shortest launch route on a Docker-capable host

Prerequisites: Linux x86_64 or Windows with WSL2, Docker Engine/Desktop with Compose v2, Python 3.11, Node 22 for native frontend development, and network access to the declared package/container registries. Container image tags have not been pulled or digest-qualified in this environment. No model weights, signing keys, database or private footage are packaged.

From the extracted `visionops/` directory:

```bash
python3 scripts/setup.py
docker compose --env-file .env -f infrastructure/compose.yaml up --build -d
docker compose --env-file .env -f infrastructure/compose.yaml ps
docker compose --env-file .env -f infrastructure/compose.yaml exec backend python -m scripts.bootstrap_users --email operator@example.org --role mlops_engineer
```

Setup generates local secrets once and refuses to overwrite `.env`. Bootstrap prompts for a password of at least 12 characters. Migration is a separate startup service; backend/controller/frontend wait for it. The database must be PostgreSQL. Do not replace it with SQLite to hide a startup failure.

Open **http://localhost:8080** and sign in. Grafana is at **http://localhost:3000**; its generated administrator password is in your local `.env`. MLflow is loopback-only at **http://localhost:5000**. The console provides persisted inventory, camera/source, event, model, metric, drift and campaign API views when the backend is database-ready. Empty pages contain no fake live counts.

For Windows PowerShell, run `scripts/setup.ps1`, then `scripts/start.ps1`; use the same Compose bootstrap command. WSL2 is the intended CPU-worker environment.

## Install native tools / start an agent

Run in the extracted root. `uv.lock` is the dependency-resolution lock; `requirements.lock` is its exported base environment. Training requires the optional training dependency group.

```bash
uv sync --frozen
.venv/bin/python -m scripts.release keygen --directory var/keys --key-id demo
mkdir -p var/trusted_keys
cp var/keys/demo.pub var/trusted_keys/demo.pub
docker compose --env-file .env -f infrastructure/compose.yaml cp var/keys/demo.pub backend:/data/trusted_keys/demo.pub
```

The private `.key` stays offline/local. Copy only the `.pub` file to the backend/agent trust stores. Changing trust keys is an administrator action, not a dashboard upload.

Create an explicitly simulation-only release using a real MLflow run and uploaded fixture artifacts:

```bash
.venv/bin/python -m scripts.release simulation --url http://localhost:8080 --email operator@example.org --tracking-uri http://localhost:5000 --private-key var/keys/demo.key --key-id demo --version sim-0.1.0 --output var/simulation-release.json
```

Use the returned `release_id` in the next command (replace `RELEASE_UUID`):

```bash
.venv/bin/python -m scripts.seed_fleet --url http://localhost:8080 --email operator@example.org --release-id RELEASE_UUID
```

The default seed requests **800 sites and 8,000 simulated inventory records**, idempotently by deterministic names. It starts no agents. This operation was not run successfully in the build environment and is not a claim that 8,000 devices exist there.

Select 10 distinct simulated device UUIDs from Fleet and save a JSON array as `var/device-ids.json`. Start their actual enrollment/heartbeat/reconciliation clients:

```bash
.venv/bin/python -m simulation.run --url http://localhost:8080 --email operator@example.org --device-ids var/device-ids.json --state var/simulation --trust var/trusted_keys
```

These clients use persistent identities, SQLite outboxes, signed manifests and actual APIs. Their worker observations are synthetic and labeled `simulated_worker`. They do not represent physical cameras. This full fleet journey remains **IMPLEMENTED — NOT RUNTIME VERIFIED**.

Create a second release with `--version sim-0.1.1`, select the 10 active devices in Fleet, and create a campaign. Wait for at least five minutes of baseline evidence. Start the campaign, inspect gates, and manually advance its cumulative 1 → 3 → 10 assignments. Gate readiness requires five minutes of candidate observation plus at least 100 samples per target. A missing/stale window never passes.

For a selected simulator identity:

```bash
.venv/bin/python -m scripts.simulate_fault --state var/simulation/DEVICE_UUID --fault unhealthy
.venv/bin/python -m scripts.simulate_fault --state var/simulation/DEVICE_UUID --fault unhealthy --recover
```

`offline` is also supported. Request rollback in Deployments. Offline targets remain pending until they reconnect. These are commands to exercise implemented behavior, not evidence that the campaign/rollback acceptance criteria passed.

## Real PPE path and its current blocker

The attributed external baseline is recorded in `training/PPE_SOURCE_MANIFEST.json`. Publisher-declared license: CC BY 4.0. Source classes Person, Hardhat and NO-Hardhat map to person, helmet and no_helmet. The published SHA-256 is pinned; the artifact could not be downloaded in this environment.

```bash
.venv/bin/python -m scripts.fetch_ppe --output var/models/ppe-baseline.onnx
```

The downloader checks bytes and model class metadata. Geometry review, labeled temporal evaluation and quality gates remain necessary. A generic person detector is never substituted. `scripts.run_video` implements a development video pipeline with explicit registered provenance, but a raw-evidence importer and policy recomputation now exist, but their full real-data/MLflow/PostgreSQL journey and parity qualification remain unverified. **Do not treat this continuation as a qualified real PPE release.** Do not use the simulation release on a real device.

For independent plumbing verification, run:

```bash
.venv/bin/python -m scripts.verify_continuation
.venv/bin/python -m scripts.verify_quality_math
.venv/bin/python -m scripts.verify_components
.venv/bin/python -m scripts.verify_security
.venv/bin/python -m scripts.verify_runtime
# Edge/ML platform (no database, no GPU required):
.venv/bin/python -m scripts.verify_model_contract
.venv/bin/python -m scripts.verify_edge_platform
```

`verify_model_contract` proves the CPU adapter and the NVIDIA parser share one canonical
contract (and checks an independent decoder against it). `verify_edge_platform` exercises
inference runtimes, telemetry, hardware profiles, video backends, RTSP reconnection,
temporal analyzers and a real `Detector → ByteTrack → zone dwell → outbox` run, writing
`docs/evidence/edge-platform-runtime.json`. Neither needs PostgreSQL, Docker, a camera or
a GPU; both report absent hardware as absent.

`verify_components` generates labeled-as-synthetic engineering media and a constant-output ONNX fixture in a temporary directory. It is not a real-PPE demonstration. `verify_runtime` requires installed frontend dependencies (`cd frontend && npm ci`) and records database readiness as BLOCKED when PostgreSQL is unavailable.

## Local RTSP and export journeys

```bash
docker compose -f infrastructure/compose.rtsp.yaml up -d mediamtx
.venv/bin/python -m scripts.publish_rtsp --video var/media/ppe-2.mp4 --url rtsp://localhost:8554/live
# then set VISIONOPS_VIDEO_BACKEND=opencv|gstreamer|auto for the edge worker
```

Full detail and the verified/unverified boundary: `docs/LOCAL_RTSP.md`.

```bash
.venv/bin/python -m scripts.export_onnx --checkpoint var/tools/hansung-best.pt \
    --output var/model/hansung-export.onnx --report docs/evidence/onnx-export-parity.json
```

Export is never self-approving: the command fails unless raw PyTorch and raw ONNX
tensors agree through the same decoder at both the operating and a low stress threshold.
ARM64/Jetson targets: `docs/JETSON_DEPLOYMENT_TARGET.md` (no physical hardware here). Physical
Jetson qualification is prepared as one bundle with one command — `scripts/make_jetson_bundle.py`
builds it, `RUN_ON_JETSON.md` is what the operator follows, and the gate refuses non-Jetson hosts
with exit 3. Nothing in it is verified until a real device returns its evidence ZIP.
Rollback paths and their failure experiments: `docs/ROLLBACK_STRATEGY.md`.
Logical fleet scale driver: `simulation/fleet_scale.py` + `docs/10K_FLEET_SCALING_REPORT.md`.

## Native frontend/backend development

With your own running PostgreSQL 16, export `DATABASE_URL`, `TOKEN_PEPPER`, `APP_ORIGIN=http://localhost:5173`, `DATA_DIR`, `MEDIA_ROOT`, `TRUSTED_KEYS_DIR`, and `MLFLOW_TRACKING_URI`. Use the same names as `.env.example`; Compose-only service hostnames are not native-host addresses.

```bash
.venv/bin/alembic -c backend/alembic.ini upgrade head
.venv/bin/python -m scripts.bootstrap_users --email operator@example.org
.venv/bin/uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

In separate terminals, run `.venv/bin/python -m backend.app.controller` and `cd frontend && npm ci && npm run dev`. Open http://localhost:5173. The Vite proxy forwards `/api` to the backend. No production TLS deployment was verified; keep this HTTP route loopback-only.

Troubleshooting: database readiness 503 means PostgreSQL/migrations are unavailable; do not interpret liveness 200 as database readiness. An empty release picker means no approved compatible release exists. Missing weights fail model load. Unknown source health blocks campaign eligibility. See the reports for other incomplete behavior and actual evidence.
