# 19 — Proposed implementation repository structure

This is a structure specification, not generated application code. Root folder for the later implementation release is `visionops/`. Current deliverable is `visionops-specification/` with only the 22 requested Markdown documents.

| Path | Responsibility / required content |
| --- | --- |
| START_HERE.md | Exact fresh-extraction launch, CPU defaults, Windows/WSL2 and Linux, required model/data setup, URLs and limitations |
| README.md | One-minute story, architecture, screenshot/demo pointer, honest scale/hardware status |
| .env.example, .gitignore | Complete safe environment contract; exclude real env, secrets, media and caches |
| pyproject.toml, uv.lock | Backend/agent/shared Python baseline dependency lock; isolated training lock if incompatible |
| frontend/package.json, frontend/package-lock.json | Declared UI dependencies and deterministic install |
| frontend/src/app/ | Router, navigation, auth session/provider, protected actions |
| frontend/src/pages/ | Login, Overview, Sites, Fleet/DeviceDetail, Events/EventDetail, Models/ModelDetail, Deployments/DeploymentDetail, Drift |
| frontend/src/components/ | Tables, status/evidence badges, accessible dialogs, empty/error/loading states, metric cards |
| frontend/src/api/ | Typed clients generated/aligned from actual OpenAPI; no invented routes |
| frontend/src/styles/ | Tailwind tokens and global accessibility styling |
| backend/app/main.py | FastAPI composition and readiness |
| backend/app/api/ | auth, sites, cameras, video_sources, devices, events, models, deployments, metrics, drift routes |
| backend/app/domain/ | Canonical schemas, enums, policies and validated value objects |
| backend/app/services/ | Business transactions, authorization, lineage/manifest and snapshot services |
| backend/app/db/ | SQLAlchemy models, sessions and repository queries |
| backend/app/controller/ | Leader election, ring assignment/gates, rollback and stale-device sweep |
| backend/app/security/ | Session/device verification, RBAC, artifact signatures, safe uploads |
| backend/app/telemetry/ | Prometheus exposition, persisted window exporter, OTel/JSON logging |
| backend/alembic/ | Reproducible migrations, constraints, indexes and retention jobs |
| shared/contracts/ | JSON schemas for desired state, heartbeat, worker IPC, artifacts and events; referenced by Python/UI generation |
| edge/agent/ | Enrollment, HTTPS client, reconciliation and scheduling |
| edge/state/ | SQLite migrations, outbox, activation journal and crash recovery |
| edge/supervisor/ | Fixed worker launcher, two-slot activation and watchdog |
| edge/sources/ | File/RTSP adapters, reconnect and freshness policies |
| edge/pipeline/cpu/ | FFmpeg/OpenCV/ONNX Runtime implementation |
| edge/pipeline/deepstream/ | Vendor pipeline config, output parser bridge and qualified profile docs |
| edge/tracking/ | ByteTrack and NvDCF output adapters |
| edge/rules/ | Temporal no-helmet association/cooldown; deterministic only |
| edge/preview/ | Loopback annotated preview; no WAN exposure |
| training/dvc.yaml, training/params.yaml | Dataset validation/split/train/evaluate/export DAG and parameters |
| training/data/ | DVC metadata and split/license manifests; raw media excluded |
| training/train/ | YOLO training and MLflow run integration |
| training/export/ | ONNX export/parity, optional TensorRT build by target profile |
| training/evaluation/ | Detector/event metrics, benchmark protocol and raw-evidence outputs |
| training/drift/ | Histogram screening, review/export CLI and retraining handoff |
| training/requirements.lock | Separate exact training dependency set if needed |
| simulation/seed/ | Idempotent 800-site/8,000-device seeded inventory generator |
| simulation/agents/ | Real enrollment/heartbeat/reconciliation clients with isolated SQLite identities |
| simulation/scenarios/ | Deterministic offline, latency, crash, corrupt-download scenarios with explicit simulated mode |
| observability/prometheus/ | Scrape config, recording/alert rules |
| observability/grafana/ | Provisioned datasources and dashboard JSON |
| observability/otel/ | P0 SDK settings; P1 Collector/Tempo configuration |
| observability/loki/ | P1 Loki/Alloy configuration |
| infrastructure/compose.yaml | CPU default services and isolated networks/volumes |
| infrastructure/compose.simulation.yaml | Active simulator profiles and resource caps |
| infrastructure/compose.nvidia.yaml | Verified GPU worker image/profile override |
| infrastructure/compose.observability-extra.yaml | Optional P1 stores |
| infrastructure/docker/ | Digest-pinned base image Dockerfiles; unprivileged users |
| infrastructure/nginx/ | TLS/same-origin routing, upload/time caps, private paths |
| scripts/setup.sh, scripts/setup.ps1 | Bootstrap configuration and prerequisites |
| scripts/start.sh, scripts/start.ps1 | Documented CPU start equivalents |
| scripts/bootstrap_users.py | Interactive local first-account creation; no permanent defaults |
| scripts/enroll_device.py | One-use enrollment, secure credential persistence |
| scripts/release.py | Manifest signing/approval client; offline private key |
| scripts/seed_fleet.py, scripts/simulate_fault.py | Honest inventory/active simulation and controlled failures |
| scripts/verify_runtime.py | Runtime evidence collection/acceptance runner, no conventional testing framework |
| scripts/package_release.py | Clean release staging, secret/junk checks and ZIP inspection |
| .github/workflows/ | CPU dependency/build, actual AI eval when fixtures available, security/config audit and packaging; optional GPU runner |
| docs/specification/ | Exact 22 files from this specification pack |
| docs/ASSUMPTIONS_AND_DECISIONS.md | Working implementation decisions and deviations |
| docs/IMPLEMENTATION_REPORT.md | Built versus missing with actual architecture and dependencies |
| docs/VERIFICATION_REPORT.md | Commands, environment, journey/failure outcomes and blocked items |
| docs/AI_EVALS.md | Dataset, protocol, actual detector/event/performance results |
| docs/REQUIREMENTS_TRACEABILITY.md | R-ID → source/API/UI → AC-ID → evidence/status |
| docs/HARDWARE_COMPATIBILITY.md | Exact qualified or unverified SDK/driver/device tuples |
| docs/evidence/ | Small redacted runtime/eval summaries, not raw private footage |

## Runtime volumes, not repository content

PostgreSQL data, MLflow artifact store, central artifact/evidence directories, edge SQLite and active/previous slots, camera media, keys and `.env` live in named volumes or external mounted directories. Setup must create them and document backup/reset effects. Pack no node_modules, .venv, caches, logs, private keys, raw sensitive media or unsupported redistributable weights. GPU engines are profile-specific artifacts, not generic source assets.

## Ownership boundaries

Only backend/controller writes central domain state; simulator calls the same APIs. Only edge agent writes its slot pointers/outbox. Training tools use APIs/MLflow/DVC and never mutate central campaign tables. Frontend owns presentation, not authorization or deployment transitions. Shared contracts must remain small and domain-specific; no microservice sprawl.
