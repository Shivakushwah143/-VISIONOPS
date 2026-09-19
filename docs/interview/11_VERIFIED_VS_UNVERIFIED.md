# 11 — Verified vs unverified

One page, no hedging in either direction. A line is `VERIFIED` only when a command in this
repository produced the evidence, on the hardware named. Everything else says what is
missing and why.

## GPU / model-runtime layer

| Claim | Status | Evidence |
| --- | --- | --- |
| TensorRT inference on a real NVIDIA GPU | **VERIFIED** | `docs/evidence/tensorrt/final/fp32/` — Tesla T4, TensorRT 11.3.0.99 |
| TensorRT FP32 reproduces the reference | **VERIFIED** | 24/24 matched, IoU 1.0, 0 missing, 0 extra |
| TensorRT FP32 benchmark | **VERIFIED** | p50 4.658 ms, p95 6.479 ms, mean 4.834 ms, 206.87 model-only FPS |
| FP32 -> ModelOpt mixed-FP16 ONNX conversion preserves behaviour | **VERIFIED** | `final/true_fp16/fp32_onnx_vs_mixed_fp16_onnx.json` — min IoU 0.9955, 24/24 |
| True mixed-FP16 TensorRT engine | **VERIFIED** | `final/true_fp16/parity_fp16.json` — engine declares `DataType.HALF`, min IoU 0.9922 |
| True mixed-FP16 benchmark | **VERIFIED** | p50 9.016 ms, p95 9.434 ms, mean 9.1 ms, 109.89 model-only FPS |
| Earlier "FP16" result (~4.78 ms, ~209 FPS) | **SUPERSEDED** | engine input was `DataType.FLOAT`; `final/superseded/README.md` |
| Canonical contract protects the PPE interpretation | **VERIFIED** | `final/fp32/contract-semantics.json` — 908 of 1827 boxes would change under a global argmax |
| ONNX Runtime CPU reference path | **VERIFIED** | `scripts.verify_model_contract`, `scripts.verify_edge_platform` |
| ONNX Runtime CUDA baseline | **NOT VERIFIED** | provider listed but not operational in the run; `final/fp32/onnx_cuda_baseline.json` |
| TensorRT INT8 | **NOT ATTEMPTED** | requires a representative calibration set and a parity pass; none exists |
| Model quality (mAP, no_helmet precision/recall) | **BLOCKED BY DATA** | no labeled PPE dataset in the repository |

## Hardware / deployment layer

| Claim | Status | Why |
| --- | --- | --- |
| TensorRT on this development machine | **BLOCKED BY HARDWARE** | no NVIDIA device, driver, CUDA or TensorRT; `docs/evidence/tensorrt/blocked.json` |
| Physical NVIDIA Jetson qualification | **PREPARED — NOT RUN** | `var/bundle/visionops-jetson-validation.zip` (222 entries, audited: 221/221 hashes, 0 engines, 0 secret findings) + `run_jetson_validation.sh`. The gate itself was executed here and exits `3` `BLOCKED_NOT_PHYSICAL_JETSON`; preparation moves no verification line |
| Physical NVIDIA Jetson | **NOT VERIFIED** | no Jetson hardware available; a T4 is not a Jetson |
| JetPack on physical hardware | **NOT VERIFIED** | same |
| Jetson thermal / power / NVDEC behaviour | **NOT VERIFIED** | same; a desktop GPU has no equivalent measurement |
| ARM64 runtime execution | **NOT VERIFIED** | ARM64 container target declared, never executed on ARM64 silicon |
| DeepStream runtime | **NOT VERIFIED** | DeepStream SDK absent from the run; configuration is generated from the canonical contract and statically checked only |
| TensorRT engine portability to other GPUs | **NOT ASSERTED** | engines are hardware/driver/TensorRT-version specific; they are deleted after measuring, never committed, and the Jetson bundle's builder fails its audit if one appears |
| 10K physical Jetson fleet | **NOT VERIFIED** | `docs/10K_FLEET_SCALING_REPORT.md` is a logical control-plane simulation, not a hardware deployment |

## Application layer

| Claim | Status | Evidence |
| --- | --- | --- |
| Detector -> ByteTrack -> temporal rule -> durable event | **VERIFIED** (CPU, synthetic video) | `scripts.verify_components`, `scripts.verify_edge_platform` |
| PPE sustained temporal rule | **VERIFIED** | same |
| Zone-dwell and low-motion deterministic analyzers | **VERIFIED** | `scripts.verify_edge_platform` |
| RTSP reconnect with bounded backoff | **VERIFIED** against an unreachable endpoint | `scripts.verify_edge_platform` (`rtsp_resilience`) |
| RTSP against a real server (MediaMTX) | **IMPLEMENTED — NOT VERIFIED** | no RTSP server was available here; environment is committed in `infrastructure/compose.rtsp.yaml` |
| GStreamer video backend | **IMPLEMENTED — NOT VERIFIED** | GStreamer runtime not present on this host |
| WebSocket event delivery to the browser | **IMPLEMENTED — NOT VERIFIED** | control plane (FastAPI/PostgreSQL) not runnable in this environment |
| Signing, artifact verification, bounded download, safe extraction | **VERIFIED** (component level) | `scripts/verify_security` |
| Central rollback / local watchdog rollback | **VERIFIED** (component level) | `scripts/verify_continuation` |
| OpenAPI / PostgreSQL schema contracts in `shared/contracts/` | **STALE** | regenerated only when the control plane runs; see `docs/CURRENT_VERIFIED_STATE.md` |

## How to read this at an interview

```text
TensorRT VERIFIED ON REAL NVIDIA GPU.
PHYSICAL JETSON STILL NOT VERIFIED.
```

Both halves are load-bearing. The first is measured, with a reproducible command and
machine-readable evidence. The second is the honest boundary of what has been run, and it
is stated as a boundary rather than smoothed over.
