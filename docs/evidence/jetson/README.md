# Physical Jetson evidence

Status: **no physical Jetson evidence exists yet.** This directory holds the bundle
manifest and the comparison frame. Results appear here only after a real device runs the
bundle and its evidence ZIP comes back.

```
docs/evidence/jetson/
  README.md              this file
  T4_VS_JETSON.md        real T4 column, explicitly pending Jetson column
  bundle-manifest.json   what was shipped, with every SHA-256 and the post-build audit
```

After a physical run, the returned archive unpacks to:

```
jetson_environment.json    real Tegra / JetPack / L4T / CUDA / TensorRT record
fp32/{parity,benchmark,engine-metadata}.json
fp16/{parity,benchmark,engine-metadata}.json
video/video-pipeline.json  decode -> preprocess -> TensorRT -> ByteTrack -> temporal rule -> event
video/hardware-decode.json GStreamer/NVIDIA plugin inspection; decides the NVDEC claim
sustained/{sustained_run.json,tegrastats.log}
telemetry-baseline/
deepstream/{result.json | NOT_AVAILABLE.json}
rtsp/{reconnect.json | NOT_VERIFIED.json}
phases.json                every phase: command, exit code, stdout/stderr tails, duration
status-patch.json          the documentation claims this run authorises, with evidence behind each
```

## The status model

| Claim | State | Decided by |
| --- | --- | --- |
| TensorRT on a real NVIDIA GPU (Tesla T4, x86_64) | VERIFIED | `docs/evidence/tensorrt/final/` |
| TensorRT FP32 / true mixed FP16 on that GPU | VERIFIED | same |
| Physical Jetson | NOT VERIFIED | needs a returned `jetson_environment.json` with `PHYSICAL_JETSON_CONFIRMED` |
| JetPack on physical hardware | NOT VERIFIED | same |
| ARM64/aarch64 runtime execution | NOT VERIFIED | same |
| TensorRT on Jetson | NOT VERIFIED | needs `tensorrt/*/parity.json` VERIFIED **and** an engine whose own dtypes back the precision label |
| NVDEC / hardware decode | NOT VERIFIED | needs `video/hardware-decode.json` = `HARDWARE_DECODE_RUNTIME_VERIFIED` |
| DeepStream runtime | NOT VERIFIED | needs `deepstream/result.json` with `inference_executed: true` |
| RTSP on Jetson | NOT VERIFIED | needs a real `rtsp/reconnect.json` |
| Jetson thermal / power qualification | NOT VERIFIED | a `tegrastats` window is telemetry, not qualification |
| 10K physical Jetson fleet | NOT VERIFIED | requires 10000 real devices |

The x86 T4 result is a separate host and a separate evidence directory. It is never reused
as Jetson evidence, and no document may assert a Jetson claim from it.

## Reproducing

```bash
# build the bundle locally (no Jetson needed, nothing is claimed)
python -m scripts.make_jetson_bundle --onnx var/model/hansung-p3.onnx \
    --contract-record var/model/hansung-p3.json --video var/media/ppe-2.mp4

# on a real aarch64 Jetson
unzip var/bundle/visionops-jetson-validation.zip
cd visionops-jetson-validation && ./run_jetson_validation.sh
```

On any non-Jetson host the orchestrator exits **3** (`BLOCKED_NOT_PHYSICAL_JETSON`) after
writing `blocked.json`, and produces no capability claim. That is the designed outcome on
this development machine, not a failure.

See `RUN_ON_JETSON.md` for the on-device procedure and the dependency policy that protects
the JetPack-provided CUDA/cuDNN/TensorRT stack.
