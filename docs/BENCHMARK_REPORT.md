# Benchmark report

> **Continuation 2 update.** A short functional run on the real artifact now exists:
> 39 inference frames at the 5 FPS production throttle over the qualified clip,
> CPU p50 96.8 ms / p95 194.6 ms, plus a PT→ONNX graph-parity measurement
> (24/24 at conf 0.35, 159/159 at 0.001, min IoU 1.0, max raw tensor delta 0.003067).
> Evidence: `docs/evidence/edge-platform-runtime.json` and
> `docs/evidence/onnx-export-parity.json`. This is **not** a sustained throughput,
> end-to-end event-latency, VRAM or GPU-utilization benchmark, and no such number is
> claimed. NVIDIA/TensorRT benchmarks now exist, measured on a real **Tesla T4** with
> TensorRT 11.3.0.99 (see the table below). They are **model-only** figures and exclude
> RTSP, decode, preprocessing, tracking, temporal analysis and event handling.
>
> | Runtime | Precision | Host | p50 ms | p95 ms | Mean ms | Model-only FPS | Parity |
> | --- | --- | --- | ---: | ---: | ---: | ---: | --- |
> | ONNX Runtime CPU | FP32 | GPU host CPU | 116.825 | 176.232 | 126.724 | 7.89 | reference |
> | TensorRT | FP32 | Tesla T4 | 4.658 | 6.479 | 4.834 | 206.87 | 24/24, IoU 1.0 |
> | TensorRT | true mixed FP16 | Tesla T4 | 9.016 | 9.434 | 9.1 | 109.89 | 24/24, min IoU 0.9922 |
>
> True mixed FP16 was **slower** than FP32 for this graph on this GPU; no cause is
> asserted, because none was profiled. A superseded run that labelled an FP32 engine
> `fp16` (~4.78 ms, ~209 FPS) is deliberately **excluded** from this table and kept,
> labelled, in `docs/evidence/tensorrt/final/superseded/`.

**PARTIAL:** the NVIDIA/TensorRT benchmarks are measured (table above,
`docs/evidence/tensorrt/final/`). Still absent: any **sustained** real-PPE CPU benchmark,
VRAM utilisation, GPU utilisation, power or TOPS measurement — the GPU run recorded
`gpu_memory: NOT MEASURED` — and any model-quality benchmark, because no labeled dataset
exists.

No locally verified trained PPE weights or labeled video were available. There is no valid model-quality, sustained throughput, end-to-end event latency, VRAM or GPU-utilization result. The specification's CPU FPS/latency and quality values are targets, not results.

The synthetic engineering experiment processed 23 frames and emitted one durable event. `docs/evidence/component-runtime.json` contains its elapsed time and ONNX call durations. That model has constant outputs and performs no trained detection; those timings must not be compared with real model performance or used to pass a release gate.

The actual environment was x86_64 Linux, Python 3.12.14, Node 24.19.0 and FFmpeg 6.1.1. ONNX Runtime used CPUExecutionProvider. NVIDIA device nodes/runtime commands were absent. Exact environment evidence is in `environment.json`.

The detector evaluation CLI writes measured local results when supplied a real model and annotated samples. It explicitly leaves temporal metrics and full qualification incomplete. Warmup/timed repetitions, 30-minute sustained runs, event accuracy and full parity work remain required before a real release can be approved.
