# Implementation decisions and deviations

The 22 files under `docs/specification/` are exact copies of the authoritative specification. They were re-read and hashed by `scripts.audit_release`; no PRD/FRD or locked architecture was rewritten. Latest execution instructions supersede the original specification-only instruction.

1. PostgreSQL remains the only central domain database. SQLite is used only for edge state/outboxes. MLflow's Compose backend also targets PostgreSQL in a separate database. Native PostgreSQL's root/identity restriction is recorded as BLOCKED, not bypassed.
2. The external YOLOv8m PPE artifact is the documented Phase-2 baseline candidate; custom training remains YOLO11n. Its download, geometry and quality were not verified. A generic person model is not used as PPE.
3. Synthetic constant-output ONNX/video fixtures are limited to engineering verification and simulation-only releases. They cannot qualify a real release. Synthetic observations are clearly labeled and never called physical-device measurements.
4. Build-host Python 3.12.14 and Node 24.19.0 were used because the requested native versions were unavailable. Container declarations retain Python 3.11 and Node 22. Python version-resolution markers and exact package locks are included; container digests are not qualified.
5. Some proposed subdirectories are implemented as modules (`edge/pipeline.py`, `edge/state.py`, backend route modules). Ownership boundaries and technologies are preserved; directory consolidation is not a claim of feature completeness.
6. The baseline blocked all real reports. This continuation adds MLflow raw-evidence ingestion and fixed policy recomputation, but real-data qualification remains unverified. Client summary/result fields do not override recomputed metrics; the complete real-release journey remains unverified.
7. Source-only revisions get separate release/config slots. Content-addressed artifacts are reused after digest verification. This fixes candidate configuration overwriting the last-good slot.
8. Vite binds loopback during native development because wildcard binding invoked a denied host-interface lookup in this environment. Container frontend remains behind the configured local Nginx port.
9. Default simulation remains 800 sites/8,000 inventory records and 10 active clients. No such inventory was actually seeded here; no physical fleet claim is made.
10. The required phase order was used for initial source construction. Blocked model/database runtime gates were not declared passed; downstream source was written while those blockers remained. Therefore this is not a completed phase-by-phase acceptance execution.
11. The release is an incomplete source handoff. `KNOWN_LIMITATIONS.md` explicitly distinguishes environmental blockers from missing software. It is not a qualified production or end-to-end verified release.

12. Continuation edits preserve the supplied baseline and specifications. Rollback allocator, persisted watchdog, heartbeat rollback validation, metrics export and evidence recomputation are incremental changes. Actual component evidence is in continuation-runtime.json; database/fleet/real-PPE blockers are not reclassified as GPU-only blockers.
