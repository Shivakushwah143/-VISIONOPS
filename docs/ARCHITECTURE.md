# Implemented architecture

The intended stack remains unchanged: React/Vite/Tailwind → FastAPI/SQLAlchemy → PostgreSQL 16; outbound edge agent with local SQLite WAL; OpenCV/FFmpeg/ONNX Runtime/ByteTrack CPU inference; DVC/MLflow/YOLO11n lifecycle; Docker Compose; Prometheus/Grafana and optional console OpenTelemetry.

`backend/app/db.py` defines the central schema. `main.py` implements auth, inventory, sources, device traffic and events. `lifecycle.py`, `campaigns.py` and `drift.py` contain their respective transactional workflows. `controller.py` polls persisted campaign state with PostgreSQL advisory locking. Only this central package writes central tables; simulator and training tooling use API clients.

`edge/pipeline.py` owns frame ingestion, sampling, inference and tracking. `edge/rules.py` owns head association and temporal event policy. `edge/state.py` owns the bounded outbox and activation state. `edge/agent.py` owns outbound communication, slots and worker launch. `edge/worker.py` suppresses candidate events until commit. Model/source failures cannot become hardcoded safety events.

A release pins model, runtime, default config and evaluation identities. Every desired assignment has a monotonic generation and ledger record. Actual state is accepted only from an authenticated device and must match assigned history. Rollback restores previous release/config bytes at a newer central generation. The agent preserves previous slot configuration separately from candidate configuration.

Simulation has two independent dimensions: inventory records and active clients. `seed_fleet.py` requests 8,000 simulated database records across 800 sites. `simulation/run.py` starts 10–50 persistent client threads. Synthetic worker observations remain labeled; neither quantity implies a physical deployment. No such fleet was created in the verification environment.

The NVIDIA directory contains an unverified C++ parser, nvinfer configuration and metadata bridge. The continuation adds a standalone launcher; its SDK compatibility and integration remain unverified. See `HARDWARE_COMPATIBILITY.md`.

Security and failure boundaries are substantive but incomplete. Real promotion recomputes raw evidence and enforces fixed gates; its end-to-end qualification remains unverified. The release-gate gaps in `KNOWN_LIMITATIONS.md` take precedence over assumptions that code presence proves functionality.
