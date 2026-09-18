# 04 — User stories

| Story | Persona and concrete intention | Acceptance intent | Requirement / journey |
| --- | --- | --- | --- |
| US01 | Safety viewer signs in and opens recent events for a site | Only allowed APIs succeed; empty feed explains no events | R01, R04 / A |
| US02 | Safety viewer inspects no-helmet evidence and acknowledges it | Track, source, timestamps, release and evidence visible; acknowledgement audited | R04 / A |
| US03 | Operator adds a camera and assigns its RTSP source to an edge device | Bad URL or cross-site assignment rejected; connecting/running/error visible | R02 / B |
| US04 | Operator enrolls a device using a one-use token | Token expires and cannot be reused; device receives its own credential | R05 / B |
| US05 | Operator investigates stale inference on an online device | Camera, inference and control-channel health distinguished | R05, R10 / C |
| US06 | CV engineer registers a labeled dataset and completed training run | Exact DVC/Git/MLflow identifiers displayed without invented metrics | R07 / D |
| US07 | CV engineer submits ONNX artifact evaluation | Evaluation provenance and CPU/GPU profile shown; failed gates block approval | R07 / D, E |
| US08 | MLOps engineer approves a release and selects devices | Digests, signature, compatibility and baseline eligibility validated | R08, R12 / E, F |
| US09 | MLOps engineer starts canary and inspects stage evidence | No next ring before minimum observation, sufficient samples and explicit advance | R08 / F |
| US10 | MLOps engineer pauses and rolls back an unhealthy canary | New generation points to previous approved release; history preserved | R08 / G |
| US11 | Operator reconnects an offline device after a rollback | Device applies newest desired generation, never obsolete queued release | R06, R08 / H |
| US12 | CV engineer reviews drift examples and requests retraining | Review decision and labels recorded; training does not deploy automatically | R11 / I |
| US13 | Operator demonstrates 8,000-device inventory | Table paginated; 8,000 seeded records and active simulators labeled separately | R09 / C |
| US14 | MLOps engineer checks deployment audit | Actor, before/after state, gate evidence and reason retained | R12 / E–H |
| US15 | Operator starts the project without a GPU | CPU inference works or reports missing weights; GPU panels say unavailable | R03, R13 / A |

Every story inherits server-side RBAC from [15_SECURITY_SPEC.md](15_SECURITY_SPEC.md) and the shared UI states in [05_FRD.md](05_FRD.md).
