# 02 — Research and reuse strategy

Research checked on 2026-09-12 against official documentation. These findings support component selection; they are not a verified dependency lock or benchmark. Rolling documentation can change; the implementer must record exact installed versions and compatibility evidence before GPU execution.

## Evidence and decisions

| Component | Verified source and finding | Project decision |
| --- | --- | --- |
| DeepStream | [NVIDIA installation guide](https://docs.nvidia.com/metropolis/deepstream/dev-guide/text/DS_Installation.html) describes an accelerated video analytics SDK with platform-specific setup | Reuse decode, batching, inference and tracking infrastructure; bind to one verified NVIDIA container/platform tuple |
| TensorRT | [NVIDIA support matrix](https://docs.nvidia.com/deeplearning/tensorrt/latest/getting-started/support-matrix.html) documents compatibility and engine portability constraints | Build and qualify engines per target compatibility profile; never assume an engine is universal |
| YOLO export | [Ultralytics export documentation](https://docs.ultralytics.com/modes/export/) supports ONNX and TensorRT export options | Train a small PPE detector, export ONNX, compare numerical and task-level results before optimization |
| Model registry | [MLflow registry documentation](https://mlflow.org/docs/latest/ml/model-registry/) provides registered model versions and aliases | MLflow owns experiment/model provenance; VisionOps pins immutable version IDs and owns deployment authorization |
| Dataset versions | [DVC documentation](https://doc.dvc.org/start) covers tracking data and reproducible pipelines | Git tracks code/metadata, DVC tracks dataset content; media stays out of Git |

## Build versus reuse

Build the FastAPI control plane, device identity and registration, deterministic rules, agent reconciliation, campaign controller, simulator, fleet UI, lineage views, and telemetry integration. Reuse React UI primitives, PostgreSQL transactions, MLflow, DVC, FFmpeg/OpenCV, ONNX Runtime and vendor GPU infrastructure. Reuse ByteTrack on CPU and the DeepStream NvDCF tracker in the NVIDIA profile. Adapt their output to one contract; do not claim identical IDs or quality across trackers.

DeepStream does not provide this product's operator workflow, domain-specific PPE rule, approved desired-state policy, rollout gates, RBAC, simulator or evidence trail. Those are original integration/product work. Do not fork an entire fleet product and present it as original engineering. Preserve upstream attribution and modifications.

## Hardware path and technical risks

1. PPE weights/data may be unavailable or poorly licensed. Acquire an explicit person/helmet/no_helmet taxonomy with documented source and redistribution permission; otherwise training/data readiness is BLOCKED, not fabricated. General COCO person weights cannot prove no-helmet detection.
2. NVIDIA SDK, driver, CUDA, TensorRT, architecture and YOLO output/parser may disagree. Phase 4 freezes a compatibility tuple and evaluates preprocessing, output parsing and NMS equivalence. Failure keeps CPU available and GPU status unverified.
3. RTSP packets can arrive while content freezes. Observe both timing and content; a static scene alone is not definite failure.
4. Confidence drift is not accuracy degradation. Use it to request labeling, not auto-promote models.
5. Fleet records are cheap; concurrent telemetry, artifact bandwidth and reconnect storms are not. Measure active-agent load separately from inventory queries.
6. Mutable model aliases and image tags can break reproducibility. Resolve exact model IDs, file hashes and image digests before approval.

## External verification still required

No PPE dataset or weight download, licensing audit, third-party YOLO DeepStream parser repository review, dependency installation, hardware discovery or throughput measurement was performed for this specification. Before using Ultralytics or redistributing weights, inspect the exact release LICENSE and dataset/model terms; [Ultralytics licensing information](https://docs.ultralytics.com/) is a starting point, not a project-specific legal conclusion. Prefer an openly redistributable portfolio dataset and model; do not assert that noncommercial use automatically removes license obligations.

## Production extension

Object storage/CDN, geographically distributed ingestion, managed secrets, mTLS identity rotation and scalable metrics backends are later extensions justified by measured bottlenecks. Docker Compose and outbound HTTPS suffice for this portfolio's control-plane demonstration.
