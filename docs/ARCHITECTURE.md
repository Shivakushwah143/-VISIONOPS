# Implemented architecture

> **Continuation 2 update.** Frame ingestion and behaviour now have their own owners:
> `edge/video.py` owns the `VideoSource` contract (OpenCV/FFmpeg and GStreamer backends,
> bounded latest-frame queue, RTSP reconnect with backoff, stream health counters);
> `edge/temporal.py` owns the `TemporalAnalyzer` interface and the deterministic rules
> (sustained PPE, restricted-zone dwell, loitering, low-motion heuristic);
> `edge/runtimes.py` owns `InferenceRuntime` selection; `edge/hardware_telemetry.py` owns
> CPU/GPU telemetry; `shared/model_contract.py` is the single canonical model contract and
> `shared/hardware_profiles.py` the device-profile matrix. The NVIDIA parser, `nvinfer.txt`
> and metadata bridge are generated from the contract. `backend/app/events.py` owns the
> additive commit-gated WebSocket bus. See [CURRENT_VERIFIED_STATE.md](CURRENT_VERIFIED_STATE.md).

The intended stack remains unchanged: React/Vite/Tailwind → FastAPI/SQLAlchemy → PostgreSQL 16; outbound edge agent with local SQLite WAL; OpenCV/FFmpeg/ONNX Runtime/ByteTrack CPU inference; DVC/MLflow/YOLO11n lifecycle; Docker Compose; Prometheus/Grafana and optional console OpenTelemetry.

`backend/app/db.py` defines the central schema. `main.py` implements auth, inventory, sources, device traffic and events. `lifecycle.py`, `campaigns.py` and `drift.py` contain their respective transactional workflows. `controller.py` polls persisted campaign state with PostgreSQL advisory locking. Only this central package writes central tables; simulator and training tooling use API clients.

`edge/pipeline.py` owns frame ingestion, sampling, inference and tracking. `edge/rules.py` owns head association and temporal event policy. `edge/state.py` owns the bounded outbox and activation state. `edge/agent.py` owns outbound communication, slots and worker launch. `edge/worker.py` suppresses candidate events until commit. Model/source failures cannot become hardcoded safety events.

A release pins model, runtime, default config and evaluation identities. Every desired assignment has a monotonic generation and ledger record. Actual state is accepted only from an authenticated device and must match assigned history. Rollback restores previous release/config bytes at a newer central generation. The agent preserves previous slot configuration separately from candidate configuration.

Simulation has two independent dimensions: inventory records and active clients. `seed_fleet.py` requests 8,000 simulated database records across 800 sites. `simulation/run.py` starts 10–50 persistent client threads. Synthetic worker observations remain labeled; neither quantity implies a physical deployment. No such fleet was created in the verification environment.

The NVIDIA directory contains a C++ parser, nvinfer configuration and metadata bridge that were never compiled or executed (no DeepStream SDK on either host), plus a standalone launcher. Their consistency with the CPU path is enforced statically instead: the contract header, `nvinfer.txt` and the bridge are generated from `shared/model_contract.py`. The TensorRT runtime *is* verified on a real NVIDIA GPU (Tesla T4, FP32 and true mixed FP16) — `docs/evidence/tensorrt/final/` — which confirms the model contract and engine build on real hardware but says nothing about DeepStream, JetPack or Jetson. See `HARDWARE_COMPATIBILITY.md` and `JETSON_DEPLOYMENT_TARGET.md`.

Security and failure boundaries are substantive but incomplete. Real promotion recomputes raw evidence and enforces fixed gates; its end-to-end qualification remains unverified. The release-gate gaps in `KNOWN_LIMITATIONS.md` take precedence over assumptions that code presence proves functionality.
