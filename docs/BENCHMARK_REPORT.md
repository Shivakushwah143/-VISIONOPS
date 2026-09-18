# Benchmark report

**BLOCKED:** Real PPE CPU benchmark and all NVIDIA/TensorRT benchmarks.

No locally verified trained PPE weights or labeled video were available. There is no valid model-quality, sustained throughput, end-to-end event latency, VRAM or GPU-utilization result. The specification's CPU FPS/latency and quality values are targets, not results.

The synthetic engineering experiment processed 23 frames and emitted one durable event. `docs/evidence/component-runtime.json` contains its elapsed time and ONNX call durations. That model has constant outputs and performs no trained detection; those timings must not be compared with real model performance or used to pass a release gate.

The actual environment was x86_64 Linux, Python 3.12.14, Node 24.19.0 and FFmpeg 6.1.1. ONNX Runtime used CPUExecutionProvider. NVIDIA device nodes/runtime commands were absent. Exact environment evidence is in `environment.json`.

The detector evaluation CLI writes measured local results when supplied a real model and annotated samples. It explicitly leaves temporal metrics and full qualification incomplete. Warmup/timed repetitions, 30-minute sustained runs, event accuracy and full parity work remain required before a real release can be approved.
