# Hardware compatibility

> **Continuation 2 update.** Device targets are now an explicit matrix rather than one
> hardcoded literal: `cpu_onnx_x86_64`, `cpu_onnx_arm64`, `nvidia_jetson_tensorrt_arm64`
> (`shared/hardware_profiles.py`), with reason codes, a locally simulated-target path that
> must carry `simulated_hardware: true`, and `GET /api/v1/hardware-profiles`. CPU telemetry
> is real; GPU telemetry is reported only when a provider genuinely answers, otherwise
> `gpu_metrics_available: false` with `gpu: null`. The DeepStream parser/config divergence
> for the 10-class/14-channel artifact is fixed and statically verified. See
> [CURRENT_VERIFIED_STATE.md](CURRENT_VERIFIED_STATE.md) and [JETSON_DEPLOYMENT_TARGET.md](JETSON_DEPLOYMENT_TARGET.md).

**Blocked on this host, verified on a real GPU elsewhere.** Local detection finds no `/dev/nvidia*`, `nvidia-smi`, `nvcc` or `gst-launch-1.0`, so nothing NVIDIA-related can run here. The TensorRT runtime was therefore qualified on an external **Tesla T4** (driver 580.82.07, CUDA 12.8, TensorRT 11.3.0.99, compute capability 7.5): FP32 and a true ModelOpt mixed-FP16 engine, both parity-checked, with model-only latency and throughput measured. Evidence: [docs/evidence/tensorrt/final](evidence/tensorrt/final/README.md).

Still **NOT MEASURED** anywhere: VRAM usage, GPU utilization, power and TOPS — the GPU run recorded `gpu_memory: NOT MEASURED` and no utilization provider was queried. Still **NOT VERIFIED**: physical Jetson, JetPack, ARM64 execution, NVDEC and the DeepStream runtime.

CPU component runtime was VERIFIED on x86_64 Linux with ONNX Runtime CPUExecutionProvider and OpenCV. The actual Python/Node versions were 3.12.14/24.19.0. The locked intended container versions remain Python 3.11 and Node 22; those images were not executed here. CPU arm64 and GPU profiles were not qualified.

`edge/pipeline/deepstream/parser.cpp`, `nvinfer.txt` and `bridge.py` are IMPLEMENTED — NOT RUNTIME VERIFIED for the DeepStream runtime: no DeepStream SDK existed on the local host or on the GPU host, so the parser was never compiled or executed there. Their correctness is enforced statically instead — the contract header, `nvinfer.txt` and the metadata bridge are *generated* from `shared/model_contract.py` so the 10-class/14-channel artifact can no longer diverge from a 3-class assumption, and `scripts/gen_deepstream_contract.py` regenerates them.

What the GPU run *did* establish, on the TensorRT path rather than the DeepStream path: the qualified ONNX builds a correct engine, its raw output decodes through the canonical contract to the same detections as the CPU path (24/24, IoU 1.0), a global argmax would have re-interpreted 908 of 1827 boxes, and the mixed-FP16 conversion preserves behaviour (min IoU 0.9955). A real Jetson must still compile the parser against its DeepStream SDK, exercise tracking/metadata, and record parity/performance evidence on that target before the Jetson profile is eligible; a T4 is not a Jetson.
