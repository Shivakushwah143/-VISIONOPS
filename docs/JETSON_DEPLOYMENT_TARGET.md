# ARM64 / Jetson deployment target

This document defines what a Jetson deployment *requires* and states plainly that
**no physical Jetson and no ARM64 execution was validated here**. The TensorRT runtime
itself *was* validated, on a real but non-Jetson NVIDIA GPU (Tesla T4, TensorRT
11.3.0.99) — see [evidence/tensorrt/final](evidence/tensorrt/final/README.md). That run
says nothing about JetPack, ARM64 or NVDEC, so nothing in this document is upgraded by
it; a T4 is not a Jetson and the two are never conflated.

## 0. Physical qualification bundle — prepared, not run

The gap between "implemented" and "verified on a Jetson" is now a single uploadable file
with one entry point, so closing it costs one manual session rather than an engineering
project. Nothing in it is a claim: it carries inputs only.

```text
var/bundle/visionops-jetson-validation.zip     (gitignored; built by scripts/make_jetson_bundle.py)
  MANIFEST.json          bundle version, git commit + working-tree state, every SHA-256, audit result
  model/                 qualified FP32 ONNX (b239aa7e…) + contract record; FP16 ships only when supplied
  samples/               six real qualification frames + the sample clip (canonical samples/ppe.mp4)
  code/                  the repository and all six qualification scripts
  requirements/          requirements-jetson.txt (application packages only — see §0.3)
  run_jetson_validation.sh
  RUN_ON_JETSON.md
```

On the device:

```bash
unzip visionops-jetson-validation.zip && cd visionops-jetson-validation && ./run_jetson_validation.sh
```

### 0.1 Entry point and exit codes

`scripts/verify_physical_jetson.py` orchestrates, in order: environment detection → model
identity → canonical contract → on-device TensorRT FP32/FP16 engine build → parity →
benchmark → telemetry → real video pipeline → optional RTSP → DeepStream inspection →
sustained run → evidence ZIP → status patch. [RUN_ON_JETSON.md](../RUN_ON_JETSON.md) is the
on-device procedure; [evidence/jetson](evidence/jetson/README.md) is the status model.

| Code | Meaning |
| --- | --- |
| 0 | `QUALIFICATION_COMPLETE` |
| 2 | `ENVIRONMENT_INCOMPLETE` — looks like a Jetson, a prerequisite is missing |
| 3 | `BLOCKED_NOT_PHYSICAL_JETSON` |
| 4 | ran on a Jetson, at least one phase failed (evidence is still packaged) |
| 5 | `PARTIAL_JETSON_ENVIRONMENT` (needs `--allow-partial`; never authorises a Jetson claim) |

The gate is the same detector described in §3: it requires real Tegra evidence on an
`aarch64` host. There is no flag that turns an x86 machine into a Jetson, and no phase
begins before that gate passes. On this development host the correct outcome is exit **3**
with `blocked.json` — reproduced locally, not assumed. Every phase records its command,
exit code, stdout/stderr tails and duration into `phases.json`, and a failed run is
packaged too: a real blocker with a real error is itself the evidence.

### 0.2 Engines are built on the device, never shipped

The bundle contains **no** `.engine`/`.plan`. The T4 engine in
[evidence/tensorrt/final](evidence/tensorrt/final/README.md) would load nowhere else: a
plan is compiled against one GPU architecture, one TensorRT version and one driver.
`scripts/make_jetson_bundle.py` fails its own audit if an engine file enters the archive,
and the qualification scripts record engines by SHA-256 and size instead of shipping them.

### 0.3 Dependency policy

JetPack's CUDA, cuDNN and TensorRT are built against that device's L4T kernel and are not
interchangeable with desktop or datacenter wheels. `requirements-jetson.txt` separates
`[application]` from `[provided-by-jetpack]`, `run_jetson_validation.sh` installs only the
former and refuses to pip-install anything matching `nvidia`, `torch` or `onnxruntime`,
and a missing TensorRT import is reported as a blocker rather than repaired from PyPI.

### 0.4 Status after preparation

```text
Jetson qualification bundle:        PREPARED AND LOCALLY AUDITED
Physical Jetson / JetPack / ARM64:  NOT VERIFIED  (unchanged by preparation)
TensorRT on Tesla T4:               VERIFIED  (separate host, separate evidence)
```

Preparation does not move a verification line. Only a returned
`visionops-jetson-evidence.zip` does, and only for the claims its evidence supports
(`status-patch.json` names them, with a `not_authorised` list beside them).

## 1. Hardware profiles

`shared/hardware_profiles.py` is the single source of truth. The edge agent no longer
accepts one hardcoded literal.

| Profile | Architecture | Runtime | Accelerators | Model formats | Requires |
| --- | --- | --- | --- | --- | --- |
| `cpu_onnx_x86_64` | `x86_64` | `onnxruntime` | cpu | onnx | — |
| `cpu_onnx_arm64` | `aarch64` | `onnxruntime` | cpu | onnx | — |
| `nvidia_jetson_tensorrt_arm64` | `aarch64` | `tensorrt` | cuda, tensorrt | onnx, tensorrt | jetpack, cuda, tensorrt versions |

`evaluate(profile, environment)` returns reason codes, never a bare boolean:

```text
architecture_mismatch | runtime_mismatch | profile_not_simulatable
missing_jetpack_version | missing_cuda_version | missing_tensorrt_version
```

`GET /api/v1/hardware-profiles` exposes the matrix. Device creation rejects an unknown
profile (`unknown_hardware_profile`) instead of storing an unusable string.

## 2. Device inventory

`GET /api/v1/devices/{id}` returns the device row plus the runtime fields the latest
heartbeat reported, with unknown staying `null`:

```text
architecture, os, runtime, runtime_version, simulated_hardware,
jetpack_version, cuda_version, tensorrt_version, declared_versions, measured_versions
```

`declared_versions` and `measured_versions` are separate lists on purpose: a version an
operator declared for a simulated target must never be presented as a measurement.

## 3. Simulated targets

For locally simulated devices, `simulated_hardware: true` is mandatory and no GPU/TOPS
number is ever produced. Declare a target with:

```bash
VISIONOPS_DECLARED_ARCHITECTURE=aarch64
VISIONOPS_DECLARED_JETPACK_VERSION=6.0
VISIONOPS_DECLARED_CUDA_VERSION=12.2
VISIONOPS_DECLARED_TENSORRT_VERSION=8.6
```

Rules enforced by `shared/hardware_profiles.py`:

* a profile that is not simulatable (`cpu_onnx_x86_64`) is rejected as
  `profile_not_simulatable`;
* declared values are recorded as `declared_not_measured_*`;
* a simulated target that also claims **measured** CUDA/TensorRT/JetPack versions
  raises `simulated_target_cannot_report_measured_gpu_versions`;
* a simulated environment whose runtime is not installed produces the notice
  `runtime_not_present_on_simulation_host`, so a simulation can drive the control plane
  without ever being reported as a qualified runtime.

## 4. Container build

Multi-architecture build configuration (Docker Buildx):

```bash
docker buildx create --name visionops --use            # once
docker buildx build --platform linux/amd64,linux/arm64 \
  -f infrastructure/docker/backend.Dockerfile -t visionops-backend:local --load .
```

Base images are version-pinned but **not digest-frozen**, and neither platform was
built in this environment:

```text
BUILD CONFIGURATION: IMPLEMENTED
BUILD VERIFIED:      no
RUNTIME VERIFIED:    no  (no ARM64 host, no Jetson, no TensorRT)
```

On the Jetson itself the runtime image must be the NVIDIA L4T base so CUDA, cuDNN and
TensorRT match the JetPack generation, for example
`nvcr.io/nvidia/l4t-pytorch`/`l4t-tensorrt` for the installed L4T release, or
`nvcr.io/nvidia/deepstream:<version>-triton-multiarch` when the DeepStream path is used.
Mixing a host JetPack version with a different container CUDA/TensorRT generation is the
most common cause of "engine built, fails to load" reports; the version tuple must be
recorded before any engine is generated.

## 5. Model / runtime compatibility

| Artifact format | Runs on | Notes |
| --- | --- | --- |
| `onnx` (CPU) | `cpu_onnx_x86_64`, `cpu_onnx_arm64` | verified here on x86_64 CPU |
| `onnx` (CUDA provider) | Jetson with the CUDA execution provider | provider is detected, never assumed |
| `tensorrt` engine | the exact GPU + TensorRT version that built it | engines are not portable; never committed or shared |

The release manifest records `runtime`, `runtime_version`, `architecture`,
`model_format`, `input_shape` and `class_mapping_version`, and the edge worker refuses
to load a candidate whose mapping version does not match the released one
(`class_mapping_version_mismatch`).

## 6. Qualifying the NVIDIA path on real hardware

1. Record `nvidia-smi` output, driver, CUDA, cuDNN, TensorRT, DeepStream and L4T/JetPack
   versions in the device inventory.
2. `make contract && make` in `edge/pipeline/deepstream/` against that SDK
   (the contract header is generated from `shared/model_contract.py`).
3. Build the engine **on the target** with `TensorRTRuntime.build_engine()`
   (`trtexec` equivalent command recorded in `planned_commands()`); never download or
   copy an engine.
4. Compare real labeled PPE output against the CPU ONNX path with the same
   IoU/class/confidence tolerances used by `scripts/export_onnx.py`.
5. Record measured latency, throughput, memory and power **only if measured**, and
   mark the profile qualified only then.

## 7. Honest boundary

```text
Runtime abstraction + capability detection        IMPLEMENTED
ARM64 / Jetson profile matrix                     IMPLEMENTED
Multi-arch build configuration                    IMPLEMENTED
TensorRT on a real NVIDIA GPU (Tesla T4, x86_64)  VERIFIED   (docs/evidence/tensorrt/final)
TensorRT benchmark numbers (model-only)           MEASURED   (FP32 4.834 ms mean, FP16 9.1 ms mean)
Physical Jetson / JetPack / ARM64 execution       NOT VERIFIED
Jetson thermal, power, NVDEC                      NOT MEASURED
DeepStream runtime                                NOT VERIFIED (SDK absent, config static-checked)
Engine portability between GPU generations         NOT ASSERTED (engines are hardware specific)
```

What the T4 run *does* transfer, because it is a property of the model and the runtime
rather than the board: the qualified ONNX converts to a mixed-FP16 graph that preserves
detection behaviour (min IoU 0.9955 on the qualification samples), and the canonical
contract decode is mandatory (a global argmax would have re-interpreted 908 of 1827
boxes). What it does **not** transfer: engine portability, NVDEC decode, thermals, power
envelope, sustained throughput under a Jetson's shared memory bandwidth, or JetPack
library compatibility.
