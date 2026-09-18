# 07 — Locked technology stack

Technology choices are binding. Exact package patch versions must be resolved, installed and locked during Phase 1; this specification does not invent a validated dependency combination. Use lockfiles and digest-pinned runtime images, never `latest` for critical releases. NVIDIA versions form one compatibility tuple qualified in Phase 4, not independently upgraded libraries.

| Technology | Responsibility and reason | Priority | Without NVIDIA |
| --- | --- | --- | --- |
| React + TypeScript | Typed operator UI and API models | P0 | Same |
| Vite | Frontend development/build | P0 | Same |
| Tailwind CSS | Consistent responsive styling | P0 | Same |
| FastAPI + Pydantic | Validated REST/device API and OpenAPI | P0 | Same |
| Python 3.11 | Backend/agent/training baseline; isolate environments | P0 | Same; GPU bridge may use SDK runtime Python separately |
| SQLAlchemy 2 + psycopg 3 + Alembic | PostgreSQL access and migrations | P0 | Same |
| PostgreSQL 16 | Transactions, registry, events, campaign persistence | P0 | Same |
| SQLite WAL | Durable single-agent local outbox and state | P0 | Same |
| PyTorch + Ultralytics YOLO11n architecture | Small custom 3-class PPE training/reference model | P0 | CPU training/evaluation possible but slower; license/data review required |
| DVC | Immutable dataset manifests and pipeline | P0 | Same |
| MLflow | Experiments, model registry and provenance | P0 | Same |
| ONNX + ONNX Runtime | Export format and real portable CPU inference | P0 | Primary fallback |
| TensorRT FP16 | GPU inference artifact for qualified profile | Hardware-dependent P0 | ONNX Runtime CPU; report blocked GPU verification |
| TensorRT INT8 | Optional quantization with calibration/accuracy gates | P1 | Skip explicitly |
| DeepStream + GStreamer + NVDEC | Reuse NVIDIA video pipeline and hardware decoding | Hardware-dependent P0 | FFmpeg software decode + OpenCV |
| ByteTrack / DeepStream NvDCF | CPU / GPU person tracking respectively | P0 / hardware-dependent P0 | ByteTrack |
| FFmpeg + OpenCV | File/RTSP decode fallback, preprocessing, local preview | P0 | Primary |
| Docker + Compose | Single-node infrastructure and active-agent simulation | P0 | CPU profile requires no NVIDIA runtime |
| Prometheus + Grafana | Real metrics collection, dashboards, alert rules | P0 | GPU metrics unavailable; system metrics real |
| JSON structured logs | Event/agent/deployment diagnosis | P0 | Same |
| OpenTelemetry SDK | API/agent span and trace context instrumentation | P0 | Console exporter default, sampled |
| Loki + Grafana Alloy | Central log storage/forwarding | P1 | Optional Compose profile |
| Tempo + OTel Collector | Central distributed trace storage | P1 | Console traces in P0 |
| Nginx | Same-origin frontend/API TLS boundary | P0 | Same |
| cryptography / Argon2 library | Ed25519 artifact verification / user password hashing | P0 | Same |
| GitHub Actions | Build, AI eval orchestration, dependency/security checks, clean packaging | P0 | CPU lane; GPU job only on compatible runner |

No MongoDB, Qdrant, LLM provider, Kubernetes, K3s deployment, Kafka or Redis in P0. K3s is a conceptual future choice only if a site genuinely needs a cluster. No hosted AI service credentials are required.

## Runtime profiles

`cpu`: API, controller, PostgreSQL, MLflow, proxy/UI, one real edge worker, Prometheus and Grafana. `simulation`: CPU control services plus 10 simulated agents; each has distinct persistent identity/state. `nvidia`: replaces the real video worker with a validated DeepStream image on Linux NVIDIA host; it does not emulate a GPU. `observability-extra`: P1 Loki/Alloy/Tempo/Collector.

Development targets Linux containers via Docker Engine or Windows Docker Desktop with WSL2 for CPU. NVIDIA support requires explicit host/container compatibility and is not assumed on Windows. Baseline planning budget: 4 CPU cores, 16 GB RAM, 20 GB free disk for a small demo excluding training datasets; measure actual usage. Train separately to avoid starving the demo.

## Environment contract for later .env.example

| Variable | Required/profile | Format and source |
| --- | --- | --- |
| VISIONOPS_ENV | all | local or deployment; local binds loopback |
| DATABASE_URL | central | PostgreSQL DSN; setup-generated app credential |
| MLFLOW_TRACKING_URI | backend/training | private MLflow base URL |
| MLFLOW_DATABASE_URL | MLflow | separate PostgreSQL database/user |
| ARTIFACT_ROOT, EVIDENCE_ROOT | central | absolute mounted directories |
| SESSION_SECRET | central | setup-generated ≥32 random bytes, secret |
| CSRF_SECRET | central | independent setup-generated random secret |
| DEVICE_TOKEN_PEPPER | central | random secret for token hashing |
| ENROLLMENT_TOKEN_PEPPER | central | independent random secret |
| ARTIFACT_SIGNING_KEY_PATH | release CLI only | protected Ed25519 private-key path |
| ARTIFACT_VERIFY_KEY_PATH | API and agents | pinned public-key file |
| CONTROL_PLANE_URL | agents | HTTPS base URL; loopback HTTP only local profile |
| DEVICE_ID, DEVICE_CREDENTIAL_PATH | enrolled agent | returned ID and mode-0600 token file |
| EDGE_STATE_DIR, EDGE_MEDIA_ROOT | agent | writable state and read-only media mount |
| EDGE_PROFILE | agent | cpu_onnx or nvidia_deepstream; simulation is device mode |
| RTSP_ALLOWED_HOSTS | edge | explicit host/IP allowlist provisioned by operator |
| RTSP_CREDENTIALS_FILE | RTSP edge only | local secret mapping; optional for anonymous feeds |
| TLS_CERT_PATH, TLS_KEY_PATH | proxy deployment | valid certificate/key paths |
| PROMETHEUS_URL | backend | private Prometheus URL for central metrics |
| OTEL_EXPORTER_OTLP_ENDPOINT | P1 only | collector endpoint; omit for console exporter |
| SIMULATOR_DEVICE_COUNT | simulation | integer 10–50, default 10 |
| SEED_INVENTORY_COUNT | seed CLI | integer 0–8000, default 8000 |

Frontend uses relative `/api/v1`; no secrets or backend service URLs in Vite variables. Initial users are created by an interactive bootstrap command; no permanent default password. Thresholds belong to ConfigVersion, not scattered environment variables. Additional environment names require a recorded decision and synchronized documentation.

## Tooling and profile qualification

Use Node.js 22 with npm for frontend package-lock, and uv for Python lock/install; resolve exact installed tool patch versions during Phase 1. Training dependency environment remains separate when required. P0 hardware_profile selection is an immutable compatibility label chosen at registration; do not silently change an enrolled NVIDIA device to CPU. Use a separately registered CPU device identity if demonstrating fallback after a profile mismatch.

Release gate policy changes are MLOps-reviewed configuration changes in source, versioned with the application and recorded under decisions, not arbitrary client-submitted thresholds. ConfigVersion carries the selected approved policy values. No generic settings UI/API is implied.
