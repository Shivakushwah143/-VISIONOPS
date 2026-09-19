# Executable demo boundary

> **Continuation 2 additions.** Three journeys now run without PostgreSQL, Docker, a GPU
> or a camera, and are the strongest honest demo available on this host:

```bash
.venv/bin/python -m scripts.verify_model_contract   # one contract, CPU + NVIDIA, independent decoder
.venv/bin/python -m scripts.verify_edge_platform    # runtimes, video, RTSP, temporal, telemetry, real run
.venv/bin/python -m scripts.export_onnx --checkpoint var/tools/hansung-best.pt \
    --output var/model/hansung-export.onnx --report docs/evidence/onnx-export-parity.json
```

The RTSP demo needs a local RTSP server (`docs/LOCAL_RTSP.md`) and is **not** verified
here; the 10K fleet demo needs the full stack (`docs/10K_FLEET_SCALING_REPORT.md`) and has
no measured result.

Use the exact setup/simulation commands in `START_HERE.md` on a Docker-capable host. These commands are not a report of a completed demo.

The only fully exercised demonstration here is the synthetic engineering journey in `scripts.verify_components`: generated video, actual ONNX Runtime execution of a constant-output fixture, actual ByteTrack, deterministic temporal event, durable SQLite outbox, restart and acknowledgement. It is visibly and structurally distinguished from real PPE functionality.

The intended connected simulation demo creates signed simulation-only releases, seeds 8,000 inventory records, activates 10 identities, observes baseline telemetry, assigns cumulative 1/3/10 rings, injects a simulator-owned unhealthy/offline marker, pauses and rolls back. Backend, controller and agents use real application code. Database runtime was unavailable, so that demonstration was not executed.

The real PPE journey requires downloaded/qualified model bytes, permitted labeled media, a completed evaluation importer and working PostgreSQL. Those prerequisites are unresolved in this archive. Do not present a synthetic fixture or inventory record as a real safety deployment.
