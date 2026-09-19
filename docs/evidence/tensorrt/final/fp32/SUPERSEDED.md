# FP32 TensorRT evidence — authoritative, with one superseded key

These files are **byte-for-byte copies** of the first GPU session's output. They are the
authoritative source for the FP32 result. Nothing here was edited or re-derived locally.

## Authoritative FP32 result

Source of truth: `parity_fp32.json`, `benchmark.json` (`precisions.fp32`).

| Fact | Value |
| --- | --- |
| GPU | Tesla T4 (driver 580.82.07, CUDA 12.8, TensorRT 11.3.0.99) |
| ONNX | `hansung-p3.onnx`, sha256 `b239aa7e881fc590633944cf37df46ed477f3a819d09ec7a7bdb19d052ca2422` |
| Engine input dtype | `DataType.FLOAT` (real FP32 engine) |
| Parity | 24/24 matched, 0 missing, 0 extra, min IoU 1.0, mean IoU 1.0, max confidence delta 0.0 |
| Latency | p50 4.658 ms, p95 6.479 ms, mean 4.834 ms |
| Model-only FPS | 206.87 (`1000 / mean_ms`) |

## SUPERSEDED inside this directory — do not read as FP16

`benchmark.json` → `precisions.fp16` and `tensorrt-verification.json` → `precisions.fp16`
are **NOT** true FP16 evidence. That engine was built from the FP32 ONNX and declares
`DataType.FLOAT` for both input and output; only the *requested precision label* said
`fp16`. Its reported 4.78 ms mean / 209.21 model-only FPS must never be quoted as an FP16
result.

See [`../superseded/README.md`](../superseded/README.md) for the full record and the
engine-dtype line that proves it. The authoritative FP16 result lives in
[`../true_fp16/`](../true_fp16/).

## Files

| File | Contents |
| --- | --- |
| `environment.json` | GPU host snapshot (Tesla T4, CUDA 12.8, TensorRT 11.3.0.99, capability report) |
| `benchmark.json` | benchmark table, reference benchmark, per-precision blocks (**contains the superseded `fp16` block**) |
| `parity_fp32.json` | FP32 parity against the ONNX Runtime CPU reference, through the shared canonical decoder |
| `contract-semantics.json` | proof that a global argmax would re-interpret 908 of 1827 boxes, so the canonical subset decode is required |
| `tensorrt-verification.json` | the run summary (**contains the superseded `fp16` block**) |
| `GPU_RUN_RESULT.md` | the run's own rendered report (shows the superseded `fp16` row — see note above) |
| `status-patch.json` | the exact status lines this run authorised changing, and the `must_not_change` list |
| `samples.json` | the 6 evaluation frames and their sha256 |

## Model identity

`sha256(var/model/hansung-p3.onnx)` on this local host is
`b239aa7e881fc590633944cf37df46ed477f3a819d09ec7a7bdb19d052ca2422` — identical to
`onnx_sha256` in `environment.json`, so the measured engine and the repository's qualified
artifact are the same bytes. The ONNX itself stays out of Git (`var/` is ignored); the
evidence references it by hash and by the reproduction command in `../README.md`.
