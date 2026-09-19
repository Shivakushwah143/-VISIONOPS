# final — authoritative TensorRT results from a real NVIDIA GPU

Everything under this directory was produced on a **real Tesla T4** in an external GPU
session (Colab/Kaggle class, x86_64). All files are byte-for-byte copies of that session's
output; no local CPU number was substituted and nothing was re-derived here.

Local host, for contrast: `../blocked.json` and `../environment.json` record that this
development machine has **no NVIDIA device, no driver, no CUDA and no TensorRT**.

## Verdict

```
TensorRT on NVIDIA GPU:      VERIFIED (Tesla T4, TensorRT 11.3.0.99)
TensorRT FP32:               VERIFIED
TensorRT mixed FP16:         VERIFIED (ModelOpt AutoCast graph, engine dtype HALF)
canonical model contract:    VERIFIED for the tested samples
Physical NVIDIA Jetson:      NOT VERIFIED
JetPack on physical hw:      NOT VERIFIED
ARM64 runtime execution:     NOT VERIFIED
DeepStream runtime:          NOT VERIFIED (separate execution required)
10K physical Jetson fleet:   NOT VERIFIED
```

A T4-class Colab/Kaggle GPU is **not** a Jetson. Only "TensorRT on NVIDIA GPU" flips to
VERIFIED; every physical-Jetson and ARM64 claim stays NOT VERIFIED.

## Authoritative benchmark

Model-only, CUDA-synchronised, host timer around a synchronised execute. This is **not**
end-to-end camera FPS.

| Runtime | Precision | Host | p50 ms | p95 ms | Mean ms | Model-only FPS | Parity |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| ONNX Runtime CPU | FP32 | GPU host CPU | 116.825 | 176.232 | 126.724 | 7.89 | reference |
| TensorRT | FP32 | Tesla T4 | 4.658 | 6.479 | 4.834 | 206.87 | 24/24, IoU 1.0 |
| TensorRT | true mixed FP16 | Tesla T4 | 9.016 | 9.434 | 9.1 | 109.89 | 24/24, min IoU 0.9922 |

The superseded pseudo-FP16 row (~4.78 ms, ~209 FPS) is **excluded** from this table — see
[`superseded/README.md`](superseded/README.md). Model-only FPS excludes RTSP/network,
decode, preprocessing, tracking, temporal analysis and event handling, so real camera FPS
is strictly lower.

**Engineering finding:** true mixed FP16 was *slower* than FP32 on this T4 for this graph
(9.1 ms vs 4.834 ms mean). FP16 did not improve latency here. Candidate explanations
(mixed-precision cast overhead, tactic/kernel selection, FP32-retained operations,
strongly-typed graph behaviour, default-stream synchronisation, graph structure) are
**not confirmed** — profiling is required before any of them is asserted. The defensible
conclusion is: *profile before selecting precision.*

## Layout

| Path | Contents |
| --- | --- |
| `FINAL_GPU_EVIDENCE_SUMMARY.json` | the curated, authoritative summary of both runs |
| `fp32/` | qualified FP32 ONNX → TensorRT FP32: environment, parity, benchmark, contract semantics |
| `true_fp16/` | qualified FP32 ONNX → ModelOpt mixed-FP16 ONNX → TensorRT FP16, plus the FP32-vs-FP16 ONNX parity check |
| `superseded/` | the run-1 "fp16" result, preserved and labelled: logical FP16 label over an FP32 graph |

Each directory carries its own `SUPERSEDED.md` / `README.md` stating exactly which keys are
authoritative and which must not be read as FP16.

## Artifacts referenced, not committed

`.engine` binaries are hardware- and TensorRT-version-specific, so they are never
committed and the GPU run deleted them after measuring (`engine_deleted_after_run: true`).
They are referenced by hash instead:

| Artifact | sha256 | Bytes |
| --- | --- | ---: |
| qualified FP32 ONNX (`var/model/hansung-p3.onnx`) | `b239aa7e881fc590633944cf37df46ed477f3a819d09ec7a7bdb19d052ca2422` | 12245976 |
| ModelOpt mixed-FP16 ONNX | `a74ec6b384c93b2d0bd5d99b3fcc1399712ffee479e9b490c2cda276e1cbea58` | 6215909 |
| TensorRT FP32 engine | `7ca68400f45e3898eec321dcf5a6e4508937ece14b7df14fadec422812967315` | 80982948 |
| TensorRT FP16 engine | `7caaafd08b4d8b4e884ea8f4da169d82baa324d0aca354b1b130ab86636a12fe` | 45024844 |

## Reproduce

```bash
# on an NVIDIA GPU host, from the repository root
python -m scripts.verify_tensorrt_gpu \
    --onnx var/model/hansung-p3.onnx \
    --contract-record var/model/hansung-p3.json \
    --samples-dir samples --precisions fp32 \
    --fp16-onnx var/model/hansung-p3-fp16.onnx --precisions-fp16 fp16 \
    --out docs/evidence/tensorrt/rerun
```

The mixed-FP16 graph is produced from the qualified FP32 ONNX by
`scripts/convert_fp16_onnx.py` (ModelOpt AutoCast + Ultralytics metadata restoration).
Reference notebook: [`../colab_gpu_verify.ipynb`](../colab_gpu_verify.ipynb). No NVIDIA
hardware? The same command exits `3` with `BLOCKED_BY_NVIDIA_HARDWARE` — it never falls
back to CPU and calls that a TensorRT result.
