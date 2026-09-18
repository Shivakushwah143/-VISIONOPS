# Executable demo boundary

Use the exact setup/simulation commands in `START_HERE.md` on a Docker-capable host. These commands are not a report of a completed demo.

The only fully exercised demonstration here is the synthetic engineering journey in `scripts.verify_components`: generated video, actual ONNX Runtime execution of a constant-output fixture, actual ByteTrack, deterministic temporal event, durable SQLite outbox, restart and acknowledgement. It is visibly and structurally distinguished from real PPE functionality.

The intended connected simulation demo creates signed simulation-only releases, seeds 8,000 inventory records, activates 10 identities, observes baseline telemetry, assigns cumulative 1/3/10 rings, injects a simulator-owned unhealthy/offline marker, pauses and rolls back. Backend, controller and agents use real application code. Database runtime was unavailable, so that demonstration was not executed.

The real PPE journey requires downloaded/qualified model bytes, permitted labeled media, a completed evaluation importer and working PostgreSQL. Those prerequisites are unresolved in this archive. Do not present a synthetic fixture or inventory record as a real safety deployment.
