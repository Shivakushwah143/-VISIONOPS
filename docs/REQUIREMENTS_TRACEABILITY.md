# Requirements traceability

> **Continuation 2 update.** R03 gains a static+CPU contract guarantee (one canonical
> contract, generated NVIDIA artifacts, independent decoder agreement) while the NVIDIA
> runtime stays BLOCKED. R10 gains executed video/stream/telemetry metrics. R13 gains
> `scripts/export_onnx` (export is never self-approving). R09/R08 keep their blockers but
> now have a logical-fleet driver. Full mapping of the change to the four status labels:
> [CURRENT_VERIFIED_STATE.md](CURRENT_VERIFIED_STATE.md).

No complete AC01–AC23 criterion is marked VERIFIED. Component evidence is narrower than the acceptance criteria and must not be promoted to an end-to-end claim.

| Requirement | Implementation | Acceptance | Status and evidence boundary |
| --- | --- | --- | --- |
| R01 Authentication / roles | backend security/main, frontend login | AC01 | BLOCKED: PostgreSQL-backed role/session journeys; code exists |
| R02 Sites / cameras / sources | source APIs, ConfigVersion, console forms | AC02 | BLOCKED: persistence and source editing journeys |
| R03 Real inference / tracking | edge pipeline, ByteTrack, NVIDIA adapter files | AC03, AC22 | VERIFIED (CPU): real Hansung PPE ONNX through Detector → ByteTrack → PPE association on real footage, 3 tracks over 206 frames, PT↔ONNX parity 24/24. VERIFIED (TensorRT, real NVIDIA GPU): FP32 24/24 at IoU 1.0 and true mixed FP16 24/24 at min IoU 0.9922 on a Tesla T4 — `docs/evidence/tensorrt/final/`. DeepStream runtime and physical Jetson remain unverified |
| R04 Deterministic safety events | edge rules/state, backend event APIs, Events UI | AC04, AC05 | VERIFIED (backend path): real-video rule result is a correct negative case (0 violations), plus a labelled transport event through outbox → `POST /device-events` → PostgreSQL → API read-back with duplicate and outage recovery. Console/UI journey unverified |
| R05 Device control | enrollment, heartbeat, assignment ledger, fleet UI | AC06, AC07 | VERIFIED (single device): enrollment, signed manifest fetch, hash-verified artifact staging, desired/actual convergence and heartbeat reporting. Fleet-scale and console journeys unverified |
| R06 Edge durability / updates | state, agent, worker | AC08, AC13 | IMPLEMENTED — NOT RUNTIME VERIFIED: connected WAN/update journey; watchdog component VERIFIED; full recovery/preview journey unverified |
| R07 Model lifecycle | lifecycle APIs, training/evaluation/DVC/MLflow tooling | AC09, AC10 | PARTIAL: artifact qualification, PT↔ONNX parity, MLflow lineage and an approved release are verified; real-data quality qualification (mAP, no_helmet recall, event quality) is BLOCKED on missing labeled data, so the release is `evidence_mode=simulated` |
| R08 Campaigns / rollback | campaigns/controller, simulator, deployment UI | AC11, AC12, AC13, AC23 | IMPLEMENTED — NOT RUNTIME VERIFIED: PostgreSQL execution blocked; rollback permit component VERIFIED; DB concurrency unverified |
| R09 Fleet simulation / scale | seed_fleet, simulation/run | AC14, AC15 | IMPLEMENTED — NOT RUNTIME VERIFIED: zero seeded/active fleet evidence |
| R10 Observability | HTTP metrics, metric summaries, Grafana/Prometheus config | AC15, AC16 | PARTIAL: Prometheus target is up with 16 `visionops_*` families (device health, model/artifact version, ingest counts) and the Grafana dashboard loads; per-camera CV metrics carry simulator values only and real-mode `outbox_depth` is unpopulated |
| R11 Drift / retraining | drift, hard-example/retraining APIs, review controls | AC17 | NOT IMPLEMENTED: complete reviewed-example export and retraining journey |
| R12 Security / safe activation | signatures, hashes, scopes, roles, archive checks | AC18, AC19 | PARTIAL: Ed25519 release signature, artifact hash verification at the device and archive checks were exercised on a real release/device. Full role/device audit remains unverified |
| R13 Usable release / UI | frontend, Compose, docs, packaging | AC20, AC21 | NOT IMPLEMENTED: complete UI/clean central launch; build/HTTP/archive checks verified |

Evidence files: `component-runtime.json`, `security-runtime.json`, `service-runtime.json`, `environment.json` and `contract-audit.json` under `docs/evidence/`. Static DDL/route checks verify source structure, not transaction correctness or authorization behavior. The Hansung PPE slice adds `docs/evidence/hansung-onnx-qualification.json` and `docs/evidence/HANSUNG_PPE_E2E_VERIFICATION.md` (with `var/hansung-release.json`, `var/hansung-device.json`, `var/hansung-transport.json` and `var/evidence/hansung-ppe-evidence.json`).
