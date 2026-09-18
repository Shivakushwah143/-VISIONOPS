# 17 — Measurable acceptance criteria

This is a future runtime acceptance matrix, not a report of completed implementation. All current implementation/evaluation evidence is NOT EXECUTED. Do not install conventional application test frameworks; exercise runtime journeys, inspect persistent state and run meaningful computer-vision evaluations.

| Criterion | Requirements / journey | Procedure and observable pass condition | Evidence and constraint |
| --- | --- | --- | --- |
| AC01 | R01 / A | Bootstrap four users; each logs in. Direct forbidden API actions return 403; session expiry/revoke returns 401 | API responses and UI capture; hiding buttons insufficient |
| AC02 | R02 / B | Create site/device/camera/file source; restart API; records remain. Invalid cross-site assignment and traversal rejected | PostgreSQL rows and request evidence |
| AC03 | R03 / A | Real licensed sample video produces person/PPE boxes and person track IDs via CPU ONNX, local preview and sampled DetectionEvents | Actual video/model hashes; no seeded detections counted |
| AC04 | R04 / A | Qualifying labeled no-helmet sequence emits one event after ≥5 observations over ≥2 s, within rule cooldown; compliant/unknown head examples do not satisfy rule | Frame/event matching evidence; full accuracy separately evaluated |
| AC05 | R04 / A | Event reaches actual backend/DB/UI; online emission-to-visible p95 ≤10 s across ≥20 events | Timestamped observations, hardware/network context; optional snapshot missing state exercised |
| AC06 | R05 / B,C | One-use enrollment rejected on replay; heartbeats appear within one UI poll after acceptance; >60 s silence displays offline | Identity/receipt timestamps; inactive seeded records never_seen |
| AC07 | R05 / C | Different actual/desired release and generation shown until committed application reported; stale sequence does not overwrite state | DB and UI snapshots |
| AC08 | R06 / H | Disconnect WAN for 2 minutes while real worker continues; generate events, restart agent, reconnect; accepted event IDs appear once | SQLite persistence, replay receipts; cap/TTL loss behavior separately demonstrated |
| AC09 | R07 / D,E | DVC dataset, real MLflow training run/model version, ONNX bytes/hash and EvaluationReport linked in Models UI | Actual run evidence; absent data/hardware marked BLOCKED |
| AC10 | R07 / D | Execute PyTorch/ONNX eval on fixed split; record all document 13 metrics and gate decisions, no invented values | MLflow evidence; failed gates cannot approve real release |
| AC11 | R08 / F | Ten compatible simulated agents with baseline: 1→3→10 cumulative assignments, with gate-ready + manual advance, then completed | Real API/DB campaign history; simulated worker metrics labeled |
| AC12 | R08 / G | Inject canary latency fault; 3 failing windows pause campaign and prevent next ring. Rollback restores previous release at newer generation | Timeline and agent actual reports, not UI-only animations |
| AC13 | R06,R08 / H | Offline target during rollback remains pending/incomplete; reconnection applies latest rollback generation rather than superseded candidate | Controller/agent logs and generation history |
| AC14 | R09 / C | Seed exactly 8,000 devices/800 sites, activate 10 unique agents; paginated list limit≤200, connectivity totals partition inventory | Record real/simulated/never_seen counts; no physical fleet claim |
| AC15 | R09,R10 / C | At recorded host limits, 30-minute inventory/load run with 10 agents meets proposed list p95<1 s and API success ≥99%, to pass; any missed target is FAILED with actual result | Raw duration/error/resource evidence; scale to 50 optional |
| AC16 | R10 / C,G | Prometheus receives increasing real pipeline counters; Grafana plots FPS/latency/age. GPU fields unavailable on CPU. Missing telemetry cannot pass canary | Actual scrapes and panels; simulated metrics separated |
| AC17 | R11 / I | Eligible signal → accepted example labels → new DVC version → linked retraining request/run; no desired state change until approved campaign | IDs and mutation audit; insufficient-data branch demonstrated |
| AC18 | R12 / E,G | Corrupt bytes/signature, wrong profile and unsafe bundle rejected before activation; prior worker continues | Agent validation and audit evidence |
| AC19 | R12 / B | Cross-device heartbeat/event rejected; credential revoke prevents central access; secrets absent from logs/release ZIP | Runtime API observations and archive inspection |
| AC20 | R13 / A–I | Every declared route loads; form/list loading/empty/error/permission/stale states visible; no dead action buttons | Journey captures and actual requests |
| AC21 | R13 / release | Fresh extraction with documented prerequisites/config starts central services, CPU path and simulator; no dependency on files outside release except documented media/weights/secrets | Exact commands/exit states in VERIFICATION_REPORT |
| AC22 | R03,R07 / GPU path | On compatible NVIDIA hardware only: DeepStream decode/infer/track and TensorRT FP16 eval meet qualified gates | Hardware inventory, versions and raw metrics; otherwise BLOCKED, not pass |
| AC23 | R08 / G | Restart controller during assignment and agent during staging; one authoritative generation, last-good recovery, no duplicate target success | Persistent state and actual runtime recovery evidence |

## Release decision

P0 functional release requires implemented R01–R13, real CPU critical journey, active simulator campaign, security controls and actual metrics. Accuracy/performance failures remain visible and block real release approval; a simulation-only showcase must not be called complete real PPE deployment. Hardware-dependent AC22 may remain BLOCKED with explicit documentation and working CPU alternative. Missing licensed PPE data prevents claiming AC03/04/09/10 passed; package may be delivered only with that material limitation prominently identified.

Record status per criterion: VERIFIED; IMPLEMENTED — NOT RUNTIME VERIFIED; BLOCKED BY EXTERNAL SERVICE/ENVIRONMENT; NOT IMPLEMENTED. Add AI-EVALUATED when actual model evaluation ran, and HARDWARE VERIFIED only with real compatible hardware evidence. Present passing specification consistency separately from application acceptance.

## Traceability audit for implementation

Map each R-ID → relevant source files/API/UI → AC-ID → evidence path and status in docs/REQUIREMENTS_TRACEABILITY.md. Inspect actual storage for durability/identity claims and actual API responses for permission claims. Avoid broad unsupported claims such as scalable, production-ready, zero downtime or 8,000 deployed devices.
