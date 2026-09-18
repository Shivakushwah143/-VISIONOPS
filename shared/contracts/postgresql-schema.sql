
CREATE TABLE users (
	user_id VARCHAR(36) NOT NULL, 
	email VARCHAR NOT NULL, 
	password_hash VARCHAR NOT NULL, 
	role VARCHAR NOT NULL, 
	status VARCHAR NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (user_id), 
	UNIQUE (email)
)

;

CREATE TABLE sites (
	site_id VARCHAR(36) NOT NULL, 
	name VARCHAR NOT NULL, 
	timezone VARCHAR NOT NULL, 
	status VARCHAR NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (site_id)
)

;

CREATE TABLE models (
	model_id VARCHAR(36) NOT NULL, 
	name VARCHAR, 
	task VARCHAR NOT NULL, 
	class_map JSONB NOT NULL, 
	status VARCHAR NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (model_id), 
	UNIQUE (name)
)

;

CREATE TABLE dataset_versions (
	dataset_version_id VARCHAR(36) NOT NULL, 
	name VARCHAR NOT NULL, 
	git_commit VARCHAR NOT NULL, 
	dvc_hash VARCHAR NOT NULL, 
	dvc_remote_ref VARCHAR NOT NULL, 
	taxonomy JSONB NOT NULL, 
	split_manifest_hash VARCHAR NOT NULL, 
	license_ref VARCHAR NOT NULL, 
	validation_evidence_ref VARCHAR NOT NULL, 
	status VARCHAR NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (dataset_version_id)
)

;

CREATE TABLE runtime_artifacts (
	runtime_artifact_id VARCHAR(36) NOT NULL, 
	version_label VARCHAR NOT NULL, 
	hardware_profile VARCHAR NOT NULL, 
	sha256 VARCHAR NOT NULL, 
	size_bytes INTEGER NOT NULL, 
	storage_key VARCHAR NOT NULL, 
	entrypoint VARCHAR NOT NULL, 
	compatibility JSONB NOT NULL, 
	status VARCHAR NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (runtime_artifact_id)
)

;

CREATE TABLE audit_events (
	audit_event_id VARCHAR(36) NOT NULL, 
	actor_type VARCHAR NOT NULL, 
	actor_id VARCHAR NOT NULL, 
	action VARCHAR NOT NULL, 
	entity_type VARCHAR NOT NULL, 
	entity_id VARCHAR NOT NULL, 
	reason VARCHAR NOT NULL, 
	request_id VARCHAR NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (audit_event_id)
)

;

CREATE TABLE sessions (
	session_id VARCHAR(36) NOT NULL, 
	user_id VARCHAR(36) NOT NULL, 
	token_hash VARCHAR, 
	csrf_hash VARCHAR NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (session_id), 
	FOREIGN KEY(user_id) REFERENCES users (user_id), 
	UNIQUE (token_hash)
)

;
CREATE INDEX ix_sessions_user_id ON sessions (user_id);

CREATE TABLE config_versions (
	config_version_id VARCHAR(36) NOT NULL, 
	hardware_profile VARCHAR NOT NULL, 
	schema_version INTEGER NOT NULL, 
	sha256 VARCHAR NOT NULL, 
	settings JSONB NOT NULL, 
	created_by VARCHAR(36), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (config_version_id), 
	FOREIGN KEY(created_by) REFERENCES users (user_id)
)

;
CREATE INDEX ix_config_versions_created_by ON config_versions (created_by);

CREATE TABLE training_runs (
	training_run_id VARCHAR(36) NOT NULL, 
	dataset_version_id VARCHAR(36) NOT NULL, 
	mlflow_run_id VARCHAR NOT NULL, 
	code_commit VARCHAR NOT NULL, 
	parameters JSONB NOT NULL, 
	status VARCHAR NOT NULL, 
	started_at TIMESTAMP WITH TIME ZONE, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (training_run_id), 
	FOREIGN KEY(dataset_version_id) REFERENCES dataset_versions (dataset_version_id)
)

;
CREATE INDEX ix_training_runs_dataset_version_id ON training_runs (dataset_version_id);

CREATE TABLE idempotency_records (
	idempotency_record_id VARCHAR(36) NOT NULL, 
	user_id VARCHAR(36) NOT NULL, 
	route VARCHAR NOT NULL, 
	key VARCHAR NOT NULL, 
	request_sha256 VARCHAR NOT NULL, 
	response_status INTEGER NOT NULL, 
	response_body JSONB NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (idempotency_record_id), 
	UNIQUE (user_id, route, key), 
	FOREIGN KEY(user_id) REFERENCES users (user_id)
)

;
CREATE INDEX ix_idempotency_records_user_id ON idempotency_records (user_id);

CREATE TABLE model_versions (
	model_version_id VARCHAR(36) NOT NULL, 
	model_id VARCHAR(36) NOT NULL, 
	training_run_id VARCHAR(36) NOT NULL, 
	mlflow_model_name VARCHAR NOT NULL, 
	mlflow_model_version VARCHAR NOT NULL, 
	version_label VARCHAR NOT NULL, 
	status VARCHAR NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (model_version_id), 
	UNIQUE (model_id, version_label), 
	FOREIGN KEY(model_id) REFERENCES models (model_id), 
	FOREIGN KEY(training_run_id) REFERENCES training_runs (training_run_id)
)

;
CREATE INDEX ix_model_versions_model_id ON model_versions (model_id);
CREATE INDEX ix_model_versions_training_run_id ON model_versions (training_run_id);

CREATE TABLE model_artifacts (
	model_artifact_id VARCHAR(36) NOT NULL, 
	model_version_id VARCHAR(36) NOT NULL, 
	format VARCHAR NOT NULL, 
	precision VARCHAR NOT NULL, 
	hardware_profile VARCHAR NOT NULL, 
	sha256 VARCHAR NOT NULL, 
	size_bytes INTEGER NOT NULL, 
	storage_key VARCHAR NOT NULL, 
	input_shape JSONB NOT NULL, 
	class_map JSONB NOT NULL, 
	compatibility JSONB NOT NULL, 
	status VARCHAR NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (model_artifact_id), 
	FOREIGN KEY(model_version_id) REFERENCES model_versions (model_version_id)
)

;
CREATE INDEX ix_model_artifacts_model_version_id ON model_artifacts (model_version_id);

CREATE TABLE evaluation_reports (
	evaluation_report_id VARCHAR(36) NOT NULL, 
	model_version_id VARCHAR(36) NOT NULL, 
	model_artifact_id VARCHAR(36) NOT NULL, 
	dataset_version_id VARCHAR(36) NOT NULL, 
	hardware_profile VARCHAR NOT NULL, 
	evidence_mode VARCHAR NOT NULL, 
	metrics JSONB NOT NULL, 
	gate_policy JSONB NOT NULL, 
	result VARCHAR NOT NULL, 
	evidence_ref VARCHAR NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (evaluation_report_id), 
	FOREIGN KEY(model_version_id) REFERENCES model_versions (model_version_id), 
	FOREIGN KEY(model_artifact_id) REFERENCES model_artifacts (model_artifact_id), 
	FOREIGN KEY(dataset_version_id) REFERENCES dataset_versions (dataset_version_id)
)

;
CREATE INDEX ix_evaluation_reports_model_artifact_id ON evaluation_reports (model_artifact_id);
CREATE INDEX ix_evaluation_reports_dataset_version_id ON evaluation_reports (dataset_version_id);
CREATE INDEX ix_evaluation_reports_model_version_id ON evaluation_reports (model_version_id);

CREATE TABLE releases (
	release_id VARCHAR(36) NOT NULL, 
	model_artifact_id VARCHAR(36) NOT NULL, 
	runtime_artifact_id VARCHAR(36) NOT NULL, 
	config_version_id VARCHAR(36) NOT NULL, 
	evaluation_report_id VARCHAR(36) NOT NULL, 
	hardware_profile VARCHAR NOT NULL, 
	evidence_mode VARCHAR NOT NULL, 
	manifest_sha256 VARCHAR NOT NULL, 
	manifest JSONB NOT NULL, 
	signature VARCHAR, 
	key_id VARCHAR, 
	approved_by VARCHAR(36), 
	approved_at TIMESTAMP WITH TIME ZONE, 
	status VARCHAR NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (release_id), 
	FOREIGN KEY(model_artifact_id) REFERENCES model_artifacts (model_artifact_id), 
	FOREIGN KEY(runtime_artifact_id) REFERENCES runtime_artifacts (runtime_artifact_id), 
	FOREIGN KEY(config_version_id) REFERENCES config_versions (config_version_id), 
	FOREIGN KEY(evaluation_report_id) REFERENCES evaluation_reports (evaluation_report_id), 
	FOREIGN KEY(approved_by) REFERENCES users (user_id)
)

;
CREATE INDEX ix_releases_config_version_id ON releases (config_version_id);
CREATE INDEX ix_releases_evaluation_report_id ON releases (evaluation_report_id);
CREATE INDEX ix_releases_approved_by ON releases (approved_by);
CREATE INDEX ix_releases_runtime_artifact_id ON releases (runtime_artifact_id);
CREATE INDEX ix_releases_model_artifact_id ON releases (model_artifact_id);

CREATE TABLE deployment_campaigns (
	deployment_campaign_id VARCHAR(36) NOT NULL, 
	release_id VARCHAR(36) NOT NULL, 
	mode VARCHAR NOT NULL, 
	target_device_ids JSONB NOT NULL, 
	status VARCHAR NOT NULL, 
	current_ring INTEGER NOT NULL, 
	ring_started_at TIMESTAMP WITH TIME ZONE, 
	observation_started_at TIMESTAMP WITH TIME ZONE, 
	failure_windows INTEGER NOT NULL, 
	created_by VARCHAR(36) NOT NULL, 
	reason VARCHAR NOT NULL, 
	policy_snapshot JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (deployment_campaign_id), 
	FOREIGN KEY(release_id) REFERENCES releases (release_id), 
	FOREIGN KEY(created_by) REFERENCES users (user_id)
)

;
CREATE INDEX ix_deployment_campaigns_created_by ON deployment_campaigns (created_by);
CREATE INDEX ix_deployment_campaigns_release_id ON deployment_campaigns (release_id);

CREATE TABLE devices (
	device_id VARCHAR(36) NOT NULL, 
	site_id VARCHAR(36) NOT NULL, 
	name VARCHAR NOT NULL, 
	mode VARCHAR NOT NULL, 
	hardware_profile VARCHAR NOT NULL, 
	registration_status VARCHAR NOT NULL, 
	credential_hash VARCHAR, 
	last_boot_id VARCHAR, 
	last_sequence INTEGER NOT NULL, 
	last_heartbeat_at TIMESTAMP WITH TIME ZONE, 
	desired_generation INTEGER NOT NULL, 
	desired_release_id VARCHAR(36), 
	desired_config_version_id VARCHAR(36), 
	actual_release_id VARCHAR(36), 
	actual_config_version_id VARCHAR(36), 
	applied_generation INTEGER NOT NULL, 
	agent_state VARCHAR NOT NULL, 
	health_status VARCHAR NOT NULL, 
	active_deployment_campaign_id VARCHAR(36), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (device_id), 
	FOREIGN KEY(site_id) REFERENCES sites (site_id), 
	FOREIGN KEY(desired_release_id) REFERENCES releases (release_id), 
	FOREIGN KEY(desired_config_version_id) REFERENCES config_versions (config_version_id), 
	FOREIGN KEY(actual_release_id) REFERENCES releases (release_id), 
	FOREIGN KEY(actual_config_version_id) REFERENCES config_versions (config_version_id), 
	FOREIGN KEY(active_deployment_campaign_id) REFERENCES deployment_campaigns (deployment_campaign_id)
)

;
CREATE INDEX ix_devices_site_id ON devices (site_id);
CREATE INDEX ix_devices_desired_release_id ON devices (desired_release_id);
CREATE INDEX ix_devices_actual_release_id ON devices (actual_release_id);
CREATE INDEX ix_devices_active_deployment_campaign_id ON devices (active_deployment_campaign_id);
CREATE INDEX ix_devices_desired_config_version_id ON devices (desired_config_version_id);
CREATE INDEX ix_devices_actual_config_version_id ON devices (actual_config_version_id);

CREATE TABLE cameras (
	camera_id VARCHAR(36) NOT NULL, 
	site_id VARCHAR(36) NOT NULL, 
	device_id VARCHAR(36) NOT NULL, 
	name VARCHAR NOT NULL, 
	status VARCHAR NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (camera_id), 
	FOREIGN KEY(site_id) REFERENCES sites (site_id), 
	FOREIGN KEY(device_id) REFERENCES devices (device_id)
)

;
CREATE INDEX ix_cameras_site_id ON cameras (site_id);
CREATE INDEX ix_cameras_device_id ON cameras (device_id);

CREATE TABLE enrollment_tokens (
	enrollment_token_id VARCHAR(36) NOT NULL, 
	device_id VARCHAR(36) NOT NULL, 
	token_hash VARCHAR, 
	expires_at TIMESTAMP WITH TIME ZONE, 
	consumed_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (enrollment_token_id), 
	FOREIGN KEY(device_id) REFERENCES devices (device_id), 
	UNIQUE (token_hash)
)

;
CREATE INDEX ix_enrollment_tokens_device_id ON enrollment_tokens (device_id);

CREATE TABLE device_assignments (
	device_assignment_id VARCHAR(36) NOT NULL, 
	device_id VARCHAR(36) NOT NULL, 
	generation INTEGER NOT NULL, 
	release_id VARCHAR(36) NOT NULL, 
	config_version_id VARCHAR(36) NOT NULL, 
	reason VARCHAR NOT NULL, 
	deployment_campaign_id VARCHAR(36), 
	created_by VARCHAR NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (device_assignment_id), 
	UNIQUE (device_id, generation), 
	FOREIGN KEY(device_id) REFERENCES devices (device_id), 
	FOREIGN KEY(release_id) REFERENCES releases (release_id), 
	FOREIGN KEY(config_version_id) REFERENCES config_versions (config_version_id), 
	FOREIGN KEY(deployment_campaign_id) REFERENCES deployment_campaigns (deployment_campaign_id)
)

;
CREATE INDEX ix_device_assignments_deployment_campaign_id ON device_assignments (deployment_campaign_id);
CREATE INDEX ix_device_assignments_release_id ON device_assignments (release_id);
CREATE INDEX ix_device_assignments_config_version_id ON device_assignments (config_version_id);
CREATE INDEX ix_device_assignments_device_id ON device_assignments (device_id);

CREATE TABLE device_boots (
	device_boot_id VARCHAR(36) NOT NULL, 
	device_id VARCHAR(36) NOT NULL, 
	boot_id VARCHAR NOT NULL, 
	retired_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (device_boot_id), 
	UNIQUE (device_id, boot_id), 
	FOREIGN KEY(device_id) REFERENCES devices (device_id)
)

;
CREATE INDEX ix_device_boots_device_id ON device_boots (device_id);

CREATE TABLE device_heartbeats (
	heartbeat_id VARCHAR(36) NOT NULL, 
	device_id VARCHAR(36) NOT NULL, 
	boot_id VARCHAR NOT NULL, 
	sequence INTEGER NOT NULL, 
	observed_at TIMESTAMP WITH TIME ZONE, 
	received_at TIMESTAMP WITH TIME ZONE, 
	actual_release_id VARCHAR(36), 
	actual_config_version_id VARCHAR(36), 
	applied_generation INTEGER NOT NULL, 
	agent_state VARCHAR NOT NULL, 
	health_status VARCHAR NOT NULL, 
	source_states JSONB NOT NULL, 
	capabilities JSONB NOT NULL, 
	rejected_generation INTEGER, 
	last_error_code VARCHAR, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (heartbeat_id), 
	UNIQUE (device_id, boot_id, sequence), 
	FOREIGN KEY(device_id) REFERENCES devices (device_id), 
	FOREIGN KEY(actual_release_id) REFERENCES releases (release_id), 
	FOREIGN KEY(actual_config_version_id) REFERENCES config_versions (config_version_id)
)

;
CREATE INDEX ix_device_heartbeats_device_id ON device_heartbeats (device_id);
CREATE INDEX ix_device_heartbeats_actual_release_id ON device_heartbeats (actual_release_id);
CREATE INDEX ix_device_heartbeats_actual_config_version_id ON device_heartbeats (actual_config_version_id);

CREATE TABLE deployment_targets (
	deployment_target_id VARCHAR(36) NOT NULL, 
	deployment_campaign_id VARCHAR(36) NOT NULL, 
	device_id VARCHAR(36) NOT NULL, 
	ring INTEGER NOT NULL, 
	status VARCHAR NOT NULL, 
	previous_release_id VARCHAR(36), 
	previous_config_version_id VARCHAR(36), 
	assigned_generation INTEGER, 
	rollback_generation INTEGER, 
	baseline_metrics JSONB NOT NULL, 
	permit_expires_at TIMESTAMP WITH TIME ZONE, 
	assigned_config_version_id VARCHAR(36), 
	last_error_code VARCHAR, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (deployment_target_id), 
	UNIQUE (deployment_campaign_id, device_id), 
	FOREIGN KEY(deployment_campaign_id) REFERENCES deployment_campaigns (deployment_campaign_id), 
	FOREIGN KEY(device_id) REFERENCES devices (device_id), 
	FOREIGN KEY(previous_release_id) REFERENCES releases (release_id), 
	FOREIGN KEY(previous_config_version_id) REFERENCES config_versions (config_version_id), 
	FOREIGN KEY(assigned_config_version_id) REFERENCES config_versions (config_version_id)
)

;
CREATE INDEX ix_deployment_targets_previous_config_version_id ON deployment_targets (previous_config_version_id);
CREATE INDEX ix_deployment_targets_previous_release_id ON deployment_targets (previous_release_id);
CREATE INDEX ix_deployment_targets_device_id ON deployment_targets (device_id);
CREATE INDEX ix_deployment_targets_assigned_config_version_id ON deployment_targets (assigned_config_version_id);
CREATE INDEX ix_deployment_targets_deployment_campaign_id ON deployment_targets (deployment_campaign_id);

CREATE TABLE deployment_events (
	deployment_event_id VARCHAR(36) NOT NULL, 
	deployment_campaign_id VARCHAR(36) NOT NULL, 
	device_id VARCHAR(36), 
	actor_type VARCHAR NOT NULL, 
	actor_id VARCHAR NOT NULL, 
	event_type VARCHAR NOT NULL, 
	generation INTEGER, 
	before_state JSONB NOT NULL, 
	after_state JSONB NOT NULL, 
	reason VARCHAR NOT NULL, 
	evidence JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (deployment_event_id), 
	FOREIGN KEY(deployment_campaign_id) REFERENCES deployment_campaigns (deployment_campaign_id), 
	FOREIGN KEY(device_id) REFERENCES devices (device_id)
)

;
CREATE INDEX ix_deployment_events_deployment_campaign_id ON deployment_events (deployment_campaign_id);
CREATE INDEX ix_deployment_events_device_id ON deployment_events (device_id);

CREATE TABLE video_sources (
	video_source_id VARCHAR(36) NOT NULL, 
	camera_id VARCHAR(36) NOT NULL, 
	kind VARCHAR NOT NULL, 
	locator VARCHAR NOT NULL, 
	credential_ref VARCHAR, 
	enabled BOOLEAN NOT NULL, 
	loop BOOLEAN NOT NULL, 
	source_revision INTEGER NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (video_source_id), 
	UNIQUE (camera_id), 
	FOREIGN KEY(camera_id) REFERENCES cameras (camera_id)
)

;

CREATE TABLE metric_summaries (
	metric_summary_id VARCHAR(36) NOT NULL, 
	device_id VARCHAR(36) NOT NULL, 
	camera_id VARCHAR(36), 
	release_id VARCHAR(36), 
	window_start TIMESTAMP WITH TIME ZONE, 
	window_end TIMESTAMP WITH TIME ZONE, 
	sample_count INTEGER NOT NULL, 
	evidence_mode VARCHAR NOT NULL, 
	values JSONB NOT NULL, 
	received_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (metric_summary_id), 
	FOREIGN KEY(device_id) REFERENCES devices (device_id), 
	FOREIGN KEY(camera_id) REFERENCES cameras (camera_id), 
	FOREIGN KEY(release_id) REFERENCES releases (release_id)
)

;
CREATE INDEX ix_metric_summaries_camera_id ON metric_summaries (camera_id);
CREATE INDEX ix_metric_summaries_release_id ON metric_summaries (release_id);
CREATE INDEX ix_metric_summaries_device_id ON metric_summaries (device_id);

CREATE TABLE detection_events (
	detection_event_id VARCHAR(36) NOT NULL, 
	device_id VARCHAR(36) NOT NULL, 
	camera_id VARCHAR(36) NOT NULL, 
	stream_session_id VARCHAR NOT NULL, 
	observed_at TIMESTAMP WITH TIME ZONE, 
	received_at TIMESTAMP WITH TIME ZONE, 
	release_id VARCHAR(36) NOT NULL, 
	model_version_id VARCHAR(36) NOT NULL, 
	config_version_id VARCHAR(36) NOT NULL, 
	evidence_mode VARCHAR NOT NULL, 
	payload_sha256 VARCHAR NOT NULL, 
	frame_sequence INTEGER NOT NULL, 
	detections JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (detection_event_id), 
	FOREIGN KEY(device_id) REFERENCES devices (device_id), 
	FOREIGN KEY(camera_id) REFERENCES cameras (camera_id), 
	FOREIGN KEY(release_id) REFERENCES releases (release_id), 
	FOREIGN KEY(model_version_id) REFERENCES model_versions (model_version_id), 
	FOREIGN KEY(config_version_id) REFERENCES config_versions (config_version_id)
)

;
CREATE INDEX ix_detection_events_model_version_id ON detection_events (model_version_id);
CREATE INDEX ix_detection_events_release_id ON detection_events (release_id);
CREATE INDEX ix_detection_events_config_version_id ON detection_events (config_version_id);
CREATE INDEX ix_detection_events_device_id ON detection_events (device_id);
CREATE INDEX ix_detection_events_camera_id ON detection_events (camera_id);

CREATE TABLE safety_events (
	safety_event_id VARCHAR(36) NOT NULL, 
	device_id VARCHAR(36) NOT NULL, 
	camera_id VARCHAR(36) NOT NULL, 
	stream_session_id VARCHAR NOT NULL, 
	observed_at TIMESTAMP WITH TIME ZONE, 
	received_at TIMESTAMP WITH TIME ZONE, 
	release_id VARCHAR(36) NOT NULL, 
	model_version_id VARCHAR(36) NOT NULL, 
	config_version_id VARCHAR(36) NOT NULL, 
	evidence_mode VARCHAR NOT NULL, 
	payload_sha256 VARCHAR NOT NULL, 
	track_id VARCHAR NOT NULL, 
	event_type VARCHAR NOT NULL, 
	window_start TIMESTAMP WITH TIME ZONE, 
	window_end TIMESTAMP WITH TIME ZONE, 
	supporting_frames INTEGER NOT NULL, 
	confidence FLOAT, 
	bbox JSONB NOT NULL, 
	review_status VARCHAR NOT NULL, 
	acknowledged_by VARCHAR(36), 
	acknowledged_at TIMESTAMP WITH TIME ZONE, 
	evidence_key VARCHAR, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (safety_event_id), 
	FOREIGN KEY(device_id) REFERENCES devices (device_id), 
	FOREIGN KEY(camera_id) REFERENCES cameras (camera_id), 
	FOREIGN KEY(release_id) REFERENCES releases (release_id), 
	FOREIGN KEY(model_version_id) REFERENCES model_versions (model_version_id), 
	FOREIGN KEY(config_version_id) REFERENCES config_versions (config_version_id), 
	FOREIGN KEY(acknowledged_by) REFERENCES users (user_id)
)

;
CREATE INDEX ix_safety_events_release_id ON safety_events (release_id);
CREATE INDEX ix_safety_events_config_version_id ON safety_events (config_version_id);
CREATE INDEX ix_safety_events_camera_id ON safety_events (camera_id);
CREATE INDEX ix_safety_events_device_id ON safety_events (device_id);
CREATE INDEX ix_safety_events_model_version_id ON safety_events (model_version_id);
CREATE INDEX ix_safety_events_acknowledged_by ON safety_events (acknowledged_by);

CREATE TABLE drift_signals (
	drift_signal_id VARCHAR(36) NOT NULL, 
	camera_id VARCHAR(36) NOT NULL, 
	model_version_id VARCHAR(36) NOT NULL, 
	reference_dataset_version_id VARCHAR(36) NOT NULL, 
	window_start TIMESTAMP WITH TIME ZONE, 
	window_end TIMESTAMP WITH TIME ZONE, 
	reference_window_start TIMESTAMP WITH TIME ZONE, 
	reference_window_end TIMESTAMP WITH TIME ZONE, 
	sample_count INTEGER NOT NULL, 
	method VARCHAR NOT NULL, 
	score FLOAT, 
	threshold FLOAT, 
	status VARCHAR NOT NULL, 
	reason VARCHAR, 
	reference_histogram JSONB NOT NULL, 
	current_histogram JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (drift_signal_id), 
	UNIQUE (camera_id, model_version_id, window_start, window_end, method), 
	FOREIGN KEY(camera_id) REFERENCES cameras (camera_id), 
	FOREIGN KEY(model_version_id) REFERENCES model_versions (model_version_id), 
	FOREIGN KEY(reference_dataset_version_id) REFERENCES dataset_versions (dataset_version_id)
)

;
CREATE INDEX ix_drift_signals_camera_id ON drift_signals (camera_id);
CREATE INDEX ix_drift_signals_model_version_id ON drift_signals (model_version_id);
CREATE INDEX ix_drift_signals_reference_dataset_version_id ON drift_signals (reference_dataset_version_id);

CREATE TABLE hard_examples (
	hard_example_id VARCHAR(36) NOT NULL, 
	device_id VARCHAR(36) NOT NULL, 
	camera_id VARCHAR(36) NOT NULL, 
	safety_event_id VARCHAR(36), 
	drift_signal_id VARCHAR(36), 
	evidence_key VARCHAR NOT NULL, 
	observed_at TIMESTAMP WITH TIME ZONE, 
	release_id VARCHAR(36) NOT NULL, 
	confidence FLOAT, 
	capture_reason VARCHAR NOT NULL, 
	labels JSONB NOT NULL, 
	review_reason VARCHAR, 
	status VARCHAR NOT NULL, 
	reviewed_by VARCHAR(36), 
	exported_dataset_version_id VARCHAR(36), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (hard_example_id), 
	FOREIGN KEY(device_id) REFERENCES devices (device_id), 
	FOREIGN KEY(camera_id) REFERENCES cameras (camera_id), 
	FOREIGN KEY(safety_event_id) REFERENCES safety_events (safety_event_id), 
	FOREIGN KEY(drift_signal_id) REFERENCES drift_signals (drift_signal_id), 
	FOREIGN KEY(release_id) REFERENCES releases (release_id), 
	FOREIGN KEY(reviewed_by) REFERENCES users (user_id), 
	FOREIGN KEY(exported_dataset_version_id) REFERENCES dataset_versions (dataset_version_id)
)

;
CREATE INDEX ix_hard_examples_safety_event_id ON hard_examples (safety_event_id);
CREATE INDEX ix_hard_examples_reviewed_by ON hard_examples (reviewed_by);
CREATE INDEX ix_hard_examples_release_id ON hard_examples (release_id);
CREATE INDEX ix_hard_examples_camera_id ON hard_examples (camera_id);
CREATE INDEX ix_hard_examples_drift_signal_id ON hard_examples (drift_signal_id);
CREATE INDEX ix_hard_examples_device_id ON hard_examples (device_id);
CREATE INDEX ix_hard_examples_exported_dataset_version_id ON hard_examples (exported_dataset_version_id);

CREATE TABLE retraining_requests (
	retraining_request_id VARCHAR(36) NOT NULL, 
	drift_signal_id VARCHAR(36) NOT NULL, 
	hard_example_ids JSONB NOT NULL, 
	reason VARCHAR NOT NULL, 
	requested_by VARCHAR(36) NOT NULL, 
	status VARCHAR NOT NULL, 
	training_run_id VARCHAR(36), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (retraining_request_id), 
	FOREIGN KEY(drift_signal_id) REFERENCES drift_signals (drift_signal_id), 
	FOREIGN KEY(requested_by) REFERENCES users (user_id), 
	FOREIGN KEY(training_run_id) REFERENCES training_runs (training_run_id)
)

;
CREATE INDEX ix_retraining_requests_drift_signal_id ON retraining_requests (drift_signal_id);
CREATE INDEX ix_retraining_requests_requested_by ON retraining_requests (requested_by);
CREATE INDEX ix_retraining_requests_training_run_id ON retraining_requests (training_run_id);
