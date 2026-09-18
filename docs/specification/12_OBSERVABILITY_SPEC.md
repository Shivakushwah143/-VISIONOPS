# 12 — Observability specification

Metrics answer what; structured logs explain why; traces locate latency across work boundaries. Missing data means unknown, never healthy. P0: Prometheus, Grafana, JSON logs, OTel instrumentation with console exporter. P1: Loki/Alloy, OTel Collector and Tempo. All values are produced at runtime; this document contains metric names and target policies, not measured results.

## Metric contract

Device submits 15-second MetricSummary windows with UTC bounds, sample_count and nullable values. Counter deltas are nonnegative within the window. Values include input_frames, inferred_frames, dropped_frames, reconnects, inference_errors, worker_restarts, queue_depth, queue_depth_max, rtsp_connected?, decode_duration_histogram?, inference_duration_histogram, event_latency_histogram?, inference_samples_seconds, gpu_utilization_ratio?, gpu_memory_bytes?, gpu_temperature_celsius?, cpu_utilization_ratio, process_rss_bytes, class_counts and confidence_histograms. Histograms carry fixed bucket bounds and cumulative counts, plus sum and count; do not average p95 values. Use monotonic timers for local durations. Device capabilities state unsupported fields. Each per-camera window carries metric_summary_id, camera_id, release_id, window_start, window_end and sample_count; device_id/evidence_mode/received_at are server-derived. sample_count equals inferred_frames. Carry up to 1,024 inference_samples_seconds values per 15-second window (5 FPS sampling keeps this bounded); central rollout p95 is nearest-rank over these raw durations, while Prometheus dashboards use fixed-bucket approximations. Ingest is idempotent by metric_summary_id, verifies duration/count consistency and rejects changed-payload duplicates. Windows never mix releases or span worker restart; omit an incomplete window instead of fabricating duration. Baseline/observation need at least 300 seconds of distinct covered windows per camera; gaps do not count.

| Prometheus name | Type / unit | Collection and meaning |
| --- | --- | --- |
| visionops_camera_input_frames_total | counter / frames | Successful decoded frames; rate gives input FPS |
| visionops_inference_frames_total | counter / frames | Frames actually inferred; separate from camera FPS |
| visionops_frames_dropped_total | counter / frames | reason queue_full/stale/decoder; count at drop point |
| visionops_decode_duration_seconds | histogram | Demux/decode measured processing duration where observable, otherwise unavailable |
| visionops_inference_duration_seconds | histogram | Preprocessed input ready to prediction output ready; synchronize GPU for valid timing |
| visionops_event_latency_seconds | histogram | Final qualifying frame decode completion to local durable event decision; temporal-rule dwell reported separately |
| visionops_api_request_duration_seconds | histogram | Server request handling by route template and method/status_class |
| visionops_queue_depth | gauge / frames | Current queue depth; dashboard max from windows |
| visionops_rtsp_connected | gauge / 0 or 1 | Current RTSP connection, absent for file source |
| visionops_rtsp_reconnects_total | counter | Reconnect attempts |
| visionops_worker_restarts_total | counter | Supervised worker restarts |
| visionops_inference_errors_total | counter | Bounded error_code categories |
| visionops_gpu_utilization_ratio | gauge / 0–1 | Actual NVML/vendor reading, omitted without support |
| visionops_gpu_memory_bytes | gauge / bytes | GPU allocated/used memory with documented source |
| visionops_gpu_temperature_celsius | gauge / Celsius | Actual sensor reading |
| visionops_cpu_utilization_ratio | gauge / 0–1 | Process CPU normalized by allocated cores |
| visionops_process_memory_bytes | gauge / bytes | RSS |
| visionops_device_last_heartbeat_timestamp_seconds | gauge / epoch seconds | Server receipt of newest accepted heartbeat |
| visionops_device_state_info | gauge / 1 | Current actual_release_id and health; info series only |
| visionops_safety_events_total | counter | Generated/received event counts separated by stage |
| visionops_detection_confidence | histogram | Fixed bins per canonical class, observed predictions only |
| visionops_outbox_records | gauge | Pending safety metadata |
| visionops_outbox_lost_records_total | counter | Permanent cap/TTL loss, persisted over restart |
| visionops_artifact_validation_failures_total | counter | Hash/signature/profile failure category |
| visionops_campaign_pauses_total | counter | Gate/failure/manual reason category |
| visionops_telemetry_age_seconds | gauge | Age of latest accepted MetricSummary |

Latency buckets seconds: 0.005,0.01,0.025,0.05,0.1,0.25,0.5,1,2,5,+Inf. Confidence bins: 0,0.1,…,1. Store baseline/candidate window histograms in DB for rollout calculations, even if Prometheus is down. Exporter must persist counter aggregation state or expose resets explicitly; never repeatedly add the same window on each scrape.

## Dimensions and fleet cardinality

Detailed active-device metrics label `device_id,site_id,camera_id,evidence_mode` where applicable. `evidence_mode` real/simulated is mandatory for comparisons. Add model/release only to bounded info series and DB windows, not every histogram. Track IDs, event IDs, timestamps, raw URLs and request IDs are forbidden metric labels. Class labels only the three canonical values. In P0 expose per-camera detail for at most 50 active devices; inventory-only records contribute aggregate counts, not thousands of empty histogram series. Query high-cardinality version history in PostgreSQL, not labels. Estimate series count before increasing fleet simulation concurrency.

## Collection and dashboards

Prometheus scrapes central API/exporter every 15 seconds. Exporter reads accepted windows from PostgreSQL; it never assumes WAN access to edge endpoints. Local Compose may additionally scrape local agent `/metrics` on its private network for diagnostic views, with a separate job label to avoid double counting. Grafana dashboards: Fleet Health (connectivity/mismatch/age), Video Pipeline (FPS, drops, queue, latencies), Deployment Safety (ring, failure, convergence, baseline/candidate), Model Health (class/confidence, labeled metrics, drift), Host Health (CPU/RAM and genuine GPU sensor data).

Each chart includes window, unit, source, evidence mode and last sample time. Pipeline panels distinguish input FPS, inferred FPS, event dwell time and decode-to-event processing latency. Remote camera-capture-to-dashboard latency is not claimed without synchronized trustworthy source clocks.

## Logs and traces

JSON log fields: timestamp,severity,service,event_name,message,request_id?,trace_id?,device_id?,site_id?,camera_id?,release_id?,generation?,deployment_campaign_id?,error_code?,duration_ms?. No video pixels, passwords, credentials, RTSP secrets or full headers. Log state transitions, not every frame. Rotate local logs 5×10 MiB; pipeline errors rate-limited with counters.

Error logs are always retained locally within rotation; P0 10% head-sampled tracing cannot guarantee a span for every error. OTel spans: HTTP ingress, DB transaction, desired-state reconciliation, artifact download/validation, activation, rollback and safety batch ingestion. Propagate W3C trace context; queue replay uses a span link to original event context rather than one days-long span. Sample 10% routine operations and correlate all error logs with available trace context; no per-frame distributed traces. P0 console exporter must be bounded/nonblocking.

## Alerts and SLI/SLO candidates

| Signal | Proposed threshold | Action |
| --- | --- | --- |
| Device offline | receipt age >60 s | Warning, show last known state; campaign cannot count healthy |
| Source no progress | no decoded frame >10 s | Reconnect source; report camera failure |
| Frozen suspicion | duplicate-content hash >30 s while timestamps advance | Warn only; static scene is not proof; inspect/reconnect by policy |
| Pipeline backlog | queue depth=2 for >30 s or drop fraction >20%/1 min | Warn and inspect processing budget; no unbounded queue |
| Inference failure | any worker fatal error or 3 crashes/5 min | Local recovery; pause active campaign |
| Unknown campaign health | telemetry age >30 s | Gate waiting; pause if unavailable >60 s during running ring |
| Thermal | hardware-specific safe limit from qualified profile exceeded 60 s | Warn, reduce sampling through operator config; no universal fabricated temperature |
| Durable metadata loss | increase outbox_lost_records_total >0 | Critical data-loss notification |
| DB unavailable | readiness fails | API unavailable; edge continues locally |
| Artifact invalid | any validation failure | Reject activation, alert and audit |

Proposed SLOs for measured demo profile: 99% non-auth-error API requests succeed during a 30-minute run; fleet list p95 <1 second at 8,000 records with 10 active agents; online event metadata appears within 10 seconds after local event emission at p95. These are provisional goals. Measure denominator, window, host limits and payload size. Safety recall is an evaluated model metric, not uptime. Camera operational time and online event delivery time exclude declared source-disabled windows but report exclusions. Sustained CPU inference target is profile-qualified, not inferred from hardware TOPS.
