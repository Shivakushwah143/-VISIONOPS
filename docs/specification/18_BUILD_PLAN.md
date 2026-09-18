# 18 — Build plan

Build vertical slices. The phase order below keeps a minimal UI and authorization present early; Phase 12 refines and completes the UI rather than starting it after an isolated backend. Every phase produces code/config only during the later implementation task. No implementation code is part of this specification.

| Phase | Objective and files/services | Dependencies | Output | Runtime verification / definition of done |
| --- | --- | --- | --- | --- |
| 1 | Skeleton, typed API contracts, auth, PostgreSQL migrations, Compose and initial UI: frontend/, backend/, infrastructure/, scripts/ | Docs 07,09,10,15 | CPU profile boots; login/roles, empty Overview; actual dependency locks | Start services from clean env; DB readiness and four-role denial evidence; no hardcoded passwords |
| 2 | Real baseline video inference: edge/pipeline/cpu/, training/evaluation/ | Phase 1; licensed PPE data/weights | Sample video → ONNX PPE predictions → loopback preview, source status visible | Real boxes from identified bytes; missing model error; record CPU performance; AC03 groundwork |
| 3 | Source management: backend/api/cameras,video_sources; frontend/Sites; edge/sources/ | 1–2 | Persisted file/RTSP config, bounded queues, reconnect | Add/edit/disable through UI; invalid paths denied; RTSP interruption observed |
| 4 | NVIDIA adapter and profile qualification: edge/pipeline/deepstream/, infrastructure/compose.nvidia; training/export/ | 2–3, compatible host if available | Common adapter, parser/config, exact tuple; CPU unaffected | Run GPU path when available; otherwise clearly BLOCKED with runnable CPU; no fabricated engine |
| 5 | Tracking/events: edge/rules, edge/tracking, backend/events, frontend/Events | 2–3 | Normalized detections, temporal no-helmet rule, durable metadata/snapshot UI | AC04/05 real event journey; unknown heads and duplicate/cooldown behavior inspected |
| 6 | DVC + MLflow + lineage: training/, backend/lifecycle/, frontend/Models | 1–2, permitted labeled dataset | Actual training/evaluation/export registration and model dashboard | Reproduce bounded run, compare PyTorch/ONNX and gate actual metrics; failed real gates remain blocked |
| 7 | Agent: edge/agent, state, outbox, supervisor; initial enrollment UI | 3,5,6 | Unique identity, local persistence, signed bundle staging, initial approved commissioning; include minimal device registration/heartbeat API here | Enroll; survive WAN outage/restart; reject bad artifact; source config convergence |
| 8 | Fleet control: backend/fleet, controller, frontend/Fleet | 7 | Heartbeats, desired/actual generation, liveness, device detail | AC06–08; stale sequence and device-scope denial; actual API/DB/UI alignment |
| 9 | Campaigns: backend/deployments, controller, frontend/Deployments | 6–8; ten active simulators from minimal simulation runner | Frozen targets, rings, baseline gates, rollback, history | AC11–13/18/23; real persistent simulator state; no instant fake transitions |
| 10 | Fleet simulation/inventory: simulation/, scripts/seed | 8–9 | Deterministic 800-site/8,000-record inventory; 10–50 active identities | AC14/15; record load/CPU/RAM and active-vs-inventory distinction |
| 11 | Observability complete: observability/, backend/exporter, frontend metric views | Instrumentation starts Phase 1; phases 5–10 | Prometheus/Grafana provisioning, alerts, JSON logs, OTel console | Actual counters/histograms, stale/unknown views and rollout health gates; optional P1 stores separate |
| 12 | UI integration/polish: frontend pages/components/api | UI already accompanies phases 1–11 | All routes, accessible dialogs, responsive layouts, evidence badges | AC20; no disconnected controls, no static live numbers; 5–8 minute journey viable |
| 13 | Drift/retraining: training/drift, backend/drift, frontend/Drift | 6,10–12 | Histograms, accepted hard examples, DVC export, manual retraining queue | AC17 including insufficient_data; successful run never auto-deploys |
| 14 | Security hardening, failure recovery, clean install and release: scripts/, docs/, CI | All P0 slices | START_HERE, reports, traceability, AI evals, clean ZIP | AC01–23 status recorded, root causes fixed, archive inspected; no false GPU/scale claims |

## Dependency clarifications

Phase 2 can use a separately obtained, legally permitted PPE ONNX artifact before full training flow exists; record its provenance. It does not approve fleet deployment until Phase 6 evaluation and Phase 7 release controls exist. Early preview is a development worker, not a bypass of final enrollment approval. Minimal simulator runner begins Phase 9; Phase 10 adds large inventory and measured load. Phase 9 needs MetricSummary and baseline gate calculations already added with agents, while Phase 11 completes dashboards/exporters. Security is implemented at each route from Phase 1, not postponed wholesale to Phase 14.

## Execution rules

Resolve exact installed dependency versions and GPU tuple using official documentation and host discovery, record locks, then preserve them. Extend a small shared schema only when needed by a listed workflow and update every affected document. Build UI→API→logic→DB→worker→UI behavior before marking a feature complete. Re-read FRD after each slice; keep an evidence ledger with actual commands/results.

No conventional application test suite or coverage framework is requested. Perform dependency startup, runtime API/UI journeys, failure injection, clean extraction, security/config audit and meaningful computer-vision evaluations. Do not claim static inspection proves runtime behavior. Don't parallelize database schema ownership or modify hardware limits to make results appear better.

## Packaging gate

Before later implementation ZIP: re-read all docs, compare API calls/routes/models/env names, resolve errors, run CPU profile and critical journeys, evaluate AI, inspect role/device boundaries, remove credentials/caches/logs, reproduce a clean launch and inspect archive. Keep source and documentation, safe demo fixtures with license, dependency locks, provisioning configs and evidence reports. Large private datasets, weights without redistribution rights, GPU engines requiring downloads and secrets stay out; document exact retrieval/provenance requirements.
