# Assumptions, decisions and consistency audit

Specification version: 1.0. Prepared 2026-09-12. Status: specification only; no application implementation or model/hardware evaluation was performed in this task.

## Inputs and precedence

Read `Pasted markdown(5).md` (Industrial VisionOps specification brief) and `END-TO-END-VERIFIED-PROJECT-ZIP-—-MASTER-BUILD-DIRECTIVE.txt` (later implementation/release discipline). Latest user instruction requires 22 real Markdown files, consistency review and ZIP, and explicitly forbids implementation now. Therefore the master build directive informs document 21 and future verification; it does not trigger building software in this turn. Its generic LLM/RAG examples do not add those technologies to VisionOps.

Within this pack, latest explicit instruction → locked technical decisions → FRD → detailed journeys → domain/API → acceptance → PRD → stories → visual design → judgment. Material change requires updating all dependent documents rather than choosing a conflicting value silently.

## Decision register

| ID | Binding choice | Rationale / impact |
| --- | --- | --- |
| D01 | Single organization, four roles | Sufficient explicit RBAC without unnecessary multi-tenancy |
| D02 | One real sample-video stream CPU default; 1–4 as capacity permits | Runnable without assuming a Jetson/GPU |
| D03 | 800 sites/8,000 seeded device records; 10 active simulated agents default, max 50 P0 | Distinguishes sites, inventory and active control-plane workload |
| D04 | CPU ONNX/FFmpeg/OpenCV + ByteTrack; NVIDIA DeepStream/TensorRT FP16 + NvDCF | Reuse mature infrastructure; keep hardware qualification separate |
| D05 | YOLO11n custom person/helmet/no_helmet taxonomy | Small explicit detector; data/weights acquisition and licensing remain external prerequisites |
| D06 | Visible no_helmet evidence, not absent helmet; ≥5 frames/≥2 s, ≤1 s gaps, 30 s cooldown | Prevent obvious false alert semantics; thresholds still require evaluation |
| D07 | Track identity scoped to camera+stream_session | No fictitious identity persistence across reconnects/cameras |
| D08 | PostgreSQL authoritative, SQLite edge outbox, outbound HTTPS polling | Durable state without broker/cluster complexity |
| D09 | 15 s jittered heartbeat/poll, 60 s offline, UI event/fleet poll 5 s | Concrete liveness/update expectations |
| D10 | Separate immutable model/runtime/config Release | Reproducible worker updates; agent/OS/driver upgrades outside P0 |
| D11 | SHA-256 plus Ed25519 P0; bearer/TLS P0, mTLS P1 | Integrity is not authentication; simple usable trust boundary |
| D12 | Desired generation monotonic, rollback creates newer generation | Offline devices cannot resurrect obsolete rollout intent |
| D13 | ≥10 eligible devices per campaign, 10/25/100 cumulative rings, explicit advance | Strong, reproducible simulator demo; single-device commissioning separate |
| D14 | 5-minute baseline and observation, ≥100 observations/device; 10-minute convergence | Unknown/insufficient health cannot pass; full targets specified in FRD |
| D15 | Local candidate health 60 s, central ring health 5 min | Local recovery and fleet rollout gates have different purposes |
| D16 | Missing telemetry >30 s blocks; >60 s during active ring pauses | Observability failure cannot produce a healthy rollout |
| D17 | CPU/GPU and real/simulated releases/campaigns separate | Simulation evidence cannot authorize a real video device |
| D18 | Training CLI plus real DVC/MLflow; human drift/retraining review | No hidden training scheduler or autonomous promotion |
| D19 | Prometheus/Grafana/JSON/OTel console P0; Loki/Alloy/Tempo P1 | Useful observability with manageable local footprint |
| D20 | Local annotated preview; central event snapshots only | Avoid unrequested central streaming/continuous video storage |
| D21 | Filesystem immutable artifacts/evidence P0; remote object store/CDN P1 | Single-node development is sufficient; no HA claims |
| D22 | Complete dependency choices now; exact installed patch/digest qualification during build | No fabricated compatibility lock; selected products cannot drift casually |
| D23 | CPU performance/accuracy numbers are proposed gates, not results | Measured failure stays failure; explicit changes require reevaluation |
| D24 | Runtime journeys, failure/load experiments and CV evals; no conventional application test framework | Follows attached master build directive |
| D25 | Camera source edits derive per-device config snapshots; reject edits in active campaigns | Preserve camera assignment while preventing rollback/config races |
| D26 | Bounded event outbox with explicit loss at cap/TTL | Honest resilience under finite disk, not unlimited lossless buffering |
| D27 | Full 5-minute rollout window retained during 7-minute demo through disclosed prewarm | No accelerated fake health evidence |

## Material limitations and verification boundaries

External PPE dataset/model rights, usable weights, exact NVIDIA compatibility, maintained YOLO parser suitability and target hardware capacity remain to be verified before implementation claims. CPU quality/performance gates can fail on available data/hardware. No software, dataset, GPU, 8,000-agent concurrent load, business outcome or safety certification has been verified here. Official component documentation was inspected for research in document 02; those references do not establish the application's correctness.

## Cross-document consistency audit

The final pass must read all actual files after generation, check the following mappings, and repair the actual files before packaging. Audit records below report specification review, not runtime verification.

| Cross-document relation | Checked resolution |
| --- | --- |
| PRD ↔ FRD | R01–R13 cover all P0 features; optional classes, mTLS, INT8, Loki/Tempo remain P1 |
| FRD ↔ architecture ↔ stack | CPU ONNX fallback and qualified NVIDIA adapter, PostgreSQL controller, SQLite outbox, TLS/signatures and profile separation consistent |
| Journeys ↔ API | A–I map to named CRUD/actions, device channel, evidence upload and human retraining APIs; no arbitrary desired-state write endpoint |
| API ↔ domain | snake_case specific UUID keys, field schemas, four-role actions, history and generation semantics shared |
| Edge ↔ fleet | Receipt-time liveness, boot/sequence handling, desired polling, atomic applied state and offline replay consistent |
| Deployment ↔ failure matrix | Pauses stop new assignments; rollback uses newer generation and incomplete offline-target status; local rollback does not invent generations |
| Observability ↔ failures | Camera/queue/worker/network/artifact/DB/quality failures have signals; missing and simulated values remain explicit |
| Acceptance ↔ scope | Criteria measure actual pathways and evidence; no physical fleet/GPU performance inferred from code or records |
| Build phases ↔ architecture | Minimal UI/security/metrics begin early; later phases integrate/polish; simulator exists before campaign verification |
| Master prompt ↔ files | All 22 exact filenames referenced; instructions distinguish current spec from later implementation |

## Audit fixes and packaging evidence

Final review notes and actual file/ZIP integrity results are appended below after re-reading. The archive must contain exactly this folder with 22 nonempty Markdown files and no implementation source.

### Final review completed

Re-read all 22 generated Markdown files, checked the mappings above, and corrected the actual files before packaging. Corrections include:

- Added a signed warmup-fixture path for enrollment before cameras exist, plus a guarded first-commission retry; neither creates fake safety events or bypasses campaign policy after commissioning.
- Made desired-state ETags reflect download permits as well as generation, preventing waiting devices from remaining stuck on 304 responses.
- Froze source/config ownership and signing boundaries; camera edits cannot alter approved inference defaults or race an active campaign.
- Added durable assignment/boot/idempotency records, target download reservations, explicit failed-generation fields and legal campaign transitions.
- Reconciled runtime update interruption, local 60-second recovery, central 5-minute observation, offline rollback completion and demo timing.
- Added training completion API, drift histogram evidence, hard-example annotations/provenance and server-controlled evaluation gates.
- Removed an inconsistent log identifier; aligned metric payloads and exact rollout percentile calculation; distinguished real metrics from warmup/simulation.
- Corrected PostgreSQL locking/index descriptions and training-to-artifact data flow.

Specification audit status: PASS for the reviewed cross-document relationships. This status does not claim application runtime, AI evaluation, or GPU execution.

Packaging validation: exact requested 22 filenames, all nonempty UTF-8 Markdown, relative document links resolved, balanced Mermaid fences, master prompt references every filename, and archive payloads checked byte-for-byte against the final files. ZIP contains a single visionops-specification/ root and no implementation code. Validation is performed by the packaging operation; this audit record is included in the archive.
