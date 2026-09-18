# NVIDIA adapter

BLOCKED — this environment has no NVIDIA device nodes, NVIDIA driver, CUDA, TensorRT or DeepStream. No engine or GPU performance result was generated.

`parser.cpp` implements canonical YOLO11 tensor parsing for three classes. `nvinfer.txt` defines RGB normalization, symmetric padding, FP16, class-aware NMS and model/engine paths. `bridge.py` converts DeepStream/NvDCF metadata to normalized detections. These files are IMPLEMENTED — NOT RUNTIME VERIFIED. Their SDK ABI compatibility and coordinate mapping require hardware validation. `run.py` now supplies a standalone GStreamer launcher and durable SQLite event handoff; `Makefile` builds the custom parser against a supplied SDK. Source recovery and fleet GPU-profile integration remain incomplete. The path was not run.

On a compatible host, first qualify GPU model/compute capability, driver, CUDA, TensorRT and DeepStream versions. Compile the parser against that SDK, generate the engine on the target, inspect parser shapes and mapped coordinates, and compare real labeled PPE outputs against CPU ONNX before release approval. Never reuse an engine across unqualified profiles.

Continuation: `run.py` now provides a standalone file/RTSP → nvinfer → NvDCF → rule → SQLite launcher. `Makefile` compiles the parser when an SDK is available. Full reconnect/session recovery and fleet GPU-profile selection remain incomplete. No hardware verification is claimed.
