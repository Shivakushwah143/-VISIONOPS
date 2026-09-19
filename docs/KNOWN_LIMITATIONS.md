# Known limitations and release decision

> **Continuation update.** The authoritative, defect-by-defect status now lives in
> [CURRENT_VERIFIED_STATE.md](CURRENT_VERIFIED_STATE.md). Sections below that predate it
> are kept for history. Newly verified here: one canonical model contract shared by CPU
> and NVIDIA paths (with an independent decoder cross-check), the runtime abstraction with
> fail-closed capability detection, video backends and RTSP reconnect, deterministic
> temporal analyzers, truthful hardware telemetry, the hardware-profile matrix, and
> PyTorch→ONNX graph parity (24/24 operating, 159/159 stress, min IoU 1.0). Newly added
> but **not runtime verified**: WebSocket events, GStreamer/MediaMTX, DeepStream runtime,
> campaign reason codes, release-bundle identity, the 10K logical fleet driver and the DVC
> stages. **NEWLY VERIFIED ON REAL NVIDIA HARDWARE:** TensorRT FP32 and a true ModelOpt
> mixed-FP16 engine on a Tesla T4, both parity-checked through the canonical decoder
> ([evidence](evidence/tensorrt/final/README.md)). This host still has no NVIDIA runtime.
> Still **BLOCKED**: physical Jetson, ARM64 execution, model quality (no labeled dataset),
> ONNX Runtime CUDA, and any GPU memory/utilization or fleet measurement.

**Release decision: NOT IMPLEMENTED — the complete requested P0 platform remains incomplete.** This is an audited source handoff with verified components. It is not a successfully deployed or end-to-end verified release. Environment blockers and unfinished software are listed separately; neither is hidden behind a successful build.

## Environment blockers

- **RESOLVED (partially):** PostgreSQL runtime. Docker Compose is now available and the stack runs (PostgreSQL 16.11, backend, MLflow, frontend, Prometheus, Grafana). Migrations are applied, the 31-table schema is live, and authenticated role, enrollment, device desired/actual state, event ingest and API read-back journeys were exercised end to end. Central storage was never replaced with SQLite. Campaign transactions and central rollback remain unexercised.
- **PARTIALLY RESOLVED:** Real PPE artifact. The previously pinned Hexmon artifact is still unavailable, but a different real PPE detector was obtained, qualified and exercised: Hansung-Cho/yolov8-ppe-detection (YOLOv8n, MIT) → `var/model/hansung-p3.onnx`. Real inference, taxonomy geometry, PT↔ONNX parity and CPU performance are now verified. **Still BLOCKED:** model-quality evaluation — no labeled PPE dataset, ground truth, AP/mAP or temporal event-quality measurement exists, so the server-owned real promotion gate cannot pass and no `real`-mode release may be approved.
- **PARTIALLY RESOLVED:** NVIDIA hardware/runtime. The TensorRT runtime is now verified on an external **Tesla T4** (TensorRT 11.3.0.99): FP32 and a true ModelOpt mixed-FP16 engine, both parity-checked against a reference runtime through the canonical decoder, with no engine file committed (engines are hardware-specific and were deleted after measuring). No GPU memory, utilization, power or TOPS figure was measured, so none is reported. **Still BLOCKED here:** this development host has no GPU node, no `nvidia-smi`, no CUDA and no TensorRT, and physical Jetson / JetPack / ARM64 execution / DeepStream runtime remain unverified.
- **RESOLVED (source defect):** the DeepStream parser and `nvinfer.txt` asserted a 3-class / 7-channel tensor while the qualified artifact is 10-class / 14-channel. Both are now generated from `shared/model_contract.py` and checked by `scripts/verify_model_contract`.
- **RESOLVED (source defect):** the edge agent accepted only `cpu_onnx_x86_64` on x86_64/AMD64. `shared/hardware_profiles.py` now provides an explicit matrix with reason codes and an explicitly marked simulated-target path.
- **RESOLVED (dead code):** `shared/model_contract.py` was imported by nothing and the CPU adapter had its own decoder. The CPU adapter now decodes exclusively through the shared module.
- **PARTIALLY RESOLVED:** Full Compose service qualification. PostgreSQL, MLflow, frontend, Prometheus and Grafana are running; the Prometheus target is `up` and the Grafana dashboard loads. Remaining: image tags are still not digest-frozen, the controller service was not exercised, and native verification used Python 3.12.13/3.12.14 on Windows while the container stack retains its own Python/Node versions.

## Incomplete software, independent of those blockers

- **IMPLEMENTED — NOT RUNTIME VERIFIED:** Real-evidence MLflow import and recomputation of detector/temporal metrics, confusion matrix, AP and percentile latency now exist. Known-answer arithmetic was exercised. No real model/evidence was obtained; full raw parity qualification and automated temporal-video evidence collection remain incomplete. Promotion protocol metadata is not proof of execution.
- **IMPLEMENTED — NOT RUNTIME VERIFIED:** A standalone DeepStream launcher, parser build command and durable-event bridge now exist. Full agent GPU-profile selection, RTSP recovery and hardware/SDK qualification remain incomplete; no NVIDIA path was run.
- **VERIFIED:** The post-commit watchdog component handled three real child-process crashes, restored the previous worker, preserved applied/rejected generation 2 and observed the full 60-second recovery window. This used engineering workers and SQLite. The complete signed CV-agent/control-plane recovery journey remains unverified.
- **NOT IMPLEMENTED:** Complete snapshot capture/upload scheduling from the worker, bounded snapshot spool and local HTTP preview. CPU preview currently writes an optional local JPEG; there is no preview server. The CPU worker emits SafetyEvents; periodic DetectionEvent sampling is not wired into its production loop.
- **NOT IMPLEMENTED:** Complete DVC training/export/evaluation DAG, accepted-example dataset export and retraining execution handoff. Dataset validation, training/MLflow and detector evaluation CLIs exist, plus review/retraining API records. No complete lifecycle run was verified.
- **NOT IMPLEMENTED:** All required UI workflows. Inventory, source toggles, events, enrollment, campaign actions, model/metric/drift views and drift review controls exist. Artifact upload/signing/model registration and hard-example labeling/export require CLI/API work; there is no complete UI for them. Browser-level interaction/accessibility validation was not performed.
- **NOT IMPLEMENTED:** Complete frozen schema/filter validation, including every nested metric field, time-range restriction and filter enum. Some endpoints use dictionaries. Complete keyset pagination for campaign target/event views is missing; target views can exceed 200 rows.
- **NOT IMPLEMENTED:** Full retention cleanup, bounded rate limiting, comprehensive security audit, source credential injection, download-scope narrowing to only desired/actual/last-good references, per-device compatibility-range qualification and real TLS deployment. These are material production gates, not optional polish.
- **VERIFIED:** The shared allocator component bounded rollback leases to five, transferred a lease after convergence and reclaimed an offline lease. Controller integration is IMPLEMENTED — NOT RUNTIME VERIFIED because PostgreSQL transaction/concurrency execution remains blocked.
- **IMPLEMENTED — NOT RUNTIME VERIFIED:** The persisted-observation exporter and Grafana panels now include input/inference FPS, latency, dropped frames, reconnects, queue depth, device health, version information and campaign state. Database-backed scrapes and real camera dashboards remain unverified. Unavailable GPU values are not emitted.

## Implemented but not runtime verified

**IMPLEMENTED — NOT RUNTIME VERIFIED:** PostgreSQL API persistence; enrollment/credential rotation; session/CSRF/roles; model/artifact/release APIs; agent enrollment, signed staging and HTTP replay; desired/actual projection; campaign controller; 800-site/8,000-record inventory seeder; 10–50 active simulator runner; canary progression and central rollback; drift/hard-example/retraining APIs; Compose services and migrations.

The simulator generates explicitly synthetic worker observations. It does not measure model inference or represent physical devices. Its fixed 5 FPS observation is a simulation input, never a production measurement. Signed artifacts and control-plane requests use real code, but the complete simulator was not connected to a running database here.

## Verified boundary

**VERIFIED:** See `docs/evidence/`: synthetic constant-output ONNX plumbing through video/ByteTrack/rule/outbox; durable outbox reopen and duplicate acknowledgement; model hash rejection; Ed25519 success/tamper rejection; archive traversal/symlink rejection; API liveness/metrics/OpenAPI; frontend build and HTTP startup; Python imports/dependency compatibility; PostgreSQL DDL compilation and route presence inspection.

None of these component results passes the complete AC01–AC23 acceptance criteria. Original specifications remain requirements, not claims about delivered behavior.

## Hansung PPE qualification limitations

- **BLOCKED — evaluation data:** the approved release carries `evidence_mode=simulated`. The model artifact is real and hash-verified, but no labeled PPE benchmark exists, so mAP, no_helmet precision/recall and temporal event quality are unmeasured. The real promotion gate (100 warmup frames, 3×1000-frame repetitions, parity reference) was not bypassed.
- **IMPLEMENTED — NOT RUNTIME VERIFIED:** the reconciled device is `simulated`, so the edge agent stages and hash-verifies the real artifact but starts no worker process. A `real`-mode device journey needs a `real`-mode release, which is blocked on the point above.
- **PARTIAL — observability:** Prometheus scrapes the backend and exposes 16 `visionops_*` families including device health, model/artifact version and ingest counts. Because the device is `simulated`, per-camera CV latency/FPS values are simulator samples (≈0.001 ms), not the measured 118 ms CPU inference, and `visionops_camera_outbox_depth` is only emitted by real-mode workers.
- **HONEST PERFORMANCE:** CPU ONNX Runtime measured p50 118 ms / p95 320 ms (≈5.5 FPS) with every frame inferred; the production scheduler intentionally infers ~5 FPS and drops the rest (36–37 inferred of 206 frames). No 30 FPS claim is made.
- **BLOCKED — NVIDIA:** no NVIDIA device, driver, CUDA or TensorRT exists here; no engine file and no GPU number are claimed.
- Real-video safety result is a verified **negative case**: 0 violations, because the single `no_helmet` observation (conf 0.419) cannot satisfy ≥5 frames spanning ≥2 s. Rule thresholds were not lowered to manufacture an alert.
