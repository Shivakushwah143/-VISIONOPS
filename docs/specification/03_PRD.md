# 03 — Product requirements

## Vision

Make edge safety inference observable, traceable and reversibly deployable. The compelling demonstration combines real detection with an operational failure/recovery story.

## Scope register

Requirement IDs below remain stable across FRD, journeys, acceptance and build phases.

| ID | P0 capability | Success evidence |
| --- | --- | --- |
| R01 | Login and four-role authorization | Authorized read/write paths and server-side denial |
| R02 | Sites, cameras, file/RTSP source assignment | Persisted source config and visible runtime state |
| R03 | Real person/PPE inference and tracking on CPU fallback | Annotated local preview and normalized detection records |
| R04 | Deterministic no-helmet event and viewer acknowledgement | Event through agent → API → PostgreSQL → UI |
| R05 | Device enrollment, heartbeat and desired/actual state | Identity, freshness, mismatch and capability visible |
| R06 | Agent offline continuation and bounded durable event delivery | WAN outage does not stop local inference; replay deduplicates |
| R07 | Dataset/training/evaluation/MLflow lineage and immutable release | Real provenance, file hash and explicit evidence state |
| R08 | Canary, progressive rollout, pause and rollback | Persisted campaign transitions and restored actual release |
| R09 | 8,000 inventory records plus 10–50 active simulated agents | Separate seeded, simulated and real counts and load evidence |
| R10 | Metrics, structured logs, dashboards and alerts | Actual Prometheus samples and freshness indicators |
| R11 | Drift review and retraining handoff | Signal → review → dataset version → evaluated challenger |
| R12 | Signed artifact validation, audit and least privilege | Tampered artifact rejected before activation |
| R13 | Usable UI and clean reproducible release | All critical journeys, explicit missing-data states and launch guide |

## P0 versus hardware-dependent P0

R03 CPU inference, ONNX export/evaluation, model/app release handling and simulator must work without NVIDIA hardware. P0 also includes the NVIDIA adapter boundary, compatibility check, documented DeepStream configuration and hardware verification gate. DeepStream/TensorRT execution is hardware-dependent P0: absence of compatible hardware must be shown as BLOCKED, not silently counted as verified. No GPU performance target is required on CPU.

P0 training includes an executable DVC/MLflow workflow and a bounded real training run on a permitted small dataset where resources allow. A tiny run proves plumbing, not deployable accuracy. Model promotion always requires [13_MODEL_EVALUATION_AND_BENCHMARK_PLAN.md](13_MODEL_EVALUATION_AND_BENCHMARK_PLAN.md) gates. P0 drift is transparent histogram screening plus human review and CLI training, not automated model discovery.

## P1

Helmet-adjacent vest/forklift classes, restricted zones and line crossing, short evidence clips, INT8 calibration, larger GPU benchmarks, mTLS, hosted object storage, Loki/Alloy and Tempo trace storage. OS/firmware updates remain outside scope. No P1 item may substitute for missing R01–R13 behavior.

## Experience and value

Safety viewer opens evidence without learning infrastructure. Operators see last heartbeat, source state and desired/actual release side by side. MLOps engineer sees immutable lineage and rollout gates with reasons. CV engineer sees per-class and event metrics, not a single decorative accuracy number.

## Targets and limitations

Use the measurable criteria in [17_ACCEPTANCE_CRITERIA.md](17_ACCEPTANCE_CRITERIA.md). Numeric gates throughout this pack are proposed design targets, never observed results. Inventory scale does not establish active-agent scale. Initial no-helmet rules depend on camera visibility, taxonomy and labels; unseen/occluded heads are unknown. This advisory prototype has no certified life-safety guarantee.
