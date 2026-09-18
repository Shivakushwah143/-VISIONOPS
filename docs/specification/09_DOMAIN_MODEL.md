# 09 — Canonical domain model

## Identity, types and ownership

One organization per P0 deployment. Every API/DB field uses snake_case. Each entity uses its specific UUID field as primary key (e.g. `device_id`), not interchangeable `id`/`_id`/camelCase. UUIDs are opaque. UTC RFC3339 timestamps; elapsed durations use monotonic clocks on edge. All durable entities carry `created_at`, `updated_at`; append-only records only require `created_at`. Name is nonempty 1–120 characters; description/reason ≤2000. UUID references have foreign keys. Table fields below are required unless marked `?`; JSON fields are validated structures, not unrestricted arbitrary blobs.

Owner `system` means the single organization with role-controlled mutation. Device-owned rows must match the authenticated registered device and its site. No cross-organization access claim is made.

| Entity / key | Purpose and canonical fields beyond timestamps | Relationships / lifecycle | Owner |
| --- | --- | --- | --- |
| User / user_id | email, password_hash, role, status | role: four values in FRD; status active/disabled | system; bootstrap CLI |
| UserSession / session_id | user_id, token_hash, csrf_hash, expires_at, revoked_at? | active until revoked/expired; users 1:N sessions | user |
| Site / site_id | name, timezone, status | active/archived; site 1:N devices/cameras | system |
| Camera / camera_id | site_id, device_id, name, status | active/disabled; one device, one VideoSource | site |
| VideoSource / video_source_id | camera_id, kind, locator, credential_ref?, enabled, loop, source_revision | kind file/rtsp; runtime status reported separately; locator excludes credentials | site/device |
| EdgeDevice / device_id | site_id, name, mode, hardware_profile, registration_status, credential_hash?, last_boot_id?, last_sequence?, last_heartbeat_at?, desired_generation, desired_release_id?, desired_config_version_id?, actual_release_id?, actual_config_version_id?, applied_generation, agent_state, health_status, active_deployment_campaign_id? | mode real/simulated immutable; registration registered/enrolled/revoked; connectivity derived never_seen/online/offline | site |
| EnrollmentToken / enrollment_token_id | device_id, token_hash, expires_at, consumed_at? | one-use; new enrollment token can rotate credential | device |
| DeviceHeartbeat / heartbeat_id | device_id, boot_id, sequence, observed_at, received_at, actual_release_id?, actual_config_version_id?, applied_generation, agent_state, health_status, source_states, capabilities, rejected_generation?, last_error_code? | append accepted reports; source states use camera_id, status, last_frame_at?; sequence scoped to boot | device |
| ConfigVersion / config_version_id | hardware_profile, schema_version, sha256, settings, created_by | immutable settings: class_map, thresholds, queue/timing policy, camera source snapshots; ready | system |
| Model / model_id | name, task, class_map, status | task ppe_detection; active/archived; 1:N versions | system |
| DatasetVersion / dataset_version_id | name, git_commit, dvc_hash, dvc_remote_ref, taxonomy, split_manifest_hash, license_ref, validation_evidence_ref, status | draft/validated/retired; immutable after validated | system |
| TrainingRun / training_run_id | dataset_version_id, mlflow_run_id, code_commit, parameters, status, started_at, finished_at? | running/succeeded/failed; links one dataset snapshot | system |
| ModelVersion / model_version_id | model_id, training_run_id, mlflow_model_name, mlflow_model_version, version_label, status | registered/evaluated/retired; external version string immutable | system |
| ModelArtifact / model_artifact_id | model_version_id, format, precision, hardware_profile, sha256, size_bytes, storage_key, input_shape, class_map, compatibility, status | format onnx/tensorrt/pytorch; fp32/fp16/int8; ready/quarantined | system |
| RuntimeArtifact / runtime_artifact_id | version_label, hardware_profile, sha256, size_bytes, storage_key, entrypoint, compatibility, status | signed release pins worker bundle; ready/quarantined | system |
| EvaluationReport / evaluation_report_id | model_version_id, model_artifact_id, dataset_version_id, hardware_profile, evidence_mode, metrics, gate_policy, result, evidence_ref | evidence_mode real/simulated; result passed/failed/blocked; immutable evaluated metrics and raw evidence | system |
| Release / release_id | model_artifact_id, runtime_artifact_id, config_version_id, evaluation_report_id, hardware_profile, evidence_mode, manifest_sha256, manifest, signature?, key_id?, approved_by?, approved_at?, status | draft/approved/revoked; immutable bytes after creation; signature attached on approval | system |
| DeploymentCampaign / deployment_campaign_id | release_id, mode, target_device_ids, status, current_ring, ring_started_at?, created_by, reason, policy_snapshot | draft/running/paused/completed/rolling_back/rolled_back/rollback_incomplete/cancelled | system |
| DeploymentTarget / deployment_target_id | deployment_campaign_id, device_id, ring, status, previous_release_id?, previous_config_version_id?, assigned_generation?, rollback_generation?, baseline_metrics?, permit_expires_at?, assigned_config_version_id?, last_error_code?, observed_at? | pending/assigned/downloading/validating/observing/healthy/failed/rollback_pending/rolled_back | campaign/device |
| DeploymentEvent / deployment_event_id | deployment_campaign_id, device_id?, actor_type, actor_id, event_type, generation?, before_state?, after_state?, reason, evidence | append-only; event_type created/started/assigned/observed/paused/resumed/advanced/rollback_requested/rolled_back/failed/completed | system audit |
| DetectionEvent / detection_event_id | device_id, camera_id, stream_session_id, frame_sequence, observed_at, received_at, release_id, model_version_id, config_version_id, detections, evidence_mode | immutable sampled frame; detections array class_id, confidence, bbox, track_id? | device |
| SafetyEvent / safety_event_id | device_id, camera_id, stream_session_id, track_id, event_type, observed_at, received_at, window_start, window_end, supporting_frames, confidence, bbox, release_id, model_version_id, config_version_id, evidence_mode, review_status, acknowledged_by?, acknowledged_at?, evidence_key? | event_type no_helmet_violation; review_status new/acknowledged | device; reviewer action |
| MetricSummary / metric_summary_id | device_id, camera_id?, release_id?, window_start, window_end, sample_count, evidence_mode, values, received_at | append-only 15-second windows; values defined in observability spec | device |
| DriftSignal / drift_signal_id | camera_id, model_version_id, reference_dataset_version_id, window_start, window_end, sample_count, method, score?, threshold, status, reason? | insufficient_data/pending_review/accepted/dismissed | system |
| HardExample / hard_example_id | device_id, camera_id, safety_event_id?, drift_signal_id?, evidence_key, observed_at, release_id, confidence?, capture_reason, labels?, review_reason?, status, reviewed_by?, exported_dataset_version_id? | captured/accepted/rejected/exported; no biometric identity | site |
| RetrainingRequest / retraining_request_id | drift_signal_id, hard_example_ids, reason, requested_by, status, training_run_id? | requested/running/completed/failed/cancelled | system |
| AuditEvent / audit_event_id | actor_type, actor_id, action, entity_type, entity_id, before_state?, after_state?, reason?, request_id | append-only security/config/review mutation evidence | system |

## Shared value objects

`hardware_profile`: validated immutable string such as cpu_onnx_x86_64; NVIDIA profiles additionally identify architecture, GPU compute capability, driver compatibility, CUDA/TensorRT/DeepStream versions and input-shape/batch constraints. `compatibility` carries exact versions/ranges frozen by qualification. CPU arm64 is a separate profile; do not pretend x86 bundle portability.

`agent_state`: unconfigured/idle/fetching/validating/staging/activating/observing/healthy/rolling_back/degraded. `health_status`: unknown/healthy/degraded/unhealthy. Source runtime status: stopped/connecting/running/reconnecting/ended/error/frozen_suspected. `source_states` carries bounded per-camera entries; max 4 P0.

`bbox`: [x_min,y_min,x_max,y_max] normalized floats in [0,1], min < max. Confidence [0,1]. `track_id` is string unique only within camera+stream_session. `detections` max 200/frame. All device submissions include evidence_mode assigned from immutable device mode, never trusted from a freely editable client field.

## Desired state ownership

EdgeDevice is current desired/actual projection. Release.config_version_id is the default pipeline config. Camera/source edits create a new immutable per-device ConfigVersion derived from that default plus current sources, then update desired_config_version_id and generation; this override is explicit, not a mutated release. Campaign assignment derives target config from the new release defaults while retaining that target's current camera snapshots. Target saves the exact previous config for rollback. Source edits during an active campaign are rejected with 409 to prevent accidental lost updates. New default config must remain within the approved model preprocessing/class-map contract; changing these requires a newly evaluated release.

Actual state is device report only. Desired generation increases for every assignment, configuration change and rollback. Server changes desired generation with an expected-generation CAS; local agent reports applied_generation only after atomic commit. Device status summaries never infer success merely because desired changed.

## Constraints, retention and deletion

Unique indexes: user email; model name; (model_id,version_label); (model_version_id,format,precision,hardware_profile,sha256); (device_id,boot_id,sequence); detection_event_id and safety_event_id globally unique; (deployment_campaign_id,device_id); source camera_id. The locked active_deployment_campaign_id reservation prevents overlapping active targets per device. Index camera/device foreign keys and events by (camera_id, observed_at desc, event key); site-filtered queries join indexed camera.site_id (no cross-table index), devices by (site_id,mode,last_heartbeat_at), campaign targets by campaign/status.

Source revisions/configs and manifests are immutable. Archive/disable instead of cascading deletion of referenced evidence or deployed releases. Safety metadata 30 days, snapshots 7 days, sampled detections 24 hours, heartbeat/metric windows 7 days, Prometheus 7 days, audit/deployment history 90 days. Approved/referenced artifacts, evaluation evidence and lineage retained while any deployment or rollback references them. Hard examples accepted for training persist under dataset retention until retired; rejected captures expire after 7 days. Daily cleanup is bounded and audited, with missing optional evidence reflected in UI.

## Operational state required for correctness

| Entity / key | Fields and lifecycle | Purpose / owner |
| --- | --- | --- |
| DeviceAssignment / device_assignment_id | device_id,generation,release_id,config_version_id,reason,deployment_campaign_id?,created_by; immutable, unique(device_id,generation) | Every desired write (including commissioning/source edits/rollback) creates a ledger row; device history authorizes delayed events and preserves artifact references |
| DeviceBoot / device_boot_id | device_id,boot_id,first_received_at,retired_at?; unique(device_id,boot_id) | Keep retired boots for credential lifetime so a delayed old boot cannot overwrite state; device-owned |
| IdempotencyRecord / idempotency_record_id | user_id,route,key,request_sha256,response_status,response_body,expires_at; unique(user_id,route,key) | 24-hour mutation retry safety; enrollment credential issuance excluded; system-owned |

DeploymentTarget previous_* and baseline_metrics are null in an ineligible draft, but all are required and frozen atomically at start. assigned_config_version_id is the derived target config created at assignment. Candidate and rollback assignment rows preserve history even after campaign completion. Prevent overlapping active targets by a nullable EdgeDevice.active_deployment_campaign_id acquired under row lock, rather than attempting a PostgreSQL partial index that joins campaign status. Clear it only on completed/cancelled/fully rolled_back; rollback_incomplete continues to reserve unresolved devices.

DriftSignal is unique on (camera_id,model_version_id,window_start,window_end,method); a replay of identical analysis returns the existing row. Add reference_histogram and current_histogram (fixed class/confidence arrays), plus reference_window_start/reference_window_end; these are immutable evidence. MetricSummary.metric_summary_id is device-generated for idempotence. HardExample review_reason is required for rejection, and accepted labels are arrays of {class_id,bbox}; an empty array is allowed for a human-confirmed negative image. Dataset validation_evidence_ref points to a manifest/report under configured MLflow storage or a validated DVC content reference, never arbitrary remote fetch. Completed retraining requires a succeeded TrainingRun and matching exported dataset.

### Frozen ConfigVersion.settings

schema_version=1. Fields: class_map={0:person,1:helmet,2:no_helmet}; input_width=640,input_height=640; color_order=rgb; letterbox=true; score_threshold=0.35; nms_iou=0.50; inference_fps=5; queue_capacity=2; stale_frame_ms=500; batch_timeout_ms=100; rtsp_timeout_seconds=10; rule_min_frames=5; rule_min_span_seconds=2; rule_max_gap_seconds=1; rule_cooldown_seconds=30; cameras=[{camera_id,video_source_id,kind,locator,credential_ref?,enabled,loop,source_revision}]. Defaults are release-bound; operator source APIs may modify only cameras and cannot change model/rule/preprocessing fields. loop=false default, allowed only for file sources. One enabled source per camera and max 4 camera entries per real device. CPU baseline uses one. Config canonicalization permits finite numeric thresholds; only its SHA-256 string enters the signature manifest, which contains no floats.

P0 camera status toggles update the source enabled flag consistently. Disabling means no automatic restart; re-enable resumes with a new stream session. Config-only changes reconfigure sources and commit applied generation after configuration validation; they do not require downloading unchanged model/runtime bytes. Disabled/no-source configurations can apply successfully while pipeline health stays unknown and campaign eligibility remains false.
