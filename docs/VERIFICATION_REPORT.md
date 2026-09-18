# Verification report

**Overall end-to-end status: BLOCKED. Complete P0 software status: NOT IMPLEMENTED.** Successful component checks below are not a full release approval.

| Exercise | Status | Evidence / result |
| --- | --- | --- |
| Python dependency installation and compatibility | VERIFIED | `uv pip check --python .venv/bin/python`: 178 installed packages, all compatible; locks generated |
| Import/compile application modules | VERIFIED | `python -m compileall -q backend edge training scripts simulation` completed |
| Frontend production build | VERIFIED | `npm run build`: TypeScript and Vite succeeded after JSX syntax fix |
| Backend process startup / HTTP liveness | VERIFIED | `scripts.verify_runtime`: HTTP 200 `{status:alive}` |
| PostgreSQL readiness | VERIFIED | Live Compose stack: `/health/ready` 200, migrations applied, 31 tables present |
| Prometheus-format application counters | VERIFIED | Actual scrape contained `visionops_http_requests_total` after application requests |
| Prometheus scrape + Grafana dashboard (database-backed) | PARTIAL | Target `visionops` up, 16 `visionops_*` families, dashboard "VisionOps application metrics" loads; per-camera CV metrics carry simulator values only |
| Frontend development HTTP server | VERIFIED | HTTP 200 at loopback Vite server; root HTML served |
| Browser interaction and frontend/backend authenticated journey | BLOCKED | Database unavailable; browser interaction was not exercised |
| Synthetic engineering video → ONNX → ByteTrack → rule → SQLite | VERIFIED | `component-runtime.json`: 23 inference frames, one durable event; fixture explicitly constant-output and untrained |
| Durable outbox restart / duplicate receipt | VERIFIED | Event remained after reopening SQLite; duplicate receipt removed it |
| Model digest tampering | VERIFIED | Invalid digest rejected before inference session creation |
| Unknown/conflicting head evidence | VERIFIED | Rule emitted no violation for unknown observations; conflicting labels classified unknown |
| Ed25519 valid/tampered manifest | VERIFIED | Valid signature accepted; modified manifest rejected |
| Tar traversal and symlinks | VERIFIED | Malicious archive members rejected |
| PostgreSQL schema and API presence audit | VERIFIED | Static only: 30-table DDL compiled; all 72 specified method/routes represented, including a shared download handler |
| PostgreSQL persistence, roles, enrollment, device desired/actual state, event ingest and API read-back | VERIFIED | Release → device → agent reconciliation → heartbeat → SafetyEvent → API exercised against the running stack |
| Canary campaign execution and central rollback against PostgreSQL | BLOCKED | Campaign/rollback transaction concurrency not exercised |
| Real PPE artifact qualification and PT↔ONNX parity | VERIFIED | Hansung ONNX parity 24/24, IoU min 0.981; `docs/evidence/hansung-onnx-qualification.json` |
| Real PPE accuracy / temporal event-quality evaluation | BLOCKED | No labeled PPE dataset or ground truth; the promotion gate correctly refuses a `real` release |
| NVIDIA runtime / conversion / benchmarking | BLOCKED | No compatible hardware or runtime |
| Clean archive extraction / integrity | VERIFIED | Final packaging audit checks required paths, CRCs, exclusions and exact specification copies; see `archive-audit.json` alongside the ZIP |

## Failures found and corrected

Dependency state from the previous attempt was absent; the Python environment and npm packages were reinstalled. A JSX handler was missing a closing brace; it was corrected and the production build rerun. A generic POST upload route could shadow named campaign routes; uploads and campaign commands were registered explicitly. Vite's wildcard host startup triggered a sandbox network-interface lookup failure; switching to loopback binding allowed actual HTTP startup. Release/config slots were separated so a source-only change cannot overwrite the previous slot's configuration; verified-content cache reuse avoids repeated downloads.

PostgreSQL root execution was not worked around by weakening server protections. `pgserver` could not create its OS user; `runuser -u oai -- id` failed with `Operation not permitted`. Effective capabilities were zero. There is no central SQLite fallback.

## Evidence discipline

`docs/evidence/*.json` contains measured component results and static audit boundaries. No successful database request, 8,000-record seed, active simulator fleet, canary or rollback run is claimed. No real-PPE accuracy, FPS or GPU metric is claimed. Native runtime versions differ from the intended container versions and are recorded in `environment.json`.

## Continuation runtime results

The existing release was extracted and edited in place in a separate working copy. Its architecture and the 22 authoritative specification files were preserved.

| Continuation exercise | Status | Evidence |
| --- | --- | --- |
| Rollback lease bound/reclamation | VERIFIED | Production allocator exercised directly: at most five active leases, convergence transfer, offline reclamation. This is not a PostgreSQL transaction exercise. |
| Post-commit watchdog | VERIFIED | Three actual subprocess exits, exponential restart waits, previous-worker restoration and full 60-second recovery. Applied generation 2 retained; rejected generation 2 reported. Engineering workers, not CV. |
| Metric arithmetic | VERIFIED | Known-answer precision/recall/F1, event matching, nearest-rank p50/p95 and insufficient-evidence gate rejection. No real-model accuracy claim. |
| Frontend build / HTTP startup | VERIFIED | Existing UI production build and actual HTTP serving passed again. |
| API liveness / metrics | VERIFIED | Actual HTTP 200 and Prometheus exposition; database readiness correctly returned 503. |
| Real PostgreSQL / device rollout | VERIFIED (single device) | Running Compose stack; release → enrolled agent → hash-verified artifact → committed actual state → persisted event. A 10-agent fleet-scale rollout was not re-run and remains unverified. |
| PPE model retrieval | VERIFIED | Hansung PPE YOLOv8n checkpoint obtained, qualified and exported; see `docs/evidence/HANSUNG_PPE_E2E_VERIFICATION.md`. The previously pinned Hexmon artifact remains unavailable. |
| Persisted observation exporter / MLflow evidence importer | IMPLEMENTED — NOT RUNTIME VERIFIED | Code added; required database/model/MLflow journeys unavailable. |
| NVIDIA launcher/parser compilation | BLOCKED | Runtime absent; compile prerequisite outcome recorded separately. |

New evidence: continuation-runtime.json, quality-arithmetic.json and continuation-environment.json. Full P0 completion remains NOT IMPLEMENTED. Only the listed component claims moved to VERIFIED.

## Hansung PPE production qualification

A real PPE detector now exists and the central stack is live, so several earlier BLOCKED rows moved. Summary: model artifact qualified (`var/model/hansung-p3.onnx`, sha256 `b239aa7e…42`, opset 17); PT↔ONNX parity 24/24 at conf 0.35 (IoU min 0.981, max Δconf 0.0227); `Detector` verified against the 14-channel/10-class output with adapter remapping `{5:0, 0:1, 2:2}`; release `ppe-hansung-v1` approved and reconciled onto an enrolled edge device whose staged artifact hash equals the released hash; the clip's real-video rule result is **0 genuine violations** (negative case, thresholds not lowered); SafetyEvent transport verified separately with an explicitly labelled `integration_verification` event (outbox retained it while the backend was unreachable, then drained); Windows `WinError 32` cleanup defect fixed and `scripts.verify_components` now exits 0.

Honest boundaries: the release's evaluation record is `evidence_mode=simulated` because no labeled temporal PPE benchmark exists, so the real promotion gate is **BLOCKED on evaluation data**, not software; the device under test is `simulated`, so no real worker process runs and Prometheus per-camera CV metrics carry simulator values; CPU inference measured p50 118 ms / p95 320 ms (≈5.5 FPS), which is functional verification only and not NVIDIA/TensorRT performance. **TensorRT/NVIDIA remains BLOCKED BY LOCAL HARDWARE.** Detail: `docs/evidence/HANSUNG_PPE_E2E_VERIFICATION.md`.
