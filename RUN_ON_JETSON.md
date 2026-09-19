# Running the VisionOps qualification on a physical NVIDIA Jetson

This bundle is an **input**. It contains no result, no metric and no TensorRT engine.
Everything it produces is produced by the device you run it on.

Expected hardware: Jetson Orin (or Xavier) running a JetPack image, reachable over SSH,
a browser terminal, Jupyter or VNC. `aarch64` is required — on anything else the run
stops at `BLOCKED_NOT_PHYSICAL_JETSON` (exit 3).

---

## 1. Minimum manual steps

```bash
unzip visionops-jetson-validation.zip
cd visionops-jetson-validation
./run_jetson_validation.sh
```

Then take `evidence/visionops-jetson-evidence.zip` off the device and back into the
repository. That is the whole interaction: the wrapper checks the host, installs only
application-level Python packages, and runs the orchestrator.

If `run_jetson_validation.sh` is not executable (some upload paths lose the bit):

```bash
chmod +x run_jetson_validation.sh && ./run_jetson_validation.sh
```

## 2. What to expect, and roughly how long

| Stage | Typical duration on Orin |
| --- | --- |
| environment + JetPack detection | seconds |
| model identity + canonical contract check | under a minute |
| TensorRT FP32 engine build | 1–4 min |
| TensorRT mixed-FP16 engine build | 1–4 min |
| parity (6 frames) + benchmark (`--iterations 100`) | under a minute |
| video pipeline (60 s of the sample clip) | ~1 min |
| sustained run (300 s default) | ~5 min |

Total is roughly 10–20 minutes. The sustained window is application inference, not a
hardware stress test — it does not push the SoC to its thermal limit, and it is not a
thermal qualification. It samples `tegrastats` while real inference runs and reports
what it saw.

## 3. Useful overrides

```bash
SUSTAINED_SECONDS=60 ITERATIONS=20 ./run_jetson_validation.sh          # quick smoke run
RTSP_URL=rtsp://192.168.1.20:8554/ppe ./run_jetson_validation.sh       # real RTSP source
BACKEND=gstreamer ./run_jetson_validation.sh                           # force the GStreamer path
OUT=/tmp/evidence ./run_jetson_validation.sh
```

The module takes the same options directly, which is what the wrapper invokes:

```bash
cd code && python3 -m scripts.verify_physical_jetson \
  --bundle-root .. --samples ../samples --video ../samples/ppe.mp4 \
  --precisions fp32,fp16 --iterations 100 \
  --sustained-seconds 300 --out ../evidence/jetson
```

## 4. Dependency policy — read before installing anything

CUDA, cuDNN and TensorRT on a Jetson come from JetPack and are compiled against that
device's L4T kernel. Desktop or datacenter wheels are not interchangeable with them.

**Do not run any of these:**

```bash
pip install --upgrade tensorrt
pip install nvidia-tensorrt
pip install nvidia-cudnn-cu12      # or any nvidia-*-cuXX wheel
pip install torch                  # ARM64 pulls a CPU-only or wrong-CUDA wheel
pip install onnxruntime-gpu        # ARM64 wheels are not published for every JetPack
```

`run_jetson_validation.sh` installs only the names packaged under the platform in
`requirements/requirements-jetson.txt`, and refuses to pip-install anything matching
`nvidia`, `torch` or `onnxruntime`. If TensorRT is not importable it stops and prints a
blocker rather than trying to fix JetPack from PyPI. For a PyTorch reference on ARM64,
use NVIDIA's own index — and only if you need that path:

```bash
pip3 install --index-url https://pypi.jetson-ai-lab.dev/jp6/cu126 torch
```

## 5. Why the FP16 graph may be regenerated on the device

`model/hansung-p3-fp16.onnx` is only in the bundle when it was supplied at build time.
Otherwise the orchestrator regenerates it on the Jetson with
`code/scripts/convert_fp16_onnx.py` (NVIDIA ModelOpt AutoCast), because the artifact is
not in Git. ModelOpt **strips the Ultralytics ONNX metadata**, including `names`, which
`edge/pipeline.Detector` rejects as `model_missing_class_metadata`; the conversion script
restores it, verifies the taxonomy is unchanged, and writes a conversion record. A
regenerated graph has its own SHA-256, recorded in `fp16-generation.json` — it will not
match the qualified lineage hash, and the evidence says so instead of implying it does.

The FP16 claim itself is never taken from a label: `TensorRTRuntime.precision_label_matches_engine()`
reads the engine's own tensor dtypes, and a run whose engine declares `FLOAT` while the
requested precision says `fp16` is excluded from the authoritative table.

## 6. What the evidence will say

```
evidence/jetson/
  phases.json                  every phase: command, exit code, stdout/stderr tails, duration
  jetson_environment.json      the real Tegra/JetPack/L4T/CUDA/TensorRT record
  model-identity.json          SHA-256 of every artifact vs the qualified lineage
  contract/                    canonical model-contract semantics on this device
  tensorrt/fp32/{parity,benchmark,engine-metadata}.json
  tensorrt/fp16/{parity,benchmark,engine-metadata}.json
  video/video-pipeline.json    decode → preprocess → TensorRT → ByteTrack → temporal rule → event
  video/hardware-decode.json   GStreamer/NVIDIA plugin inspection, and whether it was proven
  telemetry-baseline/          CPU/GPU/RAM/temps before inference
  sustained/{sustained_run.json, tegrastats.log}
  deepstream/{result.json | NOT_AVAILABLE.json}
  rtsp/{reconnect.json | NOT_VERIFIED.json}
  status-patch.json            the exact documentation edits this run authorises
  blocked.json                 only when the host was not a physical Jetson
```

Engines are **not** in the archive. A plan is valid only on the device and TensorRT
version that built it, so it is identified by SHA-256 and size. Pass `--keep-engines` to
the module if you specifically need the binaries.

## 7. If a phase fails

The run does not abort silently and it does not discard evidence. Every phase records its
command, exit code and output tails into `phases.json`, and the archive is packaged even
for a failed run — "it failed at this step, on this hardware, with this error" is a
result. Exit codes:

| Code | Meaning |
| --- | --- |
| 0 | `QUALIFICATION_COMPLETE` |
| 2 | `ENVIRONMENT_INCOMPLETE` — looks like a Jetson, but a prerequisite is missing |
| 3 | `BLOCKED_NOT_PHYSICAL_JETSON` |
| 4 | ran on a Jetson but at least one phase failed |
| 5 | `PARTIAL_JETSON_ENVIRONMENT` (needs `--allow-partial` to proceed) |

The wrapper uses the same codes as the module, so either one can be treated identically by
a caller or a CI step.

## 8. What this run can and cannot authorise

`status-patch.json` is generated by evidence presence, not by optimism. A successful run
may authorise:

```
Physical Jetson: VERIFIED
ARM64/aarch64 runtime execution: VERIFIED
JetPack: VERIFIED
TensorRT on Jetson (fp32 and/or fp16): VERIFIED   — only when the engine's own dtypes back the label
```

It never authorises, regardless of outcome:

```
DeepStream runtime          — only if result.json shows inference_executed
NVDEC / hardware decode     — only if hardware-decode.json shows HARDWARE_DECODE_RUNTIME_VERIFIED
RTSP on Jetson              — only from a real reconnect record
Thermal stability / power   — tegrastats samples a window; that is telemetry, not qualification
10K physical Jetson fleet   — requires 10000 real devices
```

And the x86 Tesla T4 TensorRT result already in the repository is never used as Jetson
evidence: different host, different directory, different claim.

## 9. After the run

Copy back:

```
visionops-jetson-evidence.zip
```

It contains everything needed to update `docs/CURRENT_VERIFIED_STATE.md`,
`docs/JETSON_DEPLOYMENT_TARGET.md`, `docs/interview/11_VERIFIED_VS_UNVERIFIED.md` and
`docs/interview/TENSORRT_INTERVIEW_EVIDENCE.md` from real measurements — `status-patch.json`
already names the exact claims and the evidence file behind each one.
