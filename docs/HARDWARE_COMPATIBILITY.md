# Hardware compatibility

**BLOCKED:** NVIDIA runtime verification. Actual detection found no `/dev/nvidia*`, `nvidia-smi`, `nvcc` or `gst-launch-1.0`. There is no observed GPU model, driver/CUDA/TensorRT/DeepStream tuple, compute capability, engine conversion result, FPS, VRAM, GPU latency or utilization.

CPU component runtime was VERIFIED on x86_64 Linux with ONNX Runtime CPUExecutionProvider and OpenCV. The actual Python/Node versions were 3.12.14/24.19.0. The locked intended container versions remain Python 3.11 and Node 22; those images were not executed here. CPU arm64 and GPU profiles were not qualified.

`edge/pipeline/deepstream/parser.cpp`, `nvinfer.txt` and `bridge.py` are IMPLEMENTED — NOT RUNTIME VERIFIED. A standalone GPU launcher was added and is IMPLEMENTED — NOT RUNTIME VERIFIED; agent GPU-profile selection/recovery remains incomplete. A real NVIDIA host must compile the parser against its DeepStream SDK, export/convert the actual canonical model, exercise tracking/metadata, and record parity/performance evidence before that profile is eligible.
