# Hansung PPE — end-to-end verification manifest

Compact status of the qualified-model → release → device → inference → event →
persistence → observability slice. Machine-readable evidence is in the JSON files
listed at the bottom. Nothing here is a detector-quality claim: no labeled PPE
benchmark exists in this environment.

## Status

| Stage | Status | Evidence |
| --- | --- | --- |
| MODEL | ✅ qualified | `var/model/hansung-p3.onnx` sha256 `b239aa7e…42` , 12 245 976 bytes, opset 17, checker OK |
| ONNX | ✅ parity verified | PT↔ONNX 24/24 on frames 0/25/50/100/150/200; IoU min 0.981, mean 0.991; max Δconf 0.0227 |
| DETECTOR | ✅ verified | `edge.pipeline.Detector` loads by hash and remaps source → canonical `{5:0, 0:1, 2:2}` |
| BYTETRACK | ✅ verified | 3 unique tracks over 206 frames; tracks 1 and 2 span frames 0–205 |
| PPE ASSOCIATION | ✅ verified | 412 person-frames resolved `helmet`, 1 `unknown`; head boxes associated only inside the upper-35 % head region |
| REAL VIDEO SAFETY RULE | ✅ verified negative case | **0 genuine violations** — the single `no_helmet` observation (conf 0.419) does not reach 5 frames / 2 s. Thresholds were **not** lowered |
| RELEASE / DESIRED STATE | ✅ verified | release `3741c6b3-b9e6-4ad2-910d-c86e601a8dc1`, `ppe-hansung-v1`, approved, signed `qual` |
| DEVICE RECONCILIATION | ✅ verified | desired gen 2 == actual gen 2; agent staged artifact sha256 == released sha256 |
| ANNOTATED VIDEO | ✅ | `var/evidence/hansung-ppe-tracked.mp4` + 5 PNGs |
| OUTBOX | ✅ verified | enqueued → depth 1 → real `Agent.deliver()` → depth 0, deadletter 0 |
| BACKEND INGEST | ✅ verified | `POST /api/v1/device-events` → `accepted` |
| POSTGRES | ✅ verified | `safety_events` row present; duplicate resend → `duplicate`, exactly one row |
| SAFETY EVENTS API | ✅ verified | `GET /api/v1/safety-events/{id}` returns the persisted event |
| RETRY/RECOVERY | ✅ verified | backend unreachable (`localhost:9`) → outbox retained depth 1 → restored → depth 0 |
| OBSERVABILITY | ⚠️ PARTIAL | Prometheus scraping `up`, 16 `visionops_*` families; device version/health/events exposed. Per-camera CV latency/FPS/outbox-depth are **not** populated with real values because the device under test is `simulated` |
| WINDOWS CLEANUP | ✅ fixed | `scripts.verify_components` exits 0; no `WinError 32` |
| TENSORRT / NVIDIA | 🚫 BLOCKED | no NVIDIA device, drivers, CUDA or TensorRT in this environment |

## Model and ONNX contract

- Artifact: `var/model/hansung-p3.onnx`, sha256 `b239aa7e881fc590633944cf37df46ed477f3a819d09ec7a7bdb19d052ca2422`
- Source checkpoint: `var/tools/hansung-best.pt` (Hansung-Cho/yolov8-ppe-detection, MIT), sha256 `2419700bbe3b8d38f9000655d9cf952a4bc93ef6c143baf8b49a0abde5d0760f`
- Input `images [1,3,640,640]` float32 RGB, letterbox pad 114; output `output0 [1,14,8400]` = 4 box (cx,cy,w,h) + **10** class scores, no objectness, NMS-free
- Source indices: **Person = 5, Hardhat = 0, NO-Hardhat = 2** → canonical person 0 / helmet 1 / no_helmet 2
- Canonicalization: **adapter filtering** (`Detector.infer` scores only the mapped source columns; equivalent to a 3-class subset export). No ONNX graph surgery. Contract in `var/model/hansung-p3.json`
- Parity: 24/24 matched, IoU min 0.981 / mean 0.991, max |Δconfidence| 0.0227 at conf 0.35, IoU 0.50

## Release, device and provenance

- Release `3741c6b3-b9e6-4ad2-910d-c86e601a8dc1`, version label `ppe-hansung-v1`, status `approved`, hardware profile `cpu_onnx_x86_64`
- Model artifact id `661d5fb3-d1e2-4862-aa1f-549b9be262a3`, sha256 matches the qualified artifact byte for byte (re-verified after upload)
- Device `7480bad4-41e2-4ad7-9bb4-0684ee0dc673` (mode `simulated`), desired generation 2 == actual generation 2, `agent_state=healthy`
- The agent's staged slot model sha256 == the released model sha256 → the running artifact is the qualified one, not a placeholder
- MLflow run and registry hold the model, contract and qualification evidence

### Honest scope of the release

The **model artifact is real**; the **evaluation record is `evidence_mode=simulated`**. No labeled temporal PPE benchmark exists here, so the server-owned real
promotion gate (training/quality.py `gate` plus the full benchmark protocol:
100 warmup frames, 3 timed repetitions, 1000 frames each, parity reference) cannot
be satisfied honestly. A real-mode release is therefore **BLOCKED on missing
labeled evaluation data**, not on missing software. Consequently the release may
only be reconciled by a `simulated` device, which is why no real worker is started
by the agent on this device.

## Real-video result (released artifact)

206 frames of `var/media/ppe-2.mp4` through Detector → ByteTrack → PPE association →
SafetyRule, every frame inferred:

- canonical detections: person 414 (max 0.921), helmet 412 (max 0.916), **no_helmet 1** (max 0.419)
- tracks: 3 (ids 1 and 2 span the full clip; id 3 is a single-frame `unknown`)
- safety events: **0** — correct: one bare-head observation cannot satisfy ≥5 frames spanning ≥2 s
- annotated video: `var/evidence/hansung-ppe-tracked.mp4`; frames `var/evidence/hansung-ppe-frame-{000,050,100,150,200}.png`

## Performance (CPU, honest)

| Metric | Value |
| --- | --- |
| Inference p50 | 118.1 ms |
| Inference p95 | 320.2 ms |
| Effective inference rate (every frame) | 5.53 FPS |
| Production `Pipeline` run | 36–37 frames inferred, 169–170 throttled/skipped by design (`stale_frame_ms=500`, `inference_fps=5`), p50 125–134 ms, p95 269–283 ms |
| Runtime | ONNX Runtime 1.30.0 CPU, OpenCV 5.0.0, Python 3.12.13, Intel i3-1005G1 |

This is a **functional** CPU verification only. It is not NVIDIA/TensorRT
production performance and no 30 FPS claim is made. The production scheduler
intentionally infers ~5 FPS and drops the rest.

## Transport verification (label: integration_verification)

The real clip yields zero violations, so transport is proven separately with one
deterministic event that satisfies the real `SafetyInput` schema. It is explicitly
labelled and is **not** model output:

- camera `integration-verification (synthetic transport check; not model output)`
- `track_id = integration-verification`, `stream_session_id = 00000000-0000-4000-8000-0000000ff1ce`
- `evidence_mode = simulated` (server-set from the device mode)
- event `ec16ddb9-b479-4493-9afa-1884f75f4bb6`: outbox depth 1 → real agent `deliver()` → depth 0, deadletter 0 → `GET /api/v1/safety-events/{id}` returns it (`review_status=new`, `supporting_frames=5`)
- duplicate resend → `{"status":"duplicate"}`; exactly one PostgreSQL row
- retry: backend pointed at an unreachable endpoint (`http://localhost:9`) → outbox retained depth 1 → backend restored → depth 0 and event persisted

## Observability

- Prometheus target `visionops` → `http://backend:8000/metrics` is **up**; Grafana 12.2.1 health `ok`; dashboard **"VisionOps application metrics"** is provisioned and loads
- 16 `visionops_*` families exposed, including `visionops_device_version_info` (`application_version=ppe-hansung-v1`), `visionops_device_health` (`healthy`), `visionops_camera_inference_fps`, `visionops_camera_inference_latency_ms`, `visionops_events_ingested_total=3`, `visionops_database_observations_available=1`
- **Limitation:** because the device under test is `simulated`, the per-camera latency/FPS values Prometheus reports come from the simulator's synthetic observation window (≈0.001 ms), not from the measured 118 ms CPU inference. `visionops_camera_outbox_depth` is only emitted by real-mode worker heartbeats and is therefore empty here. Real per-camera CV telemetry requires a `real`-mode release, which is blocked on labeled evaluation data.

## Hardware limitation

- **CPU ONNX Runtime: VERIFIED.**
- **TensorRT / NVIDIA GPU runtime: BLOCKED BY LOCAL HARDWARE.** No NVIDIA device, driver, CUDA or TensorRT is present; no engine file exists and no GPU number is claimed.

## Evidence files

- `docs/evidence/hansung-onnx-qualification.json` — artifact, contract, parity, timings
- `var/hansung-release.json` — approved release + manifest
- `var/hansung-device.json` — device reconciliation and staged-artifact hash
- `var/evidence/hansung-ppe-evidence.json` — real-video per-class counts, tracks, timings, zero events
- `var/hansung-transport.json` — outbox → backend → API and retry/recovery
- `var/evidence/hansung-ppe-tracked.mp4`, `var/evidence/hansung-ppe-frame-*.png`

## Reproduce

```bash
python -u var/tools/hansung_diag.py --stages 1,2,3,4,5 --onnx var/model/hansung-p3.onnx
python -m scripts.verify_components --output var/engineering/component-runtime.check.json
python -m scripts.release_hansung
python -m scripts.verify_hansung_device
python -m scripts.hansung_evidence_video
python -m scripts.verify_hansung_transport --retry
```
