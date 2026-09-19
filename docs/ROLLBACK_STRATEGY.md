# Rollback strategy

Two independent rollback paths must both work, because they fail in different ways:
a candidate can be wrong even when the control plane is unreachable, and a device can
be offline exactly when the control plane wants it to go back.

```text
central rollback          control plane decides; bounded download leases; device reconciles on reconnect
local watchdog rollback   device decides; candidate never becomes "known good"
```

## 1. Activation is effectively atomic

A candidate can never overwrite the active known-good release:

1. The agent verifies the release manifest hash **and** the Ed25519 signature before
   staging anything.
2. Model and runtime bytes are downloaded into a **slot keyed by release + config
   version**, so a source-only change cannot reuse (or overwrite) the previous slot.
   Bytes are written to `*.partial`, `fsync`ed, and only then `replace`d.
3. Artifact size and SHA-256 must match the signed manifest before extraction.
4. The archive is bounded (≤10 000 members, ≤2 GiB) and rejected on absolute paths,
   `..`, backslashes or non-file/dir members.
5. An **activation journal** records `previous` and `candidate`. An interrupted
   activation is rolled back on the next start: the journal restores `actual` to
   `previous` and records the candidate as a rejected generation. It never commits a
   half-activated candidate.
6. The worker suppresses event production until the committed state matches its own
   manifest (`state.get('actual')` and no pending activation), so warmup produces no
   safety events.

## 2. Candidate observation window

The agent commits only after the candidate survives a 60-second observation window.
It checks, every 5 seconds, that the child process is alive, that the worker status
file is fresh and marked ready, and that the control plane has not superseded the
assignment. On any failure it stops the candidate and restores the previous slot.

`worker_health()` deliberately distinguishes **process liveness** from **source
health**: a camera outage or file EOF is a source condition, not a crash, and must not
trigger a rollback. `process_alive()` applies the same distinction.

## 3. Local watchdog rollback

`edge/watchdog.py` persists a 3-crashes-in-5-minutes policy per generation with
exponential restart backoff. The third crash inside the window triggers
`local_rollback()`:

* the failed candidate is stopped and the previous release is started;
* `actual` is restored **at the same committed central generation**
  (`{**previous, 'generation': failed['generation']}`), so the control plane's
  assignment history stays consistent;
* the failed generation is recorded as `rejected_generation`, which the heartbeat
  reports so the control plane can refuse to re-issue it;
* if there is no previous known-good release, the supervisor is disabled and the
  device reports `degraded` rather than entering a crash loop;
* a 60-second recovery window then watches the restored worker; if *that* also fails,
  the supervisor disables itself instead of flapping.

## 4. Central rollback

`POST /api/v1/deployments/{id}/rollback` assigns `previous_release_id` /
`previous_config_version_id` at a **newer** generation through the normal assignment
ledger. Nothing is mutated in place, so the ledger remains an audit trail.

* Leases are bounded by `edge`-side `backend/app/permits.py`: at most **5** active
  download permits per campaign, transferred when a device converges, reclaimed when a
  heartbeat goes stale, and never issued to a device whose desired generation is
  already applied.
* Targets are marked `rollback_pending`; each becomes `rolled_back` only when the
  device reports `applied_generation == rollback_generation`, the previous release and
  config, `health_status == healthy` and a heartbeat fresher than 30 s.
* If the campaign does not finish within 10 minutes it becomes `rollback_incomplete`
  instead of pretending to have succeeded.

## 5. Offline device during rollback (required behaviour)

```text
control plane desired = previous release
device offline
→ reconnect
→ heartbeat
→ desired != actual
→ reconcile
→ previous release restored
```

This is exactly why rollback is expressed as a *desired state at a new generation*
rather than a push: an offline device needs no special path. The sequence is
implemented in the agent's `reconcile()` (fetch desired state, compare generation,
stage, observe, commit) and gated centrally by `assess()`. Failing targets stay
pending until they reconnect.

## 6. Failure experiments

| Scenario | Status | Evidence |
| --- | --- | --- |
| Bad model / corrupt artifact | **VERIFIED** (component) | digest mismatch rejected before an inference session is created (`component-runtime.json`) |
| Unsafe archive / tampered manifest | **VERIFIED** (component) | traversal, symlink and modified-manifest rejection |
| Worker crash loop → local rollback | **VERIFIED** (component) | three real child-process crashes, restart backoff, previous-worker restoration, 60 s recovery, retained applied generation 2 and reported rejected generation 2 (`continuation-runtime.json`) |
| Activation interrupted by process restart | **IMPLEMENTED — NOT VERIFIED** at runtime; the journal restore path runs in `Agent.__init__` | needs a signed candidate activation |
| Artifact verification failure reported to the gate | **IMPLEMENTED — NOT VERIFIED** | `ARTIFACT_VERIFICATION_FAILED` reason code in `backend/app/campaigns.py` |
| RTSP outage during observation | **VERIFIED** for the source contract | bounded reconnect, no crash, clean shutdown (`edge-platform-runtime.json`) |
| Central rollback with a live database | **IMPLEMENTED — NOT VERIFIED** | requires PostgreSQL; the lease allocator itself is component-verified |
| Device offline during rollback, then reconnect | **IMPLEMENTED — NOT VERIFIED** | needs the control plane; the harness in `simulation/fleet_scale.py` exercises the reconnect reconciliation path |
| Rollback request for a device that never comes back | **IMPLEMENTED — NOT VERIFIED** | campaign ends `rollback_incomplete` after the deadline |

## 7. Reproduce

```bash
.venv/bin/python -m scripts.verify_continuation     # local watchdog rollback (real subprocesses)
.venv/bin/python -m scripts.verify_security         # signature and archive rejection
```

Central rollback requires the full stack:

```bash
docker compose --env-file .env -f infrastructure/compose.yaml up --build -d
# then drive a campaign from the console or the API and request rollback
```
