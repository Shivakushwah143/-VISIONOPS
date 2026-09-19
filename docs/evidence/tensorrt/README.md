# TensorRT evidence

This directory holds two different things, and they must not be confused:

| Layer | Where | Status |
| --- | --- | --- |
| **Local development host** | `blocked.json`, `preflight.json`, `environment.json`, `bundle-manifest.json` | no NVIDIA device at all — `BLOCKED_BY_NVIDIA_HARDWARE` |
| **External NVIDIA GPU session** | [`final/`](final/README.md) | TensorRT FP32 + mixed FP16 **VERIFIED** on a Tesla T4 |

Both are true at the same time. The GPU result was measured on a temporary x86_64 CUDA host;
it does not make this development machine capable of running TensorRT, and it is **not** a
Jetson result.

## Local host (this machine)

| File | Contents |
| --- | --- |
| `environment.json` | local capability snapshot: `nvidia_smi.present: false`, `gpu_names: []`, `tensorrt: "unavailable: ModuleNotFoundError"` |
| `blocked.json` | `BLOCKED_BY_NVIDIA_HARDWARE` with the exact blockers and the explicit `did_not_do` list (no CPU fallback, no estimated number, no fabricated engine) |
| `preflight.json` | the GPU-free rehearsal: model identity, canonical contract, sample extraction, CPU decode and static TensorRT path checks all pass, so a GPU session is not spent debugging the harness |
| `bundle-manifest.json` | audit of the self-contained GPU bundle that was uploaded to the GPU session |
| `colab_gpu_verify.ipynb` | the notebook used for the GPU run |
| `KAGGLE_GPU_RUN.md` | Kaggle fallback instructions |

Local reproduction, expected to stay blocked:

```bash
python -m scripts.verify_tensorrt_gpu --preflight --out docs/evidence/tensorrt   # exit 0
python -m scripts.verify_tensorrt_gpu --out docs/evidence/tensorrt              # exit 3
```

## External GPU session

See [`final/README.md`](final/README.md) for the authoritative FP32 and true-mixed-FP16
results, the superseded run-1 record, and the reproduction command.

```
Tesla T4 · driver 580.82.07 · CUDA 12.8 · TensorRT 11.3.0.99 · Python 3.13.15
TensorRT FP32:        24/24 parity, IoU 1.0,      mean 4.834 ms, 206.87 model-only FPS
TensorRT mixed FP16:  24/24 parity, min IoU 0.9922, mean 9.1 ms, 109.89 model-only FPS
```

**Still NOT VERIFIED after the GPU run:** physical NVIDIA Jetson, JetPack on physical
hardware, Jetson thermal/power/NVDEC behaviour, ARM64 runtime execution, DeepStream
runtime, 10K physical Jetson fleet, and model quality (no labelled dataset).

## Provenance

[`PROVENANCE.json`](PROVENANCE.json) records the source archive and its sha256 so the
handoff from the GPU session is auditable. `scripts/make_gpu_bundle.py` audits bundle
contents (declared hashes, required files, secret scan) and
`scripts/verify_tensorrt_gpu.py` regenerates both the local blocker record and the GPU
evidence from a single command.
