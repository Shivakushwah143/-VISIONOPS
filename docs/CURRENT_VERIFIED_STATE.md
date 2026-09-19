# Current verified state

Reconciled from the actual repository and the actual development host. Every status
word below is one of:

```text
VERIFIED                     executed here, with a re-runnable command and evidence file
IMPLEMENTED — NOT VERIFIED    source and configuration exist and are statically checked; not executed here
BLOCKED BY HARDWARE          requires an NVIDIA device/runtime or hardware that does not exist here
NOT IMPLEMENTED              absent, or present only as an unfinished path
```

Status is never inferred from source inspection. Where a runtime artifact cannot be
packaged, the exact reproduction command is given instead.

---

## 1. Development host (measured)

| Fact | Value |
| --- | --- |
| OS / machine | Windows 11, `AMD64`, Python 3.12.13 (venv at `.venv`) |
| Present | `onnxruntime 1.30.0` (CPU provider only), `opencv 5.0.0`, `numpy 2.5.3`, `supervision 0.30.2`, `torch 2.14.0+cpu`, `onnx 1.22.0`, `ultralytics 8.4.152`, `psutil`, `av` |
| Absent from the venv | `fastapi`, `sqlalchemy`, `mlflow`, `dvc`, `alembic`, `pytest` |
| Absent from the host | `ffmpeg`, `gst-launch-1.0`/PyGObject, `nvidia-smi`, `nvcc`, NVIDIA device nodes, a running PostgreSQL, a running RTSP server |
| Docker CLI | available (29.2.1) — **no container was started, no image pulled, no digest verified** |
| Network | package installation timed out; no dataset or model download was possible |

Consequences that shape everything below: the FastAPI/PostgreSQL control plane
**cannot be executed here**, and neither can HTTP/WebSocket/browser or MLflow
journeys. The edge/ML half can be executed and is where the new runtime evidence
comes from.

## 2. Repository truth (Phase 1 reconciliation)

Findings from reading the whole repository, and what was done about each:

| Finding | Evidence | Resolution |
| --- | --- | --- |
| `shared/model_contract.py` existed but was **imported by nothing** — a dead module. The CPU adapter in `edge/pipeline.py` had its own copy of letterbox, decode, taxonomy validation and NMS. | `git ls-files shared/` listed only `contracts/*`; no importer of `model_contract` existed | CPU adapter now decodes exclusively through the shared module (`edge/pipeline.py`) |
| The DeepStream parser asserted a **3-class / 7-channel** tensor (`inferDims.d[0]!=7`, `numClassesConfigured!=3`) while the qualified artifact is **10-class / 14-channel** | `edge/pipeline/deepstream/parser.cpp`, `nvinfer.txt` (`num-detected-classes=3`) | Parser, `nvinfer.txt` and the bridge are now **generated** from the contract (`scripts/gen_deepstream_contract.py`) |
| `ModelContract.source_count` was `max(mapped index)+1` = **6**, not the published head width 10 — the exact mechanism behind the divergence above | contract `cpp_constants()` printed `10` only after the fix | `source_count` is now explicit per profile and validated against the model's declared metadata |
| The edge agent accepted exactly one literal profile and one machine string (`cpu_onnx_x86_64` + `x86_64`/`AMD64`), so ARM64/Jetson targets were unreachable | `edge/agent.py` `prepare()` | Replaced by `shared/hardware_profiles.py` with a declared matrix and reason codes |
| `edge/pipeline.py` hardcoded `sv.ByteTrack(frame_rate=5)` and a bare `SafetyRule`, leaving no place for any behaviour rule beyond PPE | `edge/pipeline.py` | Tracking + `edge/temporal.py` analyzers, configured through the signed config |
| RTSP metrics the spec asks for were only partly present: reconnects existed, `rtsp_connected`, `decode_fps`, `stream_age` and `dropped_frames_total` did not | `edge/pipeline.py` `Source` | `edge/video.py` VideoSource contract reports all of them |
| GPU reporting was `'gpu_utilization_ratio': None` in one hand-written payload and nowhere else; no provider abstraction existed | `edge/agent.py` | `edge/hardware_telemetry.py` providers; absence is an explicit `gpu_metrics_available: false` |
| The release manifest referenced model/runtime/config/evaluation ids but **not** the mapping version, architecture, runtime version or dataset identity that Phase 10 requires | `backend/app/lifecycle.py::create_release` | `release_identity()` adds them; the mapping version is asserted by the worker before it will load a candidate |
| `docs/` status claims were written before this work and several are now stale (DeepStream "three classes", no GStreamer/RTSP/temporal/telemetry paths) | this file supersedes them | Each affected document now carries an update pointer to this file |

Nothing was deleted to make an audit pass. `scripts/audit_release.py` still requires
the 22 specification files to be present and hashed.

## 3. VERIFIED in this environment

New evidence files are under `docs/evidence/`. Reproduce with the listed commands
from the repository root using `.venv`.

### 3.1 Canonical model contract shared by CPU and NVIDIA paths

```bash
.venv/bin/python -m scripts.verify_model_contract     # docs/evidence/model-contract-runtime.json
```

* Generated DeepStream artifacts are not stale: `nvinfer.txt` declares
  `num-detected-classes=10` (the published head), `raw_channels=14`, threshold 0.35.
* `parser.cpp` contains no hardcoded `!=7` / `!=3` / `numClassesConfigured!=3` pattern
  and uses the generated macros.
* The shared contract reproduces the mapping recorded for the qualified artifact
  (`{5:0, 0:1, 2:2}`, 14 channels, 10 source classes).
* The profile is **auto-detected from the model's own metadata**; the legacy
  explicit-mapping callers produce byte-identical boxes.
* An **independent numpy decoder** (written inside the verification script, importing
  no shared code) agrees with `Detector.infer` on real frames: 12 boxes over 3 frames,
  max box delta `0.0`, max confidence delta `0.0`.

### 3.2 Edge platform runtime

```bash
.venv/bin/python -m scripts.verify_edge_platform      # docs/evidence/edge-platform-runtime.json
```

* **Runtimes:** `ONNX_CPU` available and preferred; `ONNX_CUDA` unavailable
  (`gpu_not_detected`); `TENSORRT` unavailable (`tensorrt_module_not_installed`);
  `DEEPSTREAM` unavailable; `gpu_metrics_available: false`. Unavailable runtimes
  **refuse to load** instead of silently falling back.
* **Video:** OpenCV/FFmpeg file source decoded 60/60 frames at the source's 10 fps with
  a bounded queue of 2 and `dropped_frames_total=58` from the intended 5 FPS throttle.
* **RTSP:** with no RTSP server present, the source performed a bounded reconnect with
  backoff against an unreachable endpoint, reported `rtsp_connected: false`, decoded
  zero frames and stopped cleanly on request.
* **GStreamer:** reported unavailable and refused to run with
  `gstreamer_bindings_unavailable`; the exact pipeline string is recorded
  (`rtspsrc ... rtph264depay ! h264parse ! avdec_h264 ! videoconvert ! appsink`).
* **Temporal:** sustained PPE rule (≥5 frames spanning ≥2 s, cooldown enforced, helmet
  produces nothing), restricted-zone dwell (support 5, dwell 1.0 s, cooldown, timer
  restart on re-entry), loitering variant, low-motion heuristic (fires for a static
  track, silent for a moving track), unknown analyzer names rejected.
* **Telemetry:** real CPU/RSS readings (`cpu_percent 50.0`, `rss 59.9 MB`), GPU
  reported as absent with `gpu: null` and provider reason codes — never as 0 %.
* **Profiles:** local host accepted `cpu_onnx_x86_64`; `cpu_onnx_arm64` rejected with
  `architecture_mismatch`; `nvidia_jetson_tensorrt_arm64` rejected with
  `architecture_mismatch`, `runtime_mismatch` and three `missing_*` reasons. A
  **declared** simulated Jetson is accepted, marked `simulated_hardware: true`, with
  `declared_not_measured_*` notices and an empty `measured_versions` list.
* **Real end-to-end:** the qualified Hansung artifact
  (`sha256 b239aa7e…42`) through `Detector → ByteTrack → zone-dwell analyzer → SQLite
  outbox` produced 4 durable `restricted_zone_dwell` events with full provenance,
  39 inference frames, p50 96.8 ms / p95 194.6 ms on this CPU. This is a short
  functional run, **not** a sustained benchmark and **not** a model-quality result.
* **Transport restart:** the durable outbox and activation journal were exercised
  again by the pre-existing component and continuation checks (below).

### 3.3 PyTorch → ONNX export with graph parity

```bash
.venv/bin/python -m scripts.export_onnx --checkpoint var/tools/hansung-best.pt \
    --output var/model/hansung-export.onnx --report docs/evidence/onnx-export-parity.json
```

* Export recorded opset 17, IR 8, `[1, 3, 640, 640]` input, 10 source classes,
  14 raw channels, mapping version `hansung_ppe_yolov8n_10@0be69c90227d20ba`.
* Parity compares the **raw PyTorch tensor and the raw ONNX tensor through the same
  canonical decoder** on six identical frames with the same letterbox:
  **24/24 detections matched at the operating threshold 0.35** and **159/159 at the
  stress threshold 0.001**, minimum IoU **1.0**, maximum confidence delta **0.0**,
  maximum raw tensor delta `0.003067`.
* The report also counts 24 423 boxes whose head argmax picks a *discarded* source
  class: direct evidence for why every runtime must re-score the mapped subset rather
  than trust the head argmax.
* Qualification is `parity_passed`; `model_quality` stays `NOT EVALUATED` and
  `release_approval` stays `NOT GRANTED`. The exported artifact is **not** byte-identical
  to the qualified one (12 245 975 vs 12 245 976 bytes) and the report says so.

### 3.4 Previously verified behaviour, re-run after the refactor

```bash
.venv/bin/python -m scripts.verify_components --output var/evidence/component-runtime.json
.venv/bin/python -m scripts.verify_continuation
```

* Synthetic engineering fixture (constant-output ONNX, explicitly untrained):
  24 inference frames, exactly one durable rule event, outbox reopen and duplicate
  acknowledgement, model-digest tampering rejected, unknown/conflicting head evidence
  suppressed.
* Post-commit watchdog: real child-process crashes, exponential restart waits,
  previous-worker restoration, full 60-second recovery, retained applied generation.

### 3.5 TensorRT on a real NVIDIA GPU (external Tesla T4 session)

Not this host: a temporary x86_64 CUDA host. The harness, the artifact and the canonical
decoder are the same ones used here, so the result transfers as a claim about the runtime,
not about this machine.

| Fact | Value |
| --- | --- |
| GPU / driver / CUDA / TensorRT | Tesla T4 · 580.82.07 · 12.8 · 11.3.0.99 |
| Artifact | `var/model/hansung-p3.onnx`, sha256 `b239aa7e881fc590633944cf37df46ed477f3a819d09ec7a7bdb19d052ca2422` — byte-identical to the qualified record |
| TensorRT FP32 | 24/24 parity, 0 missing, 0 extra, min IoU **1.0**; p50 4.658 ms, p95 6.479 ms, mean 4.834 ms, **206.87 model-only FPS** |
| ModelOpt mixed-FP16 ONNX vs FP32 ONNX | 24/24, min IoU **0.9955**, mean 0.9975, max confidence delta 0.0009, max box delta 0.67 px |
| TensorRT true mixed FP16 | engine declares `DataType.HALF`; 24/24, min IoU **0.9922**, mean 0.9972, max confidence delta 0.0015; p50 9.016 ms, p95 9.434 ms, mean 9.1 ms, **109.89 model-only FPS** |
| Canonical contract on GPU | a global argmax would have re-interpreted **908 of 1827** boxes (907 of 1826 on the mixed-FP16 graph); the canonical subset decode is therefore mandatory, not stylistic |
| ONNX Runtime CPU reference | p50 116.825 ms, p95 176.232 ms, mean 126.724 ms, 7.89 model-only FPS |
| ONNX Runtime CUDA | **NOT VERIFIED** — the provider was listed but did not become operational; probed and recorded, never assumed usable |

**Engineering finding, reported rather than smoothed over:** true mixed FP16 was *slower*
than FP32 for this graph on this GPU (9.1 ms vs 4.834 ms mean). Candidate causes — cast
overhead, TensorRT tactic/kernel selection, operations retained in FP32, strongly-typed
graph behaviour, stream behaviour, graph structure — are **unconfirmed**; profiling is
required. The conclusion the evidence supports is: profile before choosing precision.

**The rule this run produced.** A first session recorded an "fp16" result whose engine
actually declared `DataType.FLOAT` for both input and output — a requested label, not a
measurement. `edge/runtimes.py` now derives `actual_precision()` from the engine's own
tensor dtypes, and `scripts/verify_tensorrt_gpu.py` records a precision as
`NOT VERIFIED - LABEL_NOT_BACKED_BY_ENGINE_DTYPES` and removes it from the authoritative
table when the engine disagrees with the label. TensorRT 11 no longer exposes
`BuilderFlag.FP16`, so true FP16 requires a strongly-typed graph from
`scripts/convert_fp16_onnx.py`; without one the precision is refused with that reason
instead of measured under a false label. The old record is preserved and labelled in
`docs/evidence/tensorrt/final/superseded/`.

```bash
python -m scripts.verify_tensorrt_gpu --onnx var/model/hansung-p3.onnx \
    --contract-record var/model/hansung-p3.json --samples-dir samples \
    --precisions fp32 --out docs/evidence/tensorrt/fp32
python -m scripts.convert_fp16_onnx --onnx var/model/hansung-p3.onnx \
    --out var/model/hansung-p3-fp16.onnx
python -m scripts.verify_tensorrt_gpu --onnx var/model/hansung-p3.onnx \
    --fp16-onnx var/model/hansung-p3-fp16.onnx --samples-dir samples \
    --precisions fp16 --out docs/evidence/tensorrt/true_fp16
```

Index: [docs/evidence/tensorrt/final](evidence/tensorrt/final/README.md).

## 4. IMPLEMENTED — NOT VERIFIED

| Area | What exists | Why it is unverified |
| --- | --- | --- |
| WebSocket event delivery | `backend/app/events.py` commit-gated bus (`safety_event.created`, `device.health_changed`, `campaign.status_changed`), `/api/v1/ws/events`, nginx upgrade proxy, Vite `ws: true`, console live indicator | no FastAPI/PostgreSQL in this venv; no browser session; **the frontend build was not re-run** (no `node_modules`, package installs time out here), so the console change is syntax-reviewed only |
| GStreamer ingestion | `edge/video.py` `GStreamerSource`, `VISIONOPS_VIDEO_BACKEND`, hardware-decode option behind detection | PyGObject/GStreamer not installed |
| Local RTSP environment | `infrastructure/compose.rtsp.yaml` (MediaMTX), `scripts/publish_rtsp.py` (PyAV publisher), `docs/LOCAL_RTSP.md` | image not pulled; no RTSP server was available |
| DeepStream launcher | `edge/pipeline/deepstream/run.py` routed through `edge.temporal`, generated contract, `Makefile` | no SDK, no `pysad`/`pyds`, no engine |
| Campaign gate reason codes | `REASON_CODES`, `signals()`, per-target `gate_signals`, `campaign_detail` exposes codes + descriptions, console renders them | control plane cannot be started here |
| Release bundle identity | `release_identity()` adds architecture/runtime/runtime_version/model format/mapping version/input shape/dataset/mlflow run; worker asserts the mapping version | requires the release API |
| 10K logical fleet driver | `simulation/fleet_scale.py` (bounded concurrency, offline ratio, failure ratio, permit accounting, latency percentiles) | requires PostgreSQL |
| DVC DAG | `training/dvc.yaml` validate → train → export → qualify with params, deps and metrics | no `dvc` package, no dataset |
| MLflow lineage | `scripts/export_onnx.py --mlflow-tracking-uri`, `training/recompute_evidence.py`, REST client | no MLflow server |
| ARM64/Jetson target contract | `shared/hardware_profiles.py`, `docs/JETSON_DEPLOYMENT_TARGET.md` | no ARM64 build executed |
| Physical Jetson qualification bundle | `scripts/make_jetson_bundle.py` → `var/bundle/visionops-jetson-validation.zip`, `scripts/verify_physical_jetson.py`, `run_jetson_validation.sh`, `RUN_ON_JETSON.md` | **the gate was executed here and correctly refuses** (exit 3); the bundle audit is executed; nothing inside has run on a Jetson |

## 5. BLOCKED BY HARDWARE

* **This host's** NVIDIA runtime verification: no device node, driver, CUDA, cuDNN,
  TensorRT or DeepStream. No `.engine` file exists in this repository and none was
  fabricated. This blocker is about the development host only — the TensorRT runtime
  itself is verified on an external GPU (§3.5).

### 5.1 NVIDIA GPU verification — executed on an external host

The runtime is no longer waiting on a GPU (§3.5). The local pre-flight machinery is
retained because it is what makes a GPU session cheap, and because a GPU-less checkout
must still fail honestly:

```text
TensorRT implementation:            IMPLEMENTED
TensorRT local NVIDIA execution:    BLOCKED BY NVIDIA HARDWARE (this host only)
TensorRT on a real NVIDIA GPU:      VERIFIED (Tesla T4, TensorRT 11.3.0.99)
TensorRT FP32 / true mixed FP16:    VERIFIED / VERIFIED
Physical NVIDIA Jetson:             NOT VERIFIED
ARM64 / JetPack / DeepStream:       NOT VERIFIED
```

| Piece | Path | State |
| --- | --- | --- |
| Harness | `scripts/verify_tensorrt_gpu.py` | `--preflight` **executed** here (model identity `b239aa7e…`, 10-class/14-channel contract, sample extraction, the shared decoder and the TensorRT path's static checks); **executed for real** on the GPU host |
| GPU gate | same script | **verified by execution**: with no GPU/TensorRT it exits `BLOCKED_BY_NVIDIA_HARDWARE` (code 3) and writes `blocked.json`; it never benchmarks the CPU and calls that TensorRT |
| Precision gate | `edge/runtimes.py` `actual_precision()` / `precision_label_matches_engine()` | **verified by execution** on the GPU host: a label the engine's tensor dtypes do not back is recorded as `NOT VERIFIED - LABEL_NOT_BACKED_BY_ENGINE_DTYPES` and excluded from the authoritative table |
| Provider probe | `edge/runtimes.py` `check_provider()` | **verified by execution**: a listed-but-unloadable CUDA provider is `LOAD_FAILED`, not `available` |
| FP16 graph builder | `scripts/convert_fp16_onnx.py` | **executed** on the GPU host (ModelOpt 0.46.1, 259 of 263 nodes converted, Ultralytics metadata restored and verified). Here it exits `2` `TOOL_UNAVAILABLE` rather than producing a graph |
| Bundle builder | `scripts/make_gpu_bundle.py` → `var/bundle/visionops-gpu-bundle.zip` | **executed** (11.8 MB): artifact + contract record + six real frames + the repository code, with a SHA-256 manifest |
| Notebook | `docs/evidence/tensorrt/colab_gpu_verify.ipynb` | code cells compile; **executed on a GPU** and updated to run FP32 and the ModelOpt FP16 path as two runs |
| Fallback | `docs/evidence/tensorrt/KAGGLE_GPU_RUN.md` | ready; **not executed** (Colab worked) |
| Requirements | `requirements-gpu.txt` | documents why `torch` is *not* reinstalled, why `trtexec` is not required, and why `nvidia-modelopt` is needed for a true FP16 engine on TensorRT 11+ |

Each GPU run writes `environment.json`, `benchmark.json`, `parity_<precision>.json`,
`contract-semantics.json`, `tensorrt-verification.json`, `GPU_RUN_RESULT.md` and
`status-patch.json`. The last of those is generated *by the run* and names the exact
documentation lines it authorises changing — and its `must_not_change` list keeps every
Jetson and ARM64 claim at `NOT VERIFIED`. A T4 is a real GPU and is **not** a Jetson.

Full interview framing: [docs/interview/TENSORRT_INTERVIEW_EVIDENCE.md](interview/TENSORRT_INTERVIEW_EVIDENCE.md)
and [docs/interview/11_VERIFIED_VS_UNVERIFIED.md](interview/11_VERIFIED_VS_UNVERIFIED.md).
### 5.2 Physical Jetson — qualification prepared, hardware absent

One uploadable file, one command. Preparation is complete and independently auditable;
it moves **no** verification line on its own.

```text
Jetson qualification bundle:        PREPARED AND LOCALLY AUDITED
Physical Jetson:                    NOT VERIFIED
JetPack on physical hardware:       NOT VERIFIED
ARM64/aarch64 runtime execution:    NOT VERIFIED
TensorRT on Jetson:                 NOT VERIFIED
NVDEC / hardware decode:            NOT VERIFIED
DeepStream runtime:                 NOT VERIFIED
Jetson thermal / power:             NOT VERIFIED
10K physical Jetson fleet:          NOT VERIFIED
```

| Piece | Path | State |
| --- | --- | --- |
| Bundle | `var/bundle/visionops-jetson-validation.zip` (15.2 MB, 222 entries, gitignored) | **built and audited by execution**: 221/221 declared SHA-256 + sizes re-derived from the reopened archive, 0 problems, 0 engine files, 0 secret findings |
| Builder | `scripts/make_jetson_bundle.py` | **executed**: asserts the required files exist, refuses to ship any `.engine`/`.plan`, secret-scans the payload, records the FP32 SHA against the qualified lineage |
| Orchestrator | `scripts/verify_physical_jetson.py` | **executed here and correct**: exits `3` `BLOCKED_NOT_PHYSICAL_JETSON`, writes `blocked.json`, claims nothing. Inside the extracted bundle it resolves `model/`, `samples/` and `contract` and confirms `b239aa7e…` by hash |
| On-device entry | `run_jetson_validation.sh` | **executed here**: refuses with exit `3` on `x86_64` before running any phase, matching the module's contract |
| Jetson detector | `scripts/detect_jetson_environment.py` | **executed**: `JETSON_NOT_DETECTED` (exit 3); needs a real Tegra marker plus `aarch64`, and ignores `VISIONOPS_DECLARED_*` |
| Package policy | `requirements-jetson.txt` | Policy only: NVIDIA components are listed as `[provided-by-jetpack]`, never installed. The wrapper will not pip-install anything matching `nvidia`, `torch` or `onnxruntime` |
| Comparison frame | `docs/evidence/jetson/T4_VS_JETSON.md` | Real T4 column, Jetson column is `PENDING PHYSICAL RUN` — no cell is estimated |

The chain a physical run will close:

```bash
unzip visionops-jetson-validation.zip && cd visionops-jetson-validation && ./run_jetson_validation.sh
# -> evidence/visionops-jetson-evidence.zip (+ status-patch.json naming the claims it authorises)
```

* Any GPU utilization / VRAM / power / TOPS figure: the GPU run recorded `gpu_memory` as
  `NOT MEASURED`, so no such number is reported anywhere. On this host the API reports
  `gpu_metrics_available = false`, and the Jetson path inherits that: `tegrastats` fields
  are `null` when absent, never `0`.
* JetPack execution and physical ARM64 execution.
* DeepStream runtime — the launcher and generated contract are static-checked only; no
  DeepStream SDK existed on the GPU host either.

## 6. BLOCKED BY DATA

* Model quality: no labeled PPE dataset, so no mAP, no `no_helmet` precision/recall, no
  temporal event precision/recall. The server-owned real promotion gate still refuses a
  `real`-mode release, and that refusal is correct.
* The real-footage rule result remains a **negative case** (0 true violations): the
  single low-confidence bare-head observation cannot satisfy ≥5 frames spanning ≥2 s.
  Thresholds were not lowered to manufacture an alert.

### 6.1 Stale checked-in contracts (documented, not fixed here)

`shared/contracts/openapi.json` and `shared/contracts/postgresql-schema.sql` are
snapshots produced by `scripts/audit_release.py`, which imports `backend.app.main` and
therefore needs FastAPI and SQLAlchemy. Neither package is installed in this venv and
package installation times out, so **those two files were not regenerated** and do not
yet contain the new `GET /api/v1/hardware-profiles` route or the WebSocket endpoint.
They stay as the previous verified snapshot rather than being hand-edited. Regenerate
them on a host with the backend dependencies:

```bash
.venv/bin/python -m scripts.audit_release
```

## 7. NOT IMPLEMENTED

* Complete browser-level UI/accessibility validation; artifact upload and model
  registration remain CLI/API operations.
* Full retention cleanup, rate limiting, source credential injection, TLS deployment,
  and narrowing artifact download scope to only desired/actual/last-good references.
* A completed real-data evaluation import journey end to end.
* Periodic `DetectionEvent` sampling inside the CPU worker production loop.
* Snapshot capture/upload scheduling and a local HTTP preview server.

## 8. Evidence map

| File | Proves |
| --- | --- |
| `docs/evidence/model-contract-runtime.json` | contract/parser/config consistency, independent decoder equivalence |
| `docs/evidence/edge-platform-runtime.json` | runtimes, telemetry, profiles, video, RTSP, temporal, real end-to-end |
| `docs/evidence/onnx-export-parity.json` | export metadata and two-threshold graph parity |
| `docs/evidence/component-runtime.json` | synthetic plumbing, outbox durability, tamper rejection |
| `docs/evidence/continuation-runtime.json` | rollback leasing + post-commit watchdog |
| `docs/evidence/tensorrt/final/fp32/` | TensorRT FP32 on a Tesla T4: parity 24/24 at IoU 1.0, benchmark, contract semantics (908 of 1827 boxes) |
| `docs/evidence/tensorrt/final/true_fp16/` | ModelOpt mixed-FP16 ONNX parity, TensorRT FP16 (engine dtype `HALF`) parity + benchmark |
| `docs/evidence/tensorrt/final/superseded/` | the run-1 "fp16" claim and the engine-dtype line proving it was FP32 — kept, labelled, not cited |
| `docs/evidence/tensorrt/PROVENANCE.json` | sha256 of the source archive the GPU evidence was imported from |
| `docs/evidence/security-runtime.json`, `service-runtime.json`, `contract-audit.json`, `environment.json` | earlier baseline (backend/frontend/DDL checks) — unchanged, still not end-to-end |

## 9. What this document does not claim

No end-to-end verified platform, no production release, no fleet result, no model-quality
result and no cloud deployment. The control plane is implemented and statically
consistent but cannot be executed on this host.

The NVIDIA claim is scoped precisely: **TensorRT is verified on a real NVIDIA GPU**
(§3.5), while **physical Jetson, JetPack, ARM64 execution and DeepStream runtime remain
NOT VERIFIED**, and this host still cannot run TensorRT at all. `gpu_memory`,
GPU utilization, power and TOPS were never measured and are therefore never reported.
