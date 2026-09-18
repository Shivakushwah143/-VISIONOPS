# 13 — Model evaluation and benchmark plan

No measurements have been executed for this specification. Numeric criteria below are proposed approval policies. Record actual results with evidence; fail or block unmet gates rather than filling a table with plausible numbers.

## Data and learning protocol

Canonical labels person, helmet, no_helmet; bare-head evidence must be explicitly annotated. Dataset manifest records source URL/owner, license, hashes, camera/site/session, visibility and day/night tags. Split 70/15/15 by independent site or recording session, not adjacent frames. Deduplicate before splitting. Keep evaluation partition frozen across champion/challenger and optimization comparison; fit calibration and thresholds on train/validation only. Document class support and underrepresented slices. For portfolio approval require at least 100 labeled no-helmet instances and 50 positive plus 50 negative event windows across ≥3 independent sessions; insufficient support gives BLOCKED, not a statistically robust claim. Small training smoke runs are plumbing evidence only.

## Detection metrics from first principles

IoU = intersection area / union area. At a declared score threshold and IoU match threshold, one-to-one matching yields TP, FP and FN. Precision=TP/(TP+FP), recall=TP/(TP+FN), F1=2PR/(P+R). Undefined denominators are null with support count. AP integrates the precision-recall curve for one class; mAP@50 averages class AP at IoU 0.50; mAP@50:95 averages across IoU 0.50–0.95 in 0.05 steps. Report per-class precision/recall/AP, aggregate mAP, confusion matrix and class counts. Do not equate a confidence score with calibrated correctness probability.

For the actual safety workflow evaluate event precision/recall and duplicate alerts per track-minute. Match predicted event to labeled no-helmet interval in the same camera/session if temporal overlap exists, onset delay ≤5 seconds, and associated person identity matches annotation; one prediction per labeled interval counts TP, extra predictions count duplicates (report cooldown effects). Include compliant heads, occlusion, small heads, crowded scenes and truncated persons. Tracking: ID switches and lost-track counts; optional IDF1 if full tracking labels exist. Frame AP alone is not safety-event recall.

## Promotion policy

| Gate | Proposed target | Evidence needed |
| --- | --- | --- |
| no_helmet recall at IoU .50 and score .35 | ≥0.90 | Held-out labeled instances and support |
| no_helmet precision at same point | ≥0.80 | Same evaluation set |
| Overall mAP@50 | ≥0.70 | All 3 classes, per-class values included |
| Overall mAP@50:95 | ≥0.40 | Same classes/split; report localization limitation |
| Event recall / precision | ≥0.90 / ≥0.80 | Annotated temporal windows and match log |
| Optimization regression | ≤0.02 absolute loss in no_helmet recall and mAP@50 versus PyTorch reference | Paired fixed evaluation set |
| Profile performance | p95 inference ≤approved profile budget; sustained inferred FPS ≥profile minimum | Actual end-to-end workload measurement |
| Memory/stability | Peak usage within host profile budget; no OOM/crash over 30 minutes | Resource timeline and logs |

For initial CPU profile propose 1 stream, 640×640 input, target 5 inferred FPS and p95 inference ≤200 ms, process RSS ≤4 GiB; these may be unmet on available hardware. If unmet, record failure and explicitly revise the workload policy with rationale and repeat evaluation; do not report it passed. NVIDIA budget must be set after target hardware inventory and before benchmark, with VRAM ceiling ≤80% of available VRAM; record exact bytes. Accuracy gates cannot be silently loosened for demo. A release with failed/blocked real evidence cannot go to real devices. Simulation-only releases may use deterministic evaluation fixtures labeled simulated and only deploy to simulated devices; that demonstrates control mechanics, not model quality.

## Fair comparison matrix

| Path | Purpose | Hardware condition |
| --- | --- | --- |
| PyTorch FP32 | Reference predictions and latency | CPU or supported GPU; compare on same device when measuring optimization |
| ONNX Runtime FP32 | Portable production fallback and export parity | CPU mandatory; GPU provider optional, separately reported |
| TensorRT FP16 | Qualified NVIDIA artifact | Compatible physical NVIDIA GPU/runtime required |
| TensorRT INT8 | Optional quantization | P1; representative train-only calibration set and compatible hardware |

Fix weights, dataset, preprocessing, shape, batch, score/NMS thresholds, device, power mode, versions and worker concurrency. Use 100 warm-up inferences (record warmup separately), then ≥1,000 timed samples and a 30-minute sustained-video run. Run 3 repetitions and publish variation. CUDA work is asynchronous: synchronize or use CUDA events correctly; report transfers and preprocessing separately. No CPU-versus-GPU speedup framed as a TensorRT-only benefit. Benchmark model-only and full decode→inference→tracking→rule pipeline separately; include startup/export/engine-build time outside steady-state latency.

Record p50/p95 raw latency distribution, throughput samples/s, camera input FPS, inferred FPS, decode latency, dropped frames, queue age, GPU utilization/VRAM/temperature if available and CPU/RAM. Compare one stream first, then 2/4 if supported without stale backlog. Stop overload run safely on memory/thermal limits and record failure. INT8 uses a disjoint representative calibration manifest and identical downstream scoring; a faster but less accurate engine is rejected.

## TOPS and camera capacity

TOPS means tera operations per second at a specified precision/sparsity/power condition. It is an accelerator peak capability, not FPS or camera count. Camera resolution/FPS/codec/bitrate determine ingestion and decoding load; decode capacity is separate from inference compute. Actual camera capacity is bounded by decode, preprocessing, memory transfer, inference, tracking and thermal stability. Jetson-class devices are a conceptual option; a remembered 220–260 TOPS figure alone cannot size this workload.

## Evidence artifact

Write raw per-sample timing/prediction outputs, labeled match records, hardware inventory, software lock hashes, dataset and model IDs, command, seed, date, success/failure and logs to MLflow artifacts. EvaluationReport stores summary plus evidence_ref and policy snapshot. Use independent axes: implementation status, runtime verification, evidence_mode real/simulated, and hardware verification. Hardware unavailable → BLOCKED; never synthesize GPU sensor or TensorRT results. These are AI evaluations and runtime workload verification, without conventional application test frameworks.
