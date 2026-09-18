# Failure-matrix execution boundary

| Failure | Status | Actual evidence / gap |
| --- | --- | --- |
| Missing/tampered model | VERIFIED | Digest mismatch rejected in component experiment; real model load blocked by download |
| Unknown/conflicting PPE evidence | VERIFIED | Rule/association suppressed engineering events |
| Unsafe tar/signature | VERIFIED | Traversal, symlink and modified manifest rejected in executed component checks |
| PostgreSQL unavailable | VERIFIED | API readiness returned actual HTTP 503; database workflows remain BLOCKED |
| Camera unavailable / RTSP reconnect | IMPLEMENTED — NOT RUNTIME VERIFIED | Decoder has timeouts/reconnect state; no real RTSP endpoint exercised |
| Stale frames / backlog | IMPLEMENTED — NOT RUNTIME VERIFIED | Queue size 2, drop-oldest and 500 ms freshness checks exist; dedicated failure experiment absent |
| WAN unavailable / edge offline | IMPLEMENTED — NOT RUNTIME VERIFIED | Durable outbox/HTTP retry and simulator marker exist; two-minute connected outage experiment absent |
| Agent restart | VERIFIED | SQLite outbox reopen/ack only; full activation crash recovery not runtime verified |
| Unhealthy canary / rollback | IMPLEMENTED — NOT RUNTIME VERIFIED | Persistent controller/agent code exists; database unavailable; corrected permit allocator component verified |
| Committed worker crash loop | VERIFIED | Three actual child-process crashes, previous-worker restoration and 60-second recovery; engineering worker/SQLite component only |
| Expired evidence / retention pressure | NOT IMPLEMENTED | Complete cleanup and bounded snapshot spool missing |
