# Industrial VisionOps Platform

An implementation in progress for industrial PPE evidence and edge-fleet operations. **Not end-to-end verified and not production-ready.** Start with [START_HERE.md](START_HERE.md).

The archive contains actual React/Vite/Tailwind, FastAPI/SQLAlchemy/PostgreSQL, ONNX Runtime/OpenCV/ByteTrack, durable SQLite edge state, signed artifacts, campaign/controller, simulation and model-lifecycle source. It includes all 22 authoritative specification documents unchanged under `docs/specification/`.

Runtime verification passed for synthetic video → ONNX → tracking → deterministic rule → SQLite outbox; outbox restart/acknowledgement; valid/tampered signatures and unsafe archives; API liveness/Prometheus exposition; frontend build and HTTP startup. Synthetic engineering predictions do not demonstrate trained PPE capability.

PostgreSQL startup was blocked by this host's OS restrictions; Docker was absent. The real PPE weight download was blocked. No database-backed end-to-end journey, fleet inventory creation, canary/rollback, real model-quality evaluation or GPU inference was verified. Additional implementation gaps are explicitly listed in [docs/KNOWN_LIMITATIONS.md](docs/KNOWN_LIMITATIONS.md).

Use the evidence JSON files and [verification report](docs/VERIFICATION_REPORT.md) to distinguish runtime results from code presence. No physical fleet, GPU performance or live dashboard metrics are fabricated.

Continuation: rollback leasing and post-commit watchdog components now passed real execution checks. Raw evaluation recomputation, persisted per-camera metric export and a standalone NVIDIA launcher were added. Full PostgreSQL/fleet/real-PPE qualification remains blocked and no complete release approval is claimed.
