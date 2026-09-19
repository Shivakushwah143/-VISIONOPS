# Failure-matrix execution boundary

| Failure | Status | Actual evidence / gap |
| --- | --- | --- |
| Missing/tampered model | VERIFIED | Digest mismatch rejected in component experiment; real model load blocked by download |
| Unknown/conflicting PPE evidence | VERIFIED | Rule/association suppressed engineering events |
| Unsafe tar/signature | VERIFIED | Traversal, symlink and modified manifest rejected in executed component checks |
| PostgreSQL unavailable | VERIFIED | API readiness returned actual HTTP 503; database workflows remain BLOCKED |
| Camera unavailable / RTSP reconnect | VERIFIED (contract, no server present) | Bounded reconnect with backoff against an unreachable endpoint reported `rtsp_connected:false`, zero frames, clean shutdown; no MediaMTX session established (`docs/evidence/edge-platform-runtime.json`) |
| Stale frames / backlog | VERIFIED (contract) | Bounded queue of 2 with drop-oldest and the 500 ms freshness check ran over a real file source and the real-artifact run; the counters are reported per camera |
| Video backend unavailable | VERIFIED | An explicitly requested GStreamer backend refused to start (`gstreamer_bindings_unavailable`) instead of silently retrying forever |
| Missing GPU telemetry treated as healthy | VERIFIED | GPU reported as absent with `gpu: null` and never as 0 %; campaign signals gate nothing on absent GPU data |
| WAN unavailable / edge offline | IMPLEMENTED — NOT RUNTIME VERIFIED | Durable outbox/HTTP retry and simulator marker exist; two-minute connected outage experiment absent |
| Agent restart | VERIFIED | SQLite outbox reopen/ack only; full activation crash recovery not runtime verified |
| Unhealthy canary / rollback | IMPLEMENTED — NOT RUNTIME VERIFIED | Persistent controller/agent code exists; database unavailable; corrected permit allocator component verified |
| Committed worker crash loop | VERIFIED | Three actual child-process crashes, previous-worker restoration and 60-second recovery; engineering worker/SQLite component only |
| Expired evidence / retention pressure | NOT IMPLEMENTED | Complete cleanup and bounded snapshot spool missing |
