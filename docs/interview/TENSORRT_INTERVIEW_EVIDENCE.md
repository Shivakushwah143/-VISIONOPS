# TensorRT / Jetson interview evidence

One page, in interview order. **Every cell is either a measured value or the exact word
`NOT VERIFIED`.** Nothing here is an estimate, and no number was copied from NVIDIA
documentation or from a CPU benchmark.

## Status right now

```text
TensorRT implementation:              IMPLEMENTED
TensorRT local NVIDIA execution:      BLOCKED BY NVIDIA HARDWARE (this machine has no GPU)
TensorRT on NVIDIA GPU:               VERIFIED  (Tesla T4)
TensorRT FP32:                        VERIFIED
TensorRT mixed FP16:                  VERIFIED  (ModelOpt mixed-FP16 graph, engine dtype HALF)
Canonical model contract on GPU:      VERIFIED  (for the 6 tested samples)
Physical NVIDIA Jetson:               NOT VERIFIED
ARM64 / JetPack execution:            NOT VERIFIED
DeepStream runtime:                   NOT VERIFIED (implementation only, separate execution)
```

Machine-readable source: [`docs/evidence/tensorrt/final/`](../evidence/tensorrt/final/README.md).

**One precision in the earlier evidence set does not count.** A first run labelled an
engine `fp16` while the graph and engine were FP32 (`DataType.FLOAT` in, `DataType.FLOAT`
out); that number is preserved and marked
`SUPERSEDED — LOGICAL FP16 LABEL, FP32 GRAPH/INPUT` in
[`final/superseded/`](../evidence/tensorrt/final/superseded/README.md). The harness now
refuses to record a precision the engine's own tensor dtypes do not back.

---

## What did I verify?

```text
real Hansung PPE ONNX  ->  TensorRT FP32  ->  NVIDIA GPU  ->  real inference on real frames
   ->  canonical decode  ->  parity vs reference  ->  synchronised latency + throughput

real Hansung PPE ONNX  ->  ModelOpt mixed-FP16 ONNX  ->  metadata restoration
   ->  FP32-vs-FP16 ONNX parity  ->  TensorRT FP16 (engine declares HALF)  ->  parity
```

| Stage | Evidence file |
| --- | --- |
| FP32 TensorRT, parity + benchmark | `final/fp32/parity_fp32.json`, `final/fp32/benchmark.json` |
| FP32 -> mixed-FP16 conversion parity | `final/true_fp16/fp32_onnx_vs_mixed_fp16_onnx.json` |
| FP16 TensorRT, parity + benchmark | `final/true_fp16/parity_fp16.json`, `final/true_fp16/benchmark.json` |
| Canonical contract cannot be hijacked by a discarded class | `final/fp32/contract-semantics.json` |
| Provider reality (listing is not proof) | `final/fp32/benchmark.json -> onnx_provider_probes` |

Locally verified at the same time, with re-runnable commands on a GPU-less machine:

* `python -m scripts.verify_model_contract` — the qualified artifact's 10-class / 14-channel
  head is decoded by the canonical contract, and an independently written NumPy decoder
  inside the check agrees on real frames (12 boxes, max box delta `0.0`).
* `python -m scripts.export_onnx` — raw PyTorch vs raw ONNX through the *same* canonical
  decoder: 24/24 detections at confidence 0.35, 159/159 at 0.001, min IoU `1.0`,
  max raw tensor delta `0.003067`.
* `python -m scripts.verify_edge_platform` — the runtime abstraction reports CPU
  available and CUDA/TensorRT unavailable *with reasons*, and both refuse to load
  (`RuntimeUnavailable`) instead of silently falling back to CPU.

## What hardware?

| Fact | Value |
| --- | --- |
| GPU | **Tesla T4**, 15360 MiB, compute capability 7.5 |
| Driver | 580.82.07 |
| CUDA | 12.8 |
| TensorRT | 11.3.0.99 |
| PyTorch | 2.11.0+cu128, `torch.cuda.is_available() == True` |
| ONNX Runtime | 1.24.4 |
| Host | Linux x86_64, Python 3.13.15 — a temporary Colab/Kaggle-class CUDA host |
| Local machine | Windows 11, `AMD64`, no NVIDIA device node, no `nvidia-smi`, no `nvcc`, no `tensorrt` module |

The two rows are both true at once, and the repository states it that way: the GPU result
was measured externally and does not make the development machine TensorRT-capable.
`docs/evidence/tensorrt/blocked.json` still records the local blocker.

A Colab or Kaggle T4 is a real NVIDIA GPU and is **not** a Jetson. The two are never
conflated here.

## What improved?

Model-only, CUDA-synchronised, host timer around a synchronised execute. This is **not**
end-to-end camera FPS.

| Runtime | Precision | Host | p50 ms | p95 ms | Mean ms | Model-only FPS | Parity |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| ONNX Runtime CPU | FP32 | GPU host CPU | 116.825 | 176.232 | 126.724 | 7.89 | reference |
| TensorRT | FP32 | Tesla T4 | 4.658 | 6.479 | 4.834 | 206.87 | 24/24, IoU 1.0 |
| TensorRT | true mixed FP16 | Tesla T4 | 9.016 | 9.434 | 9.1 | 109.89 | 24/24, min IoU 0.9922 |

**FP16 did not improve latency for this graph on this GPU** (9.1 ms vs 4.834 ms mean,
109.89 vs 206.87 model-only FPS). That is the measured result and it is not hidden.
Candidate explanations — mixed-precision cast overhead, TensorRT tactic/kernel selection,
operations that stayed in FP32, strongly-typed graph behaviour, stream behaviour, graph
structure — are **not confirmed**. The defensible conclusion is the one with evidence
behind it: *profile before selecting precision.*

## Did model behavior change?

Three separate questions, three separate answers:

| Comparison | Reference detections | Matched | Missing | Extra | Min IoU | Mean IoU | Max conf delta | Class agreement |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| TensorRT **FP32** vs ONNX CPU reference | 24 | 24 | 0 | 0 | **1.0** | 1.0 | 0.0 | 24/24 |
| **FP32 ONNX vs mixed-FP16 ONNX** (the conversion itself) | 24 | 24 | 0 | 0 | **0.9955** | 0.9975 | 0.0009 | 24/24 |
| TensorRT **FP16** vs ONNX CPU reference | 24 | 24 | 0 | 0 | **0.9922** | 0.9972 | 0.0015 | 24/24 |

Criterion: all detections matched within the same class at IoU ≥ 0.5 and
|confidence delta| ≤ 0.05, using the **same canonical decoder on both sides**. So the
answers are: an FP32 TensorRT engine reproduced the reference exactly; the optimisation
transform preserved behaviour within 0.45 % IoU; and the FP16 engine stayed within 0.78 %
IoU with a 0.0015 confidence delta. "The engine built" is never treated as "the model is
equivalent".

**The canonical contract is why any of this is comparable at all.** The real model has a
10-class score head while the canonical taxonomy is 3 classes (`person`, `helmet`,
`no_helmet` via source indices 5, 0, 2). On the six qualification frames, a naive global
argmax would have chosen a *discarded* source class for **908 of 1827** score vectors
(907 of 1826 on the mixed-FP16 graph) — i.e. roughly half of all boxes would have been
misinterpreted or dropped. Every runtime therefore decodes through the canonical subset.

## What remains unverified?

```text
Physical NVIDIA Jetson:              NOT VERIFIED
JetPack on physical hardware:        NOT VERIFIED
Jetson thermal / power / NVDEC:      NOT VERIFIED
10K physical Jetson fleet:           NOT VERIFIED
ARM64 runtime execution:             NOT VERIFIED
DeepStream runtime:                  NOT VERIFIED (implementation only; no DeepStream SDK in the run)
TensorRT INT8:                       NOT ATTEMPTED (no real calibration set or validation path)
Model quality (mAP, event P/R):      BLOCKED BY DATA (no labeled PPE dataset)
```

## What would I do on Jetson?

The same signed lifecycle, plus the hardware-specific qualification stage a desktop GPU
cannot stand in for:

```text
same signed release bundle        (Ed25519 + SHA-256, already implemented)
+ ARM64 / aarch64 container         (Docker Buildx target, config present)
+ JetPack / CUDA / cuDNN / TensorRT version compatibility gate
+ TensorRT engine qualification ON the target device (engines are hardware specific)
+ real RTSP / NVDEC video decode
+ thermal, power and sustained-throughput benchmark
```

That qualification lives in [JETSON_DEPLOYMENT_TARGET.md](../JETSON_DEPLOYMENT_TARGET.md).

## The sentence this evidence supports

> I validated the real VisionOps PPE model on a Tesla T4 with TensorRT 11.3. The FP32
> TensorRT engine preserved all 24 reference detections with IoU 1.0 and measured about
> 4.8 ms mean model-only inference latency. For TensorRT 11 I also built the proper
> ModelOpt mixed-FP16 graph, validated its parity against the original ONNX, and then
> qualified the FP16 TensorRT engine. Interestingly, FP16 was slower than FP32 for this
> graph on the T4, which reinforced that precision choices need profiling rather than
> assumptions. Physical Jetson deployment remains a separate hardware qualification step.

## Reproduce it

```bash
# 1. FP32 (qualified ONNX)
python -m scripts.verify_tensorrt_gpu \
    --onnx var/model/hansung-p3.onnx --contract-record var/model/hansung-p3.json \
    --samples-dir samples --precisions fp32 --out docs/evidence/tensorrt/fp32

# 2. true mixed FP16 (TensorRT 11 removed BuilderFlag.FP16, so precision lives in the graph)
python -m scripts.convert_fp16_onnx \
    --onnx var/model/hansung-p3.onnx --out var/model/hansung-p3-fp16.onnx
python -m scripts.verify_tensorrt_gpu \
    --onnx var/model/hansung-p3.onnx --fp16-onnx var/model/hansung-p3-fp16.onnx \
    --samples-dir samples --precisions fp16 --out docs/evidence/tensorrt/true_fp16
```

On this GPU-less machine the same first command exits `3` with
`BLOCKED_BY_NVIDIA_HARDWARE` and writes `blocked.json` **instead of** benchmarking the
CPU, and `--preflight` exercises the whole harness except the GPU.
