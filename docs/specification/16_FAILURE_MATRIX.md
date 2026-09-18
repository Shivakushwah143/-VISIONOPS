# 16 — Failure matrix

Policies refer to [05_FRD.md](05_FRD.md), [11_EDGE_AGENT_CONTRACT.md](11_EDGE_AGENT_CONTRACT.md) and [12_OBSERVABILITY_SPEC.md](12_OBSERVABILITY_SPEC.md). Simulated failure injection must be labeled and must not modify real-device state.

| Failure | Detection | Required system behavior | User-visible behavior | Recovery / evidence |
| --- | --- | --- | --- | --- |
| Camera offline | connect error/no decoded frame >10 s | isolate camera, reconnect with jitter | camera reconnecting; device may remain online | restore feed, new stream session; reconnect counter |
| RTSP timeout | socket/read timeout | discard stale pending frames; reopen source | source error with safe reason | 1–30 s backoff; actual progress required |
| Packet loss | decoder warnings/drop counts | bounded buffering; TCP default; do not block sibling streams | dropped frames and degraded source | stable network; verify frame freshness |
| Frozen stream | content repeats >30 s with advancing timestamps | mark suspected, avoid claiming definite outage from static scene | frozen_suspected, last progress/content info | inspect feed or configured reconnect; new frames clear |
| File end | decoder EOS | ended unless explicit loop config; loop creates new session | ended or clearly marked replay | operator restarts or loop continues |
| Decoder failure | process exit/decoder error | restart only failed pipeline, bounded retry | camera error and decoder code | correct codec/dependencies/source |
| Inference failure | exception/no worker IPC | stop accepting invalid detections, watchdog restart | online device + unhealthy pipeline | restore known-good worker/model |
| CUDA unavailable | capability preflight | reject NVIDIA profile; never claim GPU use | hardware BLOCKED; explicit CPU option | choose separately approved CPU release or install supported environment |
| TensorRT incompatibility | profile/engine load check | reject before switch; preserve old worker | incompatible artifact reason | rebuild/evaluate engine for exact profile |
| GPU OOM | allocation failure | unload candidate, local rollback, no endless retry | failed generation; campaign paused | lower workload via approved config/release and re-evaluate |
| Thermal throttling | sensor limit/profile and FPS regression | warn, pause unhealthy campaign; never change model secretly | temperature/FPS with real values | cooling or explicit lower sampling config |
| Queue backlog | depth/drop/age thresholds | drop oldest waiting frames, keep bounded memory | freshness/drop warning | tune sampling or capacity; measure again |
| Device offline | heartbeat age >60 s | preserve latest desired; never mark target healthy | offline and pending desired version | reconnect, fetch newest generation |
| WAN/control-plane outage | HTTPS failure/timeouts | local inference continues; durable bounded outbox | cloud disconnected; last known freshness | jitter reconnect, idempotent replay |
| Outbox full/TTL expired | cap/expiry check | drop oldest as last resort, durable loss counter | explicit data-loss warning | upload recovery; capacity/retention review |
| Artifact download interrupted | short read/hash incomplete | keep .part/ETag, retain last good | downloading/retry, not success | resume verified range or restart |
| Hash/signature invalid | checksum/Ed25519 fail | quarantine candidate; no activation | artifact rejected + audit | correct signed release, newer generation |
| Runtime archive unsafe | member/path validation | reject staging, no privileged extraction | validation failure | rebuild safe bundle |
| Crash during activation | startup activation journal | recover verified active/last-good slot, no false applied_generation | recovery/degraded and timeline | restart agent; reconcile journal |
| Candidate unhealthy | local 60 s gate fails or 3 crashes/5 min | local rollback; quarantine generation | actual differs from desired; pause | central rollback/new approved release |
| Canary metric regression | 3 failing 15 s windows | auto-pause assignments | failed gate and affected devices | explicit rollback or cause resolved + resume |
| Missing rollout telemetry | age >30 s; >60 s outage | no gate pass; pause after 60 s | unknown health, paused reason | restore accepted windows then fresh gate |
| Central rollback offline target | no new generation acknowledgement | keep rollback pending; after 10 min incomplete | rollback_incomplete, outstanding targets | retry pending and reconnect; no false complete |
| Local rollback also fails | last-good worker cannot start | degraded, bounded restart, no random release | recovery failed, manual action required | repair base runtime or deploy qualified bundle |
| Stale desired/acknowledgement | generation lower than current | ignore obsolete candidate/report for target completion | latest generation remains authoritative | poll current desired and reconcile |
| Concurrent campaigns/config edit | row lock/CAS/active target constraint | reject conflicting mutation | 409 with retry explanation | finish/cancel/rollback active campaign |
| Controller crash | advisory-lock session lost/no progress | new leader resumes durable state; no duplicate generation | campaign delayed | restart; lock/CAS prevents double assignment |
| Database unavailable | readiness/query failure | API 503; no acknowledge uncommitted events; controller stops writes | unavailable/stale UI | restore DB; agents replay IDs |
| MLflow unavailable | registry request fails | block new lineage/release checks; existing approved artifacts unaffected | registry unavailable; existing fleet visible | retry registration with same provenance |
| Prometheus/Grafana unavailable | scrape/query error | existing DB health windows remain authoritative; mark chart missing | charts unavailable, no invented zeros | restore service; do not block local inference |
| Logs/traces unavailable | exporter queue errors | bounded drop/counter; never block video | observability degraded | restore sink and inspect local logs |
| Model quality regression | labeled eval gate or reviewed feedback | no approval; pause if deployed risk confirmed | failed quality evidence | rollback champion, collect labels, retrain |
| Missing PPE data/weights | startup/lineage/class-map checks | block real PPE pipeline; no fake no-helmet detector | explicit setup requirement | acquire permitted data/train/evaluate |
| Invalid device credential | 401/revoked lookup | stop central upload retries at rapid rate; retain local inference | unauthorized device; operator action | re-enroll/rotate per policy |
| Clock skew | observed vs server receipt discrepancy >30 s | use server time for liveness; flag latency uncertainty | clock warning | synchronize clock; no fabricated network latency |
| Evidence expired/missing | storage lookup/retention | return 410/404 while metadata remains | evidence unavailable with reason | no reconstruction from unrelated media |

Failure injection evidence is a runtime observation artifact with timestamp, actor, scenario, mode, expected/actual transition and recovery. It is not proof of physical hardware failure recovery unless exercised on the real path.
