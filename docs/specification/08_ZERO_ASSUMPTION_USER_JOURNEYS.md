# 08 — Zero-assumption user journeys

API base is `/api/v1`. Refer to [10_API_CONTRACTS.md](10_API_CONTRACTS.md) for exact schemas. All actions below show pending/success/error states from FRD; do not navigate away after an unsuccessful write.

## A. Safety event — R01–R04

1. Operator starts CPU Compose profile, bootstraps accounts and signs in at `/login`. Open Sites; an empty deployment prompts creation of site, device, camera and source.
2. Follow B to enroll a real-mode device with an approved initial CPU release. Place permitted video under its media mount. Submit `POST /video-sources` with camera_id, kind=file, relative locator and enabled=true. Backend creates desired config generation; UI says pending until heartbeat reports it applied.
3. Agent polls desired state, verifies release, opens file and creates stream_session_id. Local preview draws real boxes, class labels and person track IDs. UI shows source running based on reported state. Missing PPE weights is blocked inference, not synthetic detection.
4. The explicit no_helmet rule accumulates observations. On qualification create safety_event_id, commit SQLite event and optional JPEG. Send `POST /device-events` containing generating release, model/config versions and observation window. Central transaction inserts deduplicated metadata; return acknowledgement.
5. Agent uploads JPEG separately to `POST /safety-events/{safety_event_id}/snapshot`. Metadata remains visible if upload fails. Backend checks ownership/size and links validated evidence.
6. Viewer at `/events` sees event on next 5-second poll. Click event, GET detail and authenticated snapshot. Show unavailable evidence if none; never silently substitute sample imagery. Choose Acknowledge, PATCH review status; record actor/time. Repeating acknowledgement is safe.
7. If WAN fails after step 4, local inference continues and replay later uses the same IDs. `observed_at` remains original and `received_at` shows delayed delivery.

## B. Device onboarding — R02, R05, R12

1. Operator creates site with `POST /sites`, then `POST /devices` with site_id, name, real/simulated mode, hardware_profile and initial approved release_id. API validates compatibility. State begins registered, connectivity never_seen.
2. Create enrollment token with `POST /devices/{device_id}/enrollment-tokens`. Show once and expire after 10 minutes; copying is explicit. Run enrollment CLI on selected edge with token and control-plane URL.
3. CLI calls `POST /device-enrollments`; backend atomically consumes token, returns device_id, site_id and bearer credential. Store credential locally, never in shell history or logs. Lost response requires operator to rotate via a fresh enrollment token; do not reuse consumed token.
4. First heartbeat reports capabilities and actual empty release. Agent fetches desired generation 1, stages initial signed bundle and runs the signed warmup fixture before reporting worker readiness. No camera yet means source health unknown and no campaign eligibility, even though model loading is verified. No previous bundle exists; failed bootstrap remains degraded. MLOps selects a corrected approved release using `POST /devices/{device_id}/commission` with expected_generation; this retry is allowed only before any successful initial release, not a canary bypass.
5. Operator adds camera `POST /cameras` and source `POST /video-sources`. Source changes produce ConfigVersion and generation 2; device detail shows mismatch until applied. Cross-site camera/device assignment is rejected.

## C. Heartbeat, fleet and health — R05, R09, R10

1. Agent sends heartbeat every 15 seconds with jitter, boot_id and increasing sequence. Backend stamps received_at, rejects stale sequence without overwriting current state, persists actual versions and fresh MetricSummary.
2. Operator opens `/devices`, filters site/mode/status and reads paginated rows plus `GET /fleet/summary`. Distinguish desired release, actual release and applied generation.
3. At >60 seconds without accepted heartbeat, central view becomes offline; inventory-only entries remain never_seen. A heartbeat with a crashed video worker means online + degraded, not healthy.
4. Follow device metrics link: actual FPS/latencies and sample age, null for unavailable GPU. Missing telemetry blocks rollout decisions.
5. For scale demo, run seed CLI for 800 sites/8,000 simulated records, then start 10 selected active agents. No heartbeat is forged for inactive records. Record actual resource/load evidence separately.

## D. Training and registration — R07

1. CV engineer obtains licensed labeled media; validate taxonomy, split by camera/site/session and remove duplicates before DVC snapshot. Run training CLI; DVC manifest captures hashes and Git commit.
2. Register DatasetVersion using `POST /dataset-versions`; run configured training CLI that writes MLflow parameters, artifacts and actual metrics. Register TrainingRun using `POST /training-runs` with MLflow run ID and dataset_version_id; update running→succeeded/failed via `PATCH /training-runs/{training_run_id}` when execution finishes.
3. Export selected model to ONNX; evaluate PyTorch and ONNX using the same locked evaluation set. Register Model if needed via `POST /models`, then ModelVersion via `POST /model-versions` referencing actual MLflow model version.
4. Upload model bytes `POST /model-artifacts` with multipart metadata. Backend computes SHA-256 and validates type/size. Submit `POST /evaluation-reports` with artifact/model/dataset/profile and evidence file reference stored within MLflow artifacts.
5. `/models/:model_id` displays lineage and results. MLflow failures leave workflow retryable; a training metadata record alone is not AI-EVALUATED. GPU export/benchmark is BLOCKED if hardware absent.

## E. Release — R07, R12

1. MLOps release CLI packages inference worker runtime with fixed entrypoint and immutable version; upload via `POST /runtime-artifacts`. Configuration is created through `POST /config-versions` for the target profile.
2. CV/MLOps proposes `POST /releases` referencing model artifact, runtime artifact, config and evaluation report. Server returns canonical manifest bytes with draft release ID.
3. MLOps signing CLI signs those exact manifest bytes using offline Ed25519 key; `POST /releases/{release_id}/approve` submits signature/key_id and reason. Backend validates hashes, compatibility, required gates and signature; approval is audited.
4. UI shows approved manifest and immutable hashes. Failed approval shows individual unmet gates. Model, runtime and release records are not editable in place; superseding releases get new IDs.

## F. Canary and progressive rollout — R08

1. At `/deployments`, choose Create, select one approved release and ≥10 eligible devices of one mode/profile. Submit `POST /deployments` for draft and target eligibility report. If targets are invalid, cancel this draft and create a new corrected draft; target membership has no edit endpoint.
2. Review immutable target list and per-device previous release; `POST /deployments/{deployment_campaign_id}/start`. Backend revalidates baseline and eligibility under lock; first 10% receives new generations.
3. Agents apply, report actual state and metrics. UI polls detail/targets/events every 2 seconds; show queued, download, observation, missing telemetry and gate reasons.
4. After all assigned targets satisfy FRD observation gates, ring is ready. MLOps presses Advance. Revalidate and commit cumulative 25%, then 100%. Completion requires final ring health, not just command delivery.

## G. Unhealthy rollout and rollback — R08, R12

1. Operator runs explicit simulator failure CLI for a simulated canary target, fault=inference_slow. Device sends actual simulated health window marked simulated. Real devices cannot accept simulator injection.
2. Controller observes three failing windows and pauses; UI shows threshold, evidence and affected IDs. No new ring assigned. User may also pause with reason.
3. MLOps presses Rollback and submits reason. Backend writes newer desired generations restoring saved previous state of all assigned targets, even those currently offline.
4. Agents restore prior verified bundles; detail shows actual previous release and newer applied generation. Campaign becomes rolled_back only after acknowledgements; unresolved devices after deadline produce rollback_incomplete with retry action. History includes fault, pause, actor, generation changes and actual recovery.

## H. Offline reconnect — R06, R08

1. Disconnect agent WAN while leaving worker running. Outbox grows within configured bound; UI becomes offline after 60 seconds.
2. Central rollout/rollback may update desired state. On reconnect, agent fetches newest desired generation before starting any obsolete update; it does not replay old deployment commands.
3. Report current actual state, reconcile approved newest release/config, then continue event replay. Safety events preserve original generating versions. Backend ignores stale deployment acknowledgements.
4. If artifact validation fails, keep last good, report failed generation and wait for newer desired state. Revoked credentials stop central upload but do not kill locally authorized inference; revocation's offline limitation is visible.

## I. Drift to retraining — R11, R07, R08

1. Drift CLI compares eligible per-camera class/confidence histograms to the approved reference and POSTs `/drift-signals`. UI says potential drift or insufficient_data, never measured accuracy loss without labels.
2. CV engineer opens `/drift`, inspects source windows and associated hard examples. Capture CLI registers `/hard-examples`; reviewer uses PATCH to accept/dismiss and provide imported normalized annotation JSON.
3. Review signal through PATCH; choose Request retraining, POST `/retraining-requests` with approved example IDs and rationale. Status requested until CV starts CLI workflow; no hidden automatic GPU job.
4. Export accepted labels into a new DVC dataset; register new DatasetVersion and TrainingRun through D. Link request to run and mark completed only after result exists. Evaluate challenger against unchanged evaluation set and champion.
5. Passing evaluation enables E, then F. No drift signal or retraining completion modifies desired state directly.
