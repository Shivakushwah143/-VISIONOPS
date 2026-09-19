# Tesla T4 (x86_64 host) vs physical Jetson — comparison frame

Status of this document: **the T4 column is real measured evidence; the Jetson column is
empty and stays empty until a physical device produces it.** Nothing here predicts Jetson
numbers, and no cell is filled by scaling a T4 measurement. A missing cell is written
`PENDING PHYSICAL RUN`, not estimated.

Both sides must use the **same qualified artifact** — FP32 ONNX
`b239aa7e881fc590633944cf37df46ed477f3a819d09ec7a7bdb19d052ca2422`, contract
`hansung_ppe_yolov8n_10`, input `[1,3,640,640]`, output `[1,14,8400]` — and the same
canonical decoder, or the comparison says nothing about optimisation and everything about
a changed model.

---

## 1. What is comparable

| Axis | Tesla T4 (verified) | Physical Jetson (pending) |
| --- | --- | --- |
| Host class | x86_64 temporary cloud GPU | ARM64 edge SoC, `nvidia_jetson_orin_arm64` profile |
| GPU | Tesla T4 | PENDING PHYSICAL RUN |
| Driver | 580.82.07 | from JetPack / L4T, PENDING PHYSICAL RUN |
| CUDA | 12.8 | PENDING PHYSICAL RUN |
| TensorRT | 11.3.0.99 | PENDING PHYSICAL RUN |
| Model SHA-256 (FP32) | `b239aa7e…2422` | must equal the same hash |
| Input shape | `[1,3,640,640]` | must equal the same shape |
| Canonical contract | `hansung_ppe_yolov8n_10`, sources 0/2/5 | must be the same contract version |
| Parity samples | 6 frames, 24 reference detections | the same 6 frames, shipped in the bundle |

Comparable **because they are the same quantity**: TensorRT FP32 latency on the same
graph, model-only throughput at that latency, and detection parity against the same
reference runtime on the same frames.

## 2. Measured results

| Runtime | Precision | Host | p50 ms | p95 ms | Mean ms | Model-only FPS | Parity |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| ONNX Runtime CPU | FP32 | cloud CPU | 116.825 | 176.232 | 126.724 | 7.89 | reference |
| TensorRT | FP32 | Tesla T4 | 4.658 | 6.479 | 4.834 | 206.87 | 24/24, min IoU 1.0 |
| TensorRT | true mixed FP16 | Tesla T4 | 9.016 | 9.434 | 9.1 | 109.89 | 24/24, min IoU 0.9922 |
| TensorRT | FP32 | Jetson | PENDING PHYSICAL RUN | PENDING PHYSICAL RUN | PENDING PHYSICAL RUN | PENDING PHYSICAL RUN | PENDING PHYSICAL RUN |
| TensorRT | true mixed FP16 | Jetson | PENDING PHYSICAL RUN | PENDING PHYSICAL RUN | PENDING PHYSICAL RUN | PENDING PHYSICAL RUN | PENDING PHYSICAL RUN |

Source: `docs/evidence/tensorrt/final/FINAL_GPU_EVIDENCE_SUMMARY.json`. All figures are
**MODEL-ONLY**: inference on a fixed `[1,3,640,640]` tensor, CUDA synchronised, warmup
excluded. They exclude RTSP, network, decode, tracking, temporal analysis and event
handling, so they are higher than achievable camera FPS. They are not end-to-end FPS and
must not be quoted as such.

A superseded run in this repository showed ~209 model-only FPS under an `fp16` label whose
engine input dtype was actually `FLOAT`. It is excluded here and marked
`SUPERSEDED — LOGICAL FP16 LABEL, FP32 GRAPH/INPUT` in
`docs/evidence/tensorrt/final/superseded/`.

## 3. One finding the T4 run produced that is not a comparison

True mixed FP16 was **slower** than FP32 on the T4 for this graph: 9.1 ms mean against
4.834 ms. This is recorded as an engineering finding, not as a defect and not as a claim
about Jetson. Plausible contributors — mixed-precision cast overhead, TensorRT tactic
selection, operations kept in higher precision, strongly-typed graph structure — are
**unconfirmed hypotheses**; nothing here says which, and profiling would be required
before asserting one. The defensible conclusion is narrower and worth stating in an
interview as-is:

> FP16 did not improve latency for this model in this environment; precision has to be
> profiled per graph and per device rather than assumed.

Do not carry that result across to Jetson. The tactic selection, SM count, memory
bandwidth and TensorRT build are different, and on an edge SoC FP16 is often the better
choice for power and bandwidth reasons that a latency number on a T4 cannot show.

## 4. What the comparison will and will not mean once both columns exist

It will mean: for this specific graph, contract and TensorRT version, here is the
model-only inference cost on a datacenter GPU and on an edge SoC.

It will not mean "Jetson is better/worse than a T4", and it cannot be reported as such,
because the devices are not substitutes:

| Dimension | Tesla T4 | Jetson (Orin class) |
| --- | --- | --- |
| Deployment location | rack, mains power, active cooling | on the camera, battery/POE, passively or fan cooled |
| Power envelope | ~70 W board | single-digit to ~25 W module |
| Memory | 16 GB GDDR6, discrete | shared LPDDR5 unified with CPU |
| Bandwidth | ~320 GB/s | a fraction of that, shared with the CPU |
| Thermal headroom | sustained by a chassis | bounded by the enclosure and ambient temperature |
| Decode | NVDEC present, not the point of the platform | NVDEC is a first-class reason to use the platform |
| Role | offline batch, multi-stream aggregation, qualification | real-time per-camera inference at the edge |

The honest framing is a **latency/power/bandwidth trade**, not a ranking. A model-only
latency on a Jetson is also only half its story: the same silicon decodes the video that a
T4 host would need a CPU or a separate NVDEC to feed, and the deployment-level FPS that
matters includes decode, tracking and temporal analysis — which is exactly what
`video/video-pipeline.json` measures and the model-only table does not.

## 5. How the Jetson column gets filled

Nothing in this repository fills it by inference. It is filled by running the bundle:

```bash
unzip visionops-jetson-validation.zip && cd visionops-jetson-validation
./run_jetson_validation.sh
```

on a real Jetson, returning `visionops-jetson-evidence.zip`, and copying
`fp32/benchmark.json`, `fp16/benchmark.json` and the two `parity.json` files into
`docs/evidence/jetson/`. The T4 figures above are never overwritten by that run, and the
Jetson figures are never copied from it.

Also out of scope for this comparison, and still unverified either way: NVDEC hardware
decode (`video/hardware-decode.json` decides), DeepStream runtime, sustained thermal
behaviour, and power measurement.
