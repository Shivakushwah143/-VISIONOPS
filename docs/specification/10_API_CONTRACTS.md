# 10 — Frozen API contracts

## Protocol conventions

Base `/api/v1`; JSON UTF-8 unless multipart or binary explicitly stated. Fields/types/enums are normative in [09_DOMAIN_MODEL.md](09_DOMAIN_MODEL.md). Body fields not listed are rejected. Resource responses use `{data: Resource}`; list responses `{data: [Resource], page: {next_cursor, limit}}`; no bare arrays. Server-generated keys/timestamps are never required in user create requests. Returned resource means all non-secret entity fields. Secrets, storage paths and password/token hashes never appear in ordinary responses; use authenticated download endpoints. Device enrollment and enrollment-token issuance are explicit one-time secret exceptions.

Pagination: `limit` default 50, max 200; opaque cursor of stable (created_at,key) order. Event feeds newest observed_at then event key; cursor includes both. `next_cursor=null` means complete. Unknown filter enums return 422. Detail endpoints use exact entity UUIDs in braces; not external MLflow IDs.

Error shape: `{error:{code,message,details,request_id}}`. All endpoints may return 400 malformed request, 401 missing/invalid credential, 403 role/scope denial, 404 inaccessible/missing resource, 422 field validation, 429 with Retry-After, 503 required service unavailable. Mutations may return 409 stale generation/state/idempotency conflict. Downloads may return 410 retained metadata but expired evidence. Exceptions below add codes, not different shapes.

Auth abbreviations: U=any active user session; O=fleet_operator or mlops_engineer; C=cv_engineer or mlops_engineer; M=mlops_engineer; V=safety_viewer, fleet_operator or mlops_engineer; D=device bearer scoped to itself. Session mutations require CSRF token and valid Origin. CLI user auth uses the same session login/cookie flow, not an undocumented superuser key.

POST creates return 201; command POSTs 200 unless listed. PATCH returns 200. GET returns 200. `Idempotency-Key` UUID required for user POST writes except login and for release approval/deployment commands; scope user+route, retain response 24 hours. Same key/body replays result; changed body 409. Device batches use stable event IDs; heartbeat uses boot/sequence. Device enrollment uses single-use token, not request replay. File uploads use SHA-256 dedupe within parent artifact identity and reject differing bytes on replay.

## Authentication and inventory

| Method / route | Purpose / auth | Request | Response / additional errors |
| --- | --- | --- | --- |
| POST /auth/login | Sign in / public | email, password | 200 data:{user,csrf_token}; HttpOnly session cookie; 401 invalid_credentials |
| POST /auth/logout | Revoke current session / U | empty | 200 data:{logged_out:true}, clear cookie |
| GET /auth/me | Current identity / U | none | data:{user,csrf_token} |
| GET /sites | Site picker/list / U | cursor,limit,status? | list Site |
| POST /sites | Create site / O | name,timezone | Site active |
| GET /sites/{site_id} | Site detail / U | none | Site |
| PATCH /sites/{site_id} | Rename/archive / O | name?,status? | Site; 409 active_devices prevents archive |
| GET /cameras | Camera list / U | site_id?,device_id?,cursor,limit | list Camera |
| POST /cameras | Add camera / O | site_id,device_id,name | Camera; 422 site_mismatch |
| PATCH /cameras/{camera_id} | Rename/disable / O | name?,status? | Camera; disabling creates desired source config; 409 campaign_active |
| GET /video-sources | Sources / U | camera_id?,device_id?,cursor,limit | list VideoSource; locator redacted to safe host/path |
| POST /video-sources | Assign source / O | camera_id,kind,locator,credential_ref?,enabled,loop? | data:{video_source,desired_generation,config_version_id}; 409 source_exists/campaign_active |
| PATCH /video-sources/{video_source_id} | Edit/stop source / O | expected_source_revision,locator?,credential_ref?,enabled?,loop? | same shape as create; 409 revision_conflict/campaign_active |
| GET /devices | Fleet table / U | site_id?,mode?,connectivity?,health_status?,q?,cursor,limit | list EdgeDevice with derived connectivity and last_seen_age_seconds; `q` is a literal substring match over name/device_id (≤120 chars), `connectivity` is online/offline/never_seen using the same 60 s server-receipt rule as the summary |
| POST /devices | Pre-register identity / O | site_id,name,mode,hardware_profile,release_id | EdgeDevice, desired_generation=1; 422 incompatible_release |
| POST /devices/{device_id}/commission | Retry failed first installation / M | release_id,expected_generation,reason | EdgeDevice with newer desired generation; only actual_release_id=null and no active campaign;409 already_commissioned/conflict |
| GET /devices/{device_id} | Device detail / U | none | data:{device,last_heartbeat,desired_state}; missing heartbeat null |
| PATCH /devices/{device_id} | Rename/revoke / O | name?,registration_status? | EdgeDevice; only enrolled/registered→revoked allowed; revoke audit |
| POST /devices/{device_id}/enrollment-tokens | Issue/rotate enrollment / O | reason | 201 data:{enrollment_token_id,token,expires_at}; token shown once |
| POST /device-enrollments | Consume enrollment / token | token,capabilities | 201 data:{device_id,site_id,device_credential}; 401 expired_or_used_token |
| GET /fleet/summary | Overview / U | site_id?,mode? | data:{total_records,real_records,simulated_records,online,offline,never_seen,healthy,degraded,desired_actual_mismatch,generated_at}; connectivity counts partition selected total |

Creating a fresh enrollment token invalidates prior unused tokens; existing enrolled credential is replaced only on successful exchange. Revoked devices cannot re-enroll; create a new identity. API accepts no operator-supplied actual state.

## Device channel

| Method / route | Purpose / auth | Request | Response / errors |
| --- | --- | --- | --- |
| POST /devices/{device_id}/heartbeats | Actual state and health / D | HeartbeatInput below | data:{accepted,server_time,desired_generation}; stale sequence accepted=false |
| GET /devices/{device_id}/desired-state | Reconcile / D | If-None-Match? | data:DesiredState below, opaque ETag covers generation plus download-permit state; 304 only if both unchanged |
| POST /device-events | Durable event ingest / D | events array of DetectionInput or SafetyInput, max 100 and 1 MiB | 200 data:{results:[{event_id,status,error_code?}]}; status accepted/duplicate/rejected; transport 413 body_too_large |
| POST /safety-events/{safety_event_id}/snapshot | Optional evidence / D | multipart file JPEG ≤512 KiB | data:{safety_event_id,evidence_available:true}; 413 size,415 type,409 different_snapshot |
| GET /releases/{release_id}/manifest | Approved signed manifest / U or D | none | data:{manifest,manifest_sha256,signature,key_id}; D restricted to current desired/actual/last-good target references |
| GET /model-artifacts/{model_artifact_id}/content | Download model / U or D | Range optional | binary with SHA-256 ETag, Content-Length; 206 range,416 invalid range; D reference-scoped |
| GET /runtime-artifacts/{runtime_artifact_id}/content | Download runtime / U or D | Range optional | same binary contract and scope |
| GET /config-versions/{config_version_id} | Immutable config / U or D | none | ConfigVersion with safe sources; D only own desired/current/rollback config, plus the signed default config of a release assigned to it (required to verify a campaign config against the release's policy baseline before activation) |

HeartbeatInput: device_id must match path; `boot_id` UUID, `sequence` nonnegative int, `observed_at`, `actual_release_id?`, `actual_config_version_id?`, `applied_generation` nonnegative int, `agent_state`, `health_status`, `source_states` (≤4), `capabilities`, `metric_summaries` (≤4 windows), `rejected_generation?`, `last_error_code?`. Metric window contract is document 12. No secret values. New boot_id permitted after successful credential authentication; central records retired boot IDs and rejects a delayed report from a retired boot. Device clock skew does not determine liveness; server received_at does.

DesiredState: `device_id,site_id,generation,release_id,config_version_id,manifest_sha256,config_sha256,download_permit,issued_at`. `download_permit` boolean is granted by controller's persisted 5-download campaign concurrency budget; false means keep polling, not failure. Enrollment/config-only changes may set true outside campaigns. Manifest/content endpoints independently reauthorize every request. A permit reservation is persisted with permit_expires_at and expires after 10 minutes or download completion; fresh heartbeat in fetching state renews it while the campaign convergence deadline remains unexpired. ETag changes whenever permit boolean changes, even if generation does not. Agent discards ETag cache on reenrollment. Agent never abandons last good just because permit is false.

DetectionInput: `kind=detection`, `detection_event_id`, `camera_id`, `stream_session_id`, `frame_sequence`, `observed_at`, `release_id`, `model_version_id`, `config_version_id`, `detections`. SafetyInput: `kind=safety`, `safety_event_id`, `camera_id`, `stream_session_id`, `track_id`, `event_type`, `observed_at`, `window_start`, `window_end`, `supporting_frames`, `confidence`, `bbox`, `release_id`, `model_version_id`, `config_version_id`. Server derives device_id/site/evidence_mode and received_at; historical generating versions may differ from current desired but must be known, compatible and previously assigned to that device. One invalid event does not reject valid neighbors; rejected IDs carry permanent error code. Events missing metadata remain local pending; snapshot upload cannot invent an event.

## Model lifecycle

Create bodies below include exactly the listed fields; immutable lifecycle changes use named commands. CLI operations call these APIs instead of writing application tables.

| Method / route | Purpose / auth | Request | Response / errors |
| --- | --- | --- | --- |
| GET /models | Model dashboard / U | cursor,limit | list Model |
| POST /models | Register logical model / C | name,task,class_map | Model;409 name_exists |
| GET /models/{model_id} | Lineage overview / U | none | data:{model,versions,dataset_versions,training_runs,evaluation_reports,releases}; max 100 recent versions; version list paginates history |
| GET /model-versions | Version history / U | model_id,cursor,limit | list ModelVersion |
| POST /dataset-versions | Record DVC snapshot / C | name,git_commit,dvc_hash,dvc_remote_ref,taxonomy,split_manifest_hash,license_ref,validation_evidence_ref,status | DatasetVersion; status draft/validated; validation evidence required for validated |
| POST /training-runs | Link MLflow run / C | dataset_version_id,mlflow_run_id,code_commit,parameters,status,started_at,finished_at? | TrainingRun;503 registry_unavailable; external status cross-checked |
| PATCH /training-runs/{training_run_id} | Finish registered run / C | status,finished_at | TrainingRun; running→succeeded/failed only; verify MLflow state |
| POST /model-versions | Link registered model / C | model_id,training_run_id,mlflow_model_name,mlflow_model_version,version_label | ModelVersion;422 lineage_mismatch |
| POST /model-artifacts | Store model bytes / C | multipart file + metadata:{model_version_id,format,precision,hardware_profile,input_shape,class_map,compatibility} | ModelArtifact;415 unsupported_format,413 >2 GiB |
| POST /runtime-artifacts | Store worker bundle / M | multipart file + metadata:{version_label,hardware_profile,entrypoint,compatibility} | RuntimeArtifact; fixed entrypoint worker; reject unsafe tar members;413 >2 GiB |
| POST /evaluation-reports | Persist observed evaluation / C | model_version_id,model_artifact_id,dataset_version_id,hardware_profile,evidence_mode,metrics,gate_policy,result,evidence_ref | EvaluationReport; server recomputes gates,422 evidence_mismatch |
| POST /config-versions | Create release default config / C | hardware_profile,schema_version,settings | ConfigVersion; server computes canonical sha256 |
| POST /releases | Draft manifest / C | model_artifact_id,runtime_artifact_id,config_version_id,evaluation_report_id | Release draft with manifest bytes represented as canonical UTF-8 JSON string |
| GET /releases | Release picker / U | status?,hardware_profile?,cursor,limit | list Release |
| POST /releases/{release_id}/approve | Sign and approve / M | signature,key_id,reason | Release approved;409 gates_failed,422 signature_invalid |
| POST /releases/{release_id}/revoke | Prevent new assignment / M | reason | Release revoked;409 referenced_active_requires_replacement; never silently remove current worker |

MLflow evidence_ref is an artifact reference under the configured private tracking server, not arbitrary fetchable URL. Simulation EvaluationReports can approve simulation-only releases; real devices reject them. Registry metadata import does not fabricate training/evaluation execution.

## Campaigns

| Method / route | Purpose / auth | Request | Response / errors |
| --- | --- | --- | --- |
| GET /deployments | Campaign list / U | status?,cursor,limit | list DeploymentCampaign |
| POST /deployments | Draft and eligibility / M | release_id,target_device_ids,reason | data:{campaign,eligibility:[{device_id,eligible,reasons}]}; ≥10 IDs; duplicates422 |
| GET /deployments/{deployment_campaign_id} | Status/gates / U | none | data:{campaign,counts,gate_status,gate_reasons,observed_at}; gate_status waiting/ready/failed |
| GET /deployments/{deployment_campaign_id}/targets | Target progress / U | status?,cursor,limit | list DeploymentTarget |
| GET /deployments/{deployment_campaign_id}/events | Audit timeline / U | cursor,limit | list DeploymentEvent |
| POST /deployments/{deployment_campaign_id}/start | Freeze and assign canary / M | expected_status=draft,reason | Campaign;409 eligibility_changed |
| POST /deployments/{deployment_campaign_id}/advance | Assign next ring / M | expected_ring,reason | Campaign;409 gates_not_ready/stale_ring |
| POST /deployments/{deployment_campaign_id}/pause | Stop new assignments / M | reason | Campaign paused; already paused idempotent |
| POST /deployments/{deployment_campaign_id}/resume | Continue same ring / M | reason | Campaign running;409 unresolved_failure; never skips ring |
| POST /deployments/{deployment_campaign_id}/rollback | Restore saved previous states / M | reason | Campaign rolling_back; pending retry from rollback_incomplete allowed |
| POST /deployments/{deployment_campaign_id}/cancel | Cancel before start / M | reason | Campaign cancelled;409 if any assignment exists |

No general public PATCH desired-state endpoint: initial registration/failed-commission retry, campaign controller and validated camera/config operations are the only writers. Heartbeat drives actual-state projection and target observation; no second undocumented acknowledgement API.

## Events, metrics and drift

| Method / route | Purpose / auth | Request | Response / errors |
| --- | --- | --- | --- |
| GET /detections | Sampled detection inspection / U | device_id?,camera_id?,from?,to?,cursor,limit | list DetectionEvent |
| GET /safety-events | Event feed / U | site_id?,camera_id?,review_status?,evidence_mode?,from?,to?,cursor,limit | list SafetyEvent with evidence_available |
| GET /safety-events/{safety_event_id} | Evidence detail / U | none | SafetyEvent with evidence_available |
| GET /safety-events/{safety_event_id}/snapshot | Read evidence / U | none | JPEG;404 never_uploaded,410 expired |
| PATCH /safety-events/{safety_event_id} | Acknowledge review / V | review_status=acknowledged | SafetyEvent; repeat same state succeeds |
| GET /metrics/summaries | UI metric windows / U | device_id?,camera_id?,from,to,cursor,limit | list MetricSummary; max query window 24 hours |
| GET /drift-signals | Review queue / U | status?,cursor,limit | list DriftSignal |
| POST /drift-signals | Drift job result / C | camera_id,model_version_id,reference_dataset_version_id,window_start,window_end,sample_count,method,score?,threshold,status | DriftSignal; status pending_review/insufficient_data only |
| PATCH /drift-signals/{drift_signal_id} | Human decision / C | status=accepted or dismissed,reason | DriftSignal; audit actor |
| GET /hard-examples | Example review / C | drift_signal_id?,status?,cursor,limit | list HardExample |
| POST /hard-examples | Register captured evidence / D or C | multipart JPEG + metadata:{camera_id,safety_event_id?,drift_signal_id?,observed_at,release_id,confidence?,capture_reason} | HardExample; evidence source binding checked; JPEG ≤512 KiB |
| PATCH /hard-examples/{hard_example_id} | Label/review / C | status,labels?,review_reason?,exported_dataset_version_id? | HardExample; accepted requires normalized class/box labels; exported only via dataset CLI |
| GET /hard-examples/{hard_example_id}/snapshot | Labeling image / C | none | JPEG;410 expired |
| POST /retraining-requests | Human-authorized handoff / C | drift_signal_id,hard_example_ids,reason | RetrainingRequest requested;422 unaccepted_examples |
| GET /retraining-requests | Work queue / C | cursor,limit | list RetrainingRequest |
| PATCH /retraining-requests/{retraining_request_id} | Link CLI execution / C | status,training_run_id? | RetrainingRequest; running/completed requires matching TrainingRun |

Operational endpoints outside API base: GET `/health/live` public returns `{status:alive}`; GET `/health/ready` private returns `{status:ready}` or 503 if PostgreSQL/migrations unavailable; GET `/metrics` private Prometheus exposition. Optional MLflow/Prometheus outage is reported separately and does not falsify API readiness. No simulator failure HTTP endpoint: a local operator CLI changes only simulator-owned state.

## Additional schema and lifecycle invariants

Drift POST also requires reference_histogram,current_histogram,reference_window_start,reference_window_end, as defined in the domain model. Use method=js_confidence_class_v1 and reject malformed/nonmatching bins; score must be recomputable from the evidence. Dataset validation requires matching validation_evidence_ref. Hard-example review transitions captured→accepted/rejected, accepted→exported only with exported_dataset_version_id; exported records cannot silently change labels. Training PATCH only finishes an existing running run; already-final identical state is idempotent.

Initial no-camera device uses release default config with cameras=[] and performs a local signed warmup-fixture readiness check. The fixture is part of the runtime artifact and is not uploaded as a camera/event or included in production metrics. Source health remains unknown until a real configured source runs. Every desired change appends DeviceAssignment; matching historical rows authorize delayed event provenance. Simulation lifecycle CLI records real MLflow runs tagged run_kind=simulation_fixture, uses a valid clearly synthetic ONNX fixture and simulation EvaluationReport, and never presents those as trained PPE quality. That enables the fleet demo without silently weakening real-device approval.

Campaign state transitions: draft→running/cancelled; running→paused/completed/rolling_back; paused→running/rolling_back; completed→rolling_back only while targets remain at this campaign's assigned desired generations and no newer active campaign exists; rolling_back→rolled_back/rollback_incomplete; rollback_incomplete→rolling_back/rolled_back. All other command transitions return 409. Duplicate rollback retries retain already-issued rollback_generation values instead of incrementing again; an explicitly newer rollback transaction is allowed only if current desired state has legitimately changed and conflict checks pass. rolled_back/cancelled are terminal. A late valid acknowledgement may finish rollback_incomplete automatically.

Campaign baseline is refreshed at start and checked again before assigning each later ring; if an unassigned device has gone offline or changed workload, pause rather than assign. Ring gate assesses all cumulative assigned targets, each on its assigned release/config. Local failed-generation status maps target to failed; cached last-good inference does not count as candidate success. 10-minute convergence deadline starts at ring assignment and includes download/local activation; 5-minute observation starts after all targets in that cumulative ring converge. No hidden auto-advance.

Manifest includes schema_version=1, release_id, hardware_profile, evidence_mode, model_artifact_id/model_sha256/model_size_bytes, runtime_artifact_id/runtime_sha256/runtime_size_bytes, config_version_id/config_sha256, evaluation_report_id, compatibility and entrypoint=worker. Release approval verifies report artifact/profile/model and class-map matches, validated dataset, succeeded training run (or explicitly simulated fixture path), verified bytes, approved gate policy, trusted key_id and valid signature. A cv_engineer cannot lower promotion policy via gate_policy input; server accepts only the current MLOps-controlled policy defined in document 13.

Registration does not trust client result=passed; server recomputes pass/fail from metrics and checks raw evidence references. Metrics must include per_class [{class_id,precision,recall,f1,ap50,ap50_95,support}], map50,map50_95,confusion_matrix,event_precision,event_recall,event_support,duplicate_events_per_track_minute,latency_p50_ms,latency_p95_ms,inference_fps,peak_rss_bytes,peak_vram_bytes?,gpu_utilization_ratio?,gpu_temperature_celsius?,dropped_frames,crashes,measurement_duration_seconds,optimization_reference_report_id?,recall_delta?,map50_delta?. Unsupported metrics are null, never zero. gate_policy includes required thresholds and profile memory/FPS/latency budgets; evidence metadata contains exact device/software/dataset configuration. A blocked report carries nulls plus reason in evidence_ref report and cannot approve a real release.

Public user resource serialization uses allowlisted fields; internal hashes of public artifacts may be exposed, credential hashes/session records never are. VideoSource user views expose full credential-free locator only to O, and a redacted label to other users; device config delivers its own full safe locator. Errors never echo raw locators with secrets. Source POST/PATCH produces a full ConfigVersion digest; DesiredState.config_sha256 must match downloaded config. Agent checks non-camera settings equal the signed release default and accepts only camera overrides from authenticated control plane.
