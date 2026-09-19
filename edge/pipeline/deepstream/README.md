# NVIDIA / DeepStream adapter

**BLOCKED BY HARDWARE.** This environment has no NVIDIA device nodes, driver,
CUDA, TensorRT or DeepStream. No engine file exists and no GPU number is
reported anywhere in this repository.

## What changed

The parser previously hardcoded a **3-class / 7-channel** tensor with a literal
`perClassPreclusterThreshold[cls]` loop over classes 0..2, while the qualified
Hansung artifact is a **10-class / 14-channel** head (`[1, 14, 8400]`) whose
canonical classes are source 5 (Person), source 0 (Hardhat) and source 2
(NO-Hardhat). CPU worked because its adapter filtered the real head; the
DeepStream path would have silently produced wrong classes and no detections.

Everything class-related is now **generated** from `shared/model_contract.py`,
the same module the CPU `Detector` decodes with:

| File | Role |
| --- | --- |
| `visionops_contract.h` | generated: raw channel count, mapped source indices, canonical ids, thresholds |
| `visionops_contract.json` | generated: machine-readable contract, consumed by `bridge.py` and the mapping version |
| `nvinfer.txt` | generated: `num-detected-classes` = full source head, not the canonical subset |
| `parser.cpp` | consumes the generated header; accepts either tensor layout, emits canonical ids |
| `bridge.py` | validates `class_id` against the generated canonical class count |
| `run.py` | file/RTSP -> nvinfer -> NvDCF -> `edge.temporal` analyzers -> durable SQLite outbox |

Regenerate with `python -m scripts.gen_deepstream_contract`; `Makefile` owns a
`contract` target and `scripts/verify_model_contract.py` fails on stale output.

## Status

- **IMPLEMENTED — NVIDIA RUNTIME NOT VERIFIED:** configuration, generated
  contract, parser source, bridge and launcher.
- **BLOCKED BY HARDWARE:** SDK ABI compile, engine generation, coordinate
  mapping parity against the CPU letterbox path, tracker behaviour and any
  latency/FPS/GPU utilization number.
- **VERIFIED (static only):** the generated header, `nvinfer.txt` and
  `bridge.py` agree with the canonical contract, and the same contract reproduces
  the qualified CPU mapping.

## Qualifying it on a real host

1. Record GPU model/compute capability, driver, CUDA, cuDNN, TensorRT and
   DeepStream versions; never reuse an engine across unqualified profiles.
2. `make contract && make` against that SDK.
3. Export/convert the actual canonical model and generate the engine on the target.
4. Compare real labeled PPE output frame-by-frame against the CPU ONNX path
   (same IoU/class/confidence tolerances as the PT↔ONNX parity check).
5. Only then may a `nvidia_jetson_tensorrt_arm64` release be marked qualified.
