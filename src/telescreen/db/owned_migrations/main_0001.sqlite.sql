-- Frozen owner baseline at shared revision 0009_inventory_counter.

-- Schema verified against db/schema/telescreen.nodetto.jsonl.

CREATE TABLE alert_fires (
	id VARCHAR(36) NOT NULL,
	rule_id VARCHAR(36) NOT NULL,
	issue_id VARCHAR(36) NOT NULL,
	trigger_kind VARCHAR(32) NOT NULL,
	outcome VARCHAR(32) NOT NULL,
	notification_count INTEGER NOT NULL,
	run_id VARCHAR(36),
	error_code VARCHAR(32),
	fired_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_alert_fires_rule_issue_at ON alert_fires (rule_id, issue_id, fired_at);

CREATE TABLE alert_rules (
	id VARCHAR(36) NOT NULL,
	name VARCHAR(64) NOT NULL,
	enabled BOOLEAN NOT NULL,
	trigger_kind VARCHAR(32) NOT NULL,
	threshold INTEGER,
	window_minutes INTEGER,
	filters_json TEXT NOT NULL,
	actions_json TEXT NOT NULL,
	action_interval_minutes INTEGER NOT NULL,
	muted_until DATETIME,
	last_fired_at DATETIME,
	created_by VARCHAR(256) NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (name)
);

CREATE TABLE api_tokens (
	id VARCHAR(36) NOT NULL,
	label VARCHAR(128) NOT NULL,
	token_hash VARCHAR(64) NOT NULL,
	scopes VARCHAR(512) NOT NULL,
	member_id VARCHAR(36),
	created_at DATETIME NOT NULL,
	revoked_at DATETIME,
	PRIMARY KEY (id),
	UNIQUE (token_hash)
);

CREATE INDEX ix_api_tokens_member_id ON api_tokens (member_id);

CREATE TABLE audit_chain_head (
	id INTEGER NOT NULL,
	seq INTEGER NOT NULL,
	hash VARCHAR(64) NOT NULL,
	PRIMARY KEY (id)
);

CREATE TABLE audit_receipts (
	id VARCHAR(36) NOT NULL,
	payload_sha256 VARCHAR(64) NOT NULL,
	applied_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE TABLE audit_records (
	seq INTEGER NOT NULL,
	id VARCHAR(36) NOT NULL,
	at DATETIME NOT NULL,
	actor_kind VARCHAR(32) NOT NULL,
	actor_ref VARCHAR(256) NOT NULL,
	action VARCHAR(128) NOT NULL,
	target_kind VARCHAR(64) NOT NULL,
	target_id VARCHAR(36) NOT NULL,
	summary TEXT NOT NULL,
	details_json TEXT NOT NULL,
	prev_hash VARCHAR(64) NOT NULL,
	hash VARCHAR(64) NOT NULL,
	PRIMARY KEY (seq),
	UNIQUE (id)
);

CREATE INDEX ix_audit_records_at ON audit_records (at);

CREATE TABLE collector_configs (
	id VARCHAR(36) NOT NULL,
	name VARCHAR(128) NOT NULL,
	kind VARCHAR(32) NOT NULL,
	trigger_mode VARCHAR(32) NOT NULL,
	cron VARCHAR(128),
	sweep_cron VARCHAR(128),
	sweep_max_concurrency INTEGER NOT NULL,
	selector_json TEXT NOT NULL,
	credential_ref VARCHAR(512),
	cursor_json TEXT,
	enabled BOOLEAN NOT NULL,
	last_scheduled_at DATETIME,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	inventory_mode VARCHAR(16) DEFAULT 'sensitive_only' NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (name)
);

CREATE TABLE collector_health (
	collector_config_id VARCHAR(36) NOT NULL,
	last_success_at DATETIME,
	last_failure_at DATETIME,
	consecutive_failures INTEGER DEFAULT '0' NOT NULL,
	last_event_at DATETIME,
	watch_expires_at DATETIME,
	open_conditions VARCHAR(256) DEFAULT '' NOT NULL,
	alerted_at DATETIME,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (collector_config_id)
);

CREATE TABLE counters (
	name VARCHAR(64) NOT NULL,
	value INTEGER NOT NULL,
	PRIMARY KEY (name)
);

CREATE TABLE engine_tasks (
	id VARCHAR(36) NOT NULL,
	kind VARCHAR(64) NOT NULL,
	ref_id VARCHAR(36) NOT NULL,
	payload_json TEXT NOT NULL,
	status VARCHAR(32) NOT NULL,
	available_at DATETIME NOT NULL,
	lease_owner VARCHAR(128),
	lease_expires_at DATETIME,
	attempts INTEGER NOT NULL,
	max_attempts INTEGER NOT NULL,
	idempotency_key VARCHAR(256) NOT NULL,
	last_error_code VARCHAR(64),
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (idempotency_key)
);

CREATE INDEX ix_engine_tasks_available_at ON engine_tasks (available_at);

CREATE INDEX ix_engine_tasks_ref_id ON engine_tasks (ref_id);

CREATE INDEX ix_engine_tasks_status ON engine_tasks (status);

CREATE TABLE escalation_policies (
	id VARCHAR(36) NOT NULL,
	name VARCHAR(128) NOT NULL,
	tiers_json TEXT NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (name)
);

CREATE TABLE evidence (
	id VARCHAR(36) NOT NULL,
	issue_id VARCHAR(36) NOT NULL,
	run_id VARCHAR(36),
	step_run_id VARCHAR(36),
	display_filename VARCHAR(512) NOT NULL,
	content_type VARCHAR(128) NOT NULL,
	size_bytes INTEGER NOT NULL,
	sha256 VARCHAR(64),
	storage_key VARCHAR(512),
	verdict VARCHAR(32) NOT NULL,
	rejection_rule_classes TEXT NOT NULL,
	uploaded_by VARCHAR(128) NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_evidence_issue_id ON evidence (issue_id);

CREATE INDEX ix_evidence_run_id ON evidence (run_id);

CREATE TABLE external_issue_links (
	id VARCHAR(36) NOT NULL,
	issue_id VARCHAR(36) NOT NULL,
	kind VARCHAR(16) NOT NULL,
	url VARCHAR(2048) NOT NULL,
	title VARCHAR(200) NOT NULL,
	external_ref VARCHAR(256),
	created_by VARCHAR(256) NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (issue_id, url)
);

CREATE INDEX ix_external_issue_links_issue_id ON external_issue_links (issue_id);

CREATE TABLE findings (
	id VARCHAR(36) NOT NULL,
	issue_id VARCHAR(36) NOT NULL,
	scan_job_id VARCHAR(36),
	source VARCHAR(32) NOT NULL,
	resource_key VARCHAR(64) NOT NULL,
	display_locator TEXT NOT NULL,
	location TEXT NOT NULL,
	rule_id VARCHAR(128) NOT NULL,
	rule_class VARCHAR(32) NOT NULL,
	detector VARCHAR(32) NOT NULL,
	confidence FLOAT NOT NULL,
	masked_snippet TEXT NOT NULL,
	fingerprint VARCHAR(64) NOT NULL,
	fingerprint_key_id VARCHAR(64) NOT NULL,
	observed_at DATETIME NOT NULL,
	fingerprint_key_version INTEGER DEFAULT '1' NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_findings_observation UNIQUE (issue_id, scan_job_id, fingerprint)
);

CREATE INDEX ix_findings_issue_id ON findings (issue_id);

CREATE INDEX ix_findings_key_fingerprint ON findings (fingerprint_key_version, fingerprint);

CREATE TABLE human_tasks (
	id VARCHAR(36) NOT NULL,
	run_id VARCHAR(36) NOT NULL,
	step_run_id VARCHAR(36) NOT NULL,
	kind VARCHAR(32) NOT NULL,
	title VARCHAR(512) NOT NULL,
	assignee_ref VARCHAR(256) NOT NULL,
	escalation_policy_id VARCHAR(36),
	tier_index INTEGER NOT NULL,
	due_at DATETIME,
	status VARCHAR(32) NOT NULL,
	outcome VARCHAR(32),
	comment TEXT,
	completed_by VARCHAR(128),
	completed_at DATETIME,
	PRIMARY KEY (id),
	UNIQUE (step_run_id)
);

CREATE INDEX ix_human_tasks_due_at ON human_tasks (due_at);

CREATE INDEX ix_human_tasks_run_id ON human_tasks (run_id);

CREATE INDEX ix_human_tasks_status ON human_tasks (status);

CREATE TABLE integration_checks (
	kind VARCHAR(32) NOT NULL,
	checked_at DATETIME NOT NULL,
	ok BOOLEAN NOT NULL,
	error_code VARCHAR(32),
	PRIMARY KEY (kind)
);

CREATE TABLE inventory_outbox (
	id VARCHAR(36) NOT NULL,
	stream_id VARCHAR(256) NOT NULL,
	sequence INTEGER NOT NULL,
	payload_json TEXT NOT NULL,
	recorded_at DATETIME NOT NULL,
	delivered_at DATETIME,
	PRIMARY KEY (id),
	CONSTRAINT uq_inventory_outbox_stream_sequence UNIQUE (stream_id, sequence)
);

CREATE TABLE inventory_outbox_streams (
	id VARCHAR(256) NOT NULL,
	last_sequence INTEGER NOT NULL,
	PRIMARY KEY (id)
);

CREATE TABLE issue_bookmarks (
	issue_id VARCHAR(36) NOT NULL,
	member_id VARCHAR(36) NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (issue_id, member_id)
);

CREATE TABLE issue_comments (
	id VARCHAR(36) NOT NULL,
	issue_id VARCHAR(36) NOT NULL,
	author_ref VARCHAR(256) NOT NULL,
	body TEXT NOT NULL,
	created_at DATETIME NOT NULL,
	edited_at DATETIME,
	deleted_at DATETIME,
	PRIMARY KEY (id)
);

CREATE INDEX ix_issue_comments_issue_id ON issue_comments (issue_id);

CREATE TABLE issue_exceptions (
	id VARCHAR(36) NOT NULL,
	issue_id VARCHAR(36) NOT NULL,
	reason TEXT NOT NULL,
	duration VARCHAR(32) NOT NULL,
	status VARCHAR(32) NOT NULL,
	requested_by VARCHAR(256) NOT NULL,
	approver_ref VARCHAR(256),
	decided_by VARCHAR(256),
	decided_at DATETIME,
	expires_at DATETIME,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_issue_exceptions_issue ON issue_exceptions (issue_id);

CREATE INDEX ix_issue_exceptions_status_expiry ON issue_exceptions (status, expires_at);

CREATE TABLE issue_history (
	id VARCHAR(36) NOT NULL,
	issue_id VARCHAR(36) NOT NULL,
	kind VARCHAR(32) NOT NULL,
	actor_ref VARCHAR(256) NOT NULL,
	from_value TEXT,
	to_value TEXT,
	note TEXT,
	at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_issue_history_issue_id ON issue_history (issue_id);

CREATE TABLE issue_mentions (
	comment_id VARCHAR(36) NOT NULL,
	member_id VARCHAR(36) NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (comment_id, member_id)
);

CREATE TABLE issue_merges (
	id VARCHAR(36) NOT NULL,
	primary_issue_id VARCHAR(36) NOT NULL,
	merged_issue_id VARCHAR(36) NOT NULL,
	merged_by VARCHAR(256) NOT NULL,
	merged_at DATETIME NOT NULL,
	unmerged_by VARCHAR(256),
	unmerged_at DATETIME,
	PRIMARY KEY (id)
);

CREATE INDEX ix_issue_merges_merged_issue_id ON issue_merges (merged_issue_id);

CREATE INDEX ix_issue_merges_primary_issue_id ON issue_merges (primary_issue_id);

CREATE TABLE issue_subscriptions (
	issue_id VARCHAR(36) NOT NULL,
	member_id VARCHAR(36) NOT NULL,
	subscribed BOOLEAN NOT NULL,
	reason VARCHAR(16) NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (issue_id, member_id)
);

CREATE TABLE issues (
	id VARCHAR(36) NOT NULL,
	number INTEGER NOT NULL,
	grouping_key VARCHAR(64) NOT NULL,
	source VARCHAR(32) NOT NULL,
	resource_key VARCHAR(64) NOT NULL,
	display_locator TEXT NOT NULL,
	resource_ref_json TEXT,
	collector_config_id VARCHAR(36),
	display_title VARCHAR(512) NOT NULL,
	rule_class VARCHAR(32) NOT NULL,
	severity VARCHAR(32) NOT NULL,
	status VARCHAR(32) NOT NULL,
	assignee_member_id VARCHAR(36),
	assignee_team_id VARCHAR(36),
	first_seen DATETIME NOT NULL,
	last_seen DATETIME NOT NULL,
	last_checked_at DATETIME NOT NULL,
	sweep_covered BOOLEAN NOT NULL,
	times_seen INTEGER NOT NULL,
	distinct_fingerprints INTEGER NOT NULL,
	regression_count INTEGER NOT NULL,
	resolved_at DATETIME,
	resolution VARCHAR(32),
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	base_severity VARCHAR(32) DEFAULT 'medium' NOT NULL,
	exposure_visibility VARCHAR(32) DEFAULT 'unknown' NOT NULL,
	exposure_json TEXT,
	exposure_changed_at DATETIME,
	placement_keys_json TEXT,
	resource_modified_at DATETIME,
	owner_ref VARCHAR(80),
	policy_name VARCHAR(64),
	policy_version INTEGER,
	risk_score INTEGER DEFAULT '0' NOT NULL,
	asset_tier VARCHAR(32),
	priority VARCHAR(16) DEFAULT 'medium' NOT NULL,
	priority_manual BOOLEAN DEFAULT '0' NOT NULL,
	needs_review BOOLEAN DEFAULT '0' NOT NULL,
	merged_into_id VARCHAR(36),
	archive_kind VARCHAR(32),
	archive_until DATETIME,
	archive_threshold INTEGER,
	archive_baseline INTEGER,
	archived_at DATETIME,
	PRIMARY KEY (id),
	UNIQUE (number),
	UNIQUE (grouping_key)
);

CREATE INDEX ix_issues_collector_config_id ON issues (collector_config_id);

CREATE INDEX ix_issues_exposure_visibility ON issues (exposure_visibility);

CREATE INDEX ix_issues_merged_into_id ON issues (merged_into_id);

CREATE INDEX ix_issues_resource_key ON issues (resource_key);

CREATE INDEX ix_issues_source ON issues (source);

CREATE INDEX ix_issues_status ON issues (status);

CREATE INDEX ix_issues_status_last_seen ON issues (status, last_seen);

CREATE INDEX ix_issues_status_merged ON issues (status, merged_into_id);

CREATE INDEX ix_issues_status_risk ON issues (status, risk_score);

CREATE INDEX ix_issues_sweep_lookup ON issues (collector_config_id, status, last_checked_at);

CREATE TABLE map_items (
	id VARCHAR(36) NOT NULL,
	map_job_id VARCHAR(36) NOT NULL,
	shard_key VARCHAR(256) NOT NULL,
	descriptor_json TEXT NOT NULL,
	weight FLOAT NOT NULL,
	status VARCHAR(32) NOT NULL,
	attempt INTEGER NOT NULL,
	dispatched_at DATETIME,
	finished_at DATETIME,
	summary_json TEXT,
	error_code VARCHAR(64),
	police_task_id VARCHAR(36),
	scan_job_id VARCHAR(36),
	PRIMARY KEY (id),
	CONSTRAINT uq_map_item_shard UNIQUE (map_job_id, shard_key)
);

CREATE INDEX ix_map_items_job_status ON map_items (map_job_id, status);

CREATE INDEX ix_map_items_map_job_id ON map_items (map_job_id);

CREATE INDEX ix_map_items_police_task ON map_items (police_task_id);

CREATE INDEX ix_map_items_status ON map_items (status);

CREATE TABLE map_jobs (
	id VARCHAR(36) NOT NULL,
	origin VARCHAR(32) NOT NULL,
	step_run_id VARCHAR(36),
	collector_config_id VARCHAR(36) NOT NULL,
	spec_json TEXT NOT NULL,
	status VARCHAR(32) NOT NULL,
	partition_done BOOLEAN NOT NULL,
	max_concurrency INTEGER NOT NULL,
	paused_until DATETIME,
	deadline DATETIME NOT NULL,
	result_json TEXT,
	created_at DATETIME NOT NULL,
	finished_at DATETIME,
	PRIMARY KEY (id)
);

CREATE INDEX ix_map_jobs_collector_config_id ON map_jobs (collector_config_id);

CREATE INDEX ix_map_jobs_status ON map_jobs (status);

CREATE INDEX ix_map_jobs_step_run_id ON map_jobs (step_run_id);

CREATE TABLE map_partitions (
	id VARCHAR(36) NOT NULL,
	map_job_id VARCHAR(36) NOT NULL,
	corpus_key VARCHAR(256) NOT NULL,
	cursor TEXT,
	status VARCHAR(32) NOT NULL,
	attempt INTEGER NOT NULL,
	police_task_id VARCHAR(36),
	PRIMARY KEY (id),
	CONSTRAINT uq_map_partition_corpus UNIQUE (map_job_id, corpus_key)
);

CREATE INDEX ix_map_partitions_job_status ON map_partitions (map_job_id, status);

CREATE INDEX ix_map_partitions_map_job_id ON map_partitions (map_job_id);

CREATE INDEX ix_map_partitions_status ON map_partitions (status);

CREATE TABLE member_identities (
	id VARCHAR(36) NOT NULL,
	member_id VARCHAR(36) NOT NULL,
	kind VARCHAR(32) NOT NULL,
	identity_key VARCHAR(64) NOT NULL,
	origin VARCHAR(32) NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (kind, identity_key)
);

CREATE INDEX ix_member_identities_member_id ON member_identities (member_id);

CREATE TABLE members (
	id VARCHAR(36) NOT NULL,
	handle VARCHAR(128) NOT NULL,
	display_name VARCHAR(256) NOT NULL,
	email VARCHAR(320),
	slack_user_id VARCHAR(128),
	active BOOLEAN NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	role VARCHAR(16),
	last_sign_in_at DATETIME,
	PRIMARY KEY (id),
	UNIQUE (handle)
);

CREATE INDEX ix_members_role_active ON members (role, active);

CREATE TABLE notification_settings (
	member_id VARCHAR(36) NOT NULL,
	category VARCHAR(32) NOT NULL,
	channel VARCHAR(16) NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (member_id, category)
);

CREATE TABLE notification_templates (
	id VARCHAR(36) NOT NULL,
	name VARCHAR(64) NOT NULL,
	version INTEGER NOT NULL,
	event_kind VARCHAR(32) NOT NULL,
	description VARCHAR(200),
	subject TEXT NOT NULL,
	body TEXT NOT NULL,
	email_subject TEXT,
	email_body TEXT,
	slack_body TEXT,
	active BOOLEAN NOT NULL,
	created_by VARCHAR(80) NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (name, version)
);

CREATE INDEX ix_notification_templates_name_active ON notification_templates (name, active);

CREATE TABLE notifications (
	id VARCHAR(36) NOT NULL,
	channel VARCHAR(32) NOT NULL,
	recipient_ref VARCHAR(256) NOT NULL,
	subject TEXT NOT NULL,
	body TEXT NOT NULL,
	dlp_verdict VARCHAR(32) NOT NULL,
	dlp_hit_count INTEGER NOT NULL,
	status VARCHAR(32) NOT NULL,
	delivery_attempts INTEGER NOT NULL,
	run_id VARCHAR(36),
	issue_id VARCHAR(36),
	created_at DATETIME NOT NULL,
	sent_at DATETIME,
	event_kind VARCHAR(32),
	template_name VARCHAR(64),
	template_version INTEGER,
	set_name VARCHAR(64),
	target_index INTEGER,
	error_code VARCHAR(32),
	category VARCHAR(32),
	PRIMARY KEY (id)
);

CREATE INDEX ix_notifications_issue_id ON notifications (issue_id);

CREATE INDEX ix_notifications_run_id ON notifications (run_id);

CREATE INDEX ix_notifications_status ON notifications (status);

CREATE TABLE ownership_rules (
	id VARCHAR(36) NOT NULL,
	position INTEGER NOT NULL,
	source VARCHAR(32),
	pattern VARCHAR(512) NOT NULL,
	team_id VARCHAR(36) NOT NULL,
	enabled BOOLEAN NOT NULL,
	created_by VARCHAR(256) NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE TABLE placement_policies (
	id VARCHAR(36) NOT NULL,
	name VARCHAR(64) NOT NULL,
	version INTEGER NOT NULL,
	source_format VARCHAR(16) NOT NULL,
	source_text TEXT NOT NULL,
	compiled_json TEXT NOT NULL,
	effect VARCHAR(16) NOT NULL,
	enabled BOOLEAN NOT NULL,
	active BOOLEAN NOT NULL,
	owner_team VARCHAR(128),
	created_by VARCHAR(256) NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (name, version)
);

CREATE TABLE police_tasks (
	id VARCHAR(36) NOT NULL,
	task_type VARCHAR(128) NOT NULL,
	labels TEXT NOT NULL,
	input_json TEXT NOT NULL,
	status VARCHAR(32) NOT NULL,
	worker_id VARCHAR(36),
	attempt INTEGER NOT NULL,
	max_attempts INTEGER NOT NULL,
	lease_expires_at DATETIME,
	deadline DATETIME NOT NULL,
	callback_token_hash VARCHAR(64),
	callback_token_used_at DATETIME,
	result_json TEXT,
	outcome VARCHAR(32),
	step_run_id VARCHAR(36),
	collector_config_id VARCHAR(36),
	created_at DATETIME NOT NULL,
	completed_at DATETIME,
	PRIMARY KEY (id)
);

CREATE INDEX ix_police_tasks_collector_config_id ON police_tasks (collector_config_id);

CREATE INDEX ix_police_tasks_status ON police_tasks (status);

CREATE INDEX ix_police_tasks_step_run_id ON police_tasks (step_run_id);

CREATE INDEX ix_police_tasks_worker_id ON police_tasks (worker_id);

CREATE TABLE police_workers (
	id VARCHAR(36) NOT NULL,
	name VARCHAR(128) NOT NULL,
	version VARCHAR(64) NOT NULL,
	mode VARCHAR(32) NOT NULL,
	contract_version VARCHAR(32) NOT NULL,
	task_types TEXT NOT NULL,
	labels TEXT NOT NULL,
	concurrency INTEGER NOT NULL,
	endpoint_url VARCHAR(2048),
	token_hash VARCHAR(64),
	status VARCHAR(32) NOT NULL,
	last_heartbeat_at DATETIME,
	last_delivery_at DATETIME,
	created_at DATETIME NOT NULL,
	health_alerted_at DATETIME,
	PRIMARY KEY (id)
);

CREATE INDEX ix_police_workers_status ON police_workers (status);

CREATE TABLE replay_nonces (
	scope VARCHAR(128) NOT NULL,
	nonce VARCHAR(256) NOT NULL,
	expires_at DATETIME NOT NULL,
	PRIMARY KEY (scope, nonce)
);

CREATE INDEX ix_replay_nonces_expires_at ON replay_nonces (expires_at);

CREATE TABLE resource_coverage (
	id VARCHAR(36) NOT NULL,
	collector_config_id VARCHAR(36) NOT NULL,
	resource_key VARCHAR(64) NOT NULL,
	source VARCHAR(32) NOT NULL,
	state VARCHAR(32) NOT NULL,
	reason VARCHAR(32),
	truncated_bytes INTEGER DEFAULT '0' NOT NULL,
	exposure_visibility VARCHAR(32) DEFAULT 'unknown' NOT NULL,
	first_seen_at DATETIME NOT NULL,
	last_attempt_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (collector_config_id, resource_key)
);

CREATE TABLE retention_notification_receipts (
	id VARCHAR(36) NOT NULL,
	payload_sha256 VARCHAR(64) NOT NULL,
	applied_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE TABLE saved_view_defaults (
	member_id VARCHAR(36) NOT NULL,
	view_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (member_id)
);

CREATE TABLE saved_view_stars (
	view_id VARCHAR(36) NOT NULL,
	member_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (view_id, member_id)
);

CREATE TABLE saved_views (
	id VARCHAR(36) NOT NULL,
	name VARCHAR(64) NOT NULL,
	"query" TEXT NOT NULL,
	sort VARCHAR(16) NOT NULL,
	period VARCHAR(64),
	visibility VARCHAR(16) NOT NULL,
	owner_member_id VARCHAR(36) NOT NULL,
	position INTEGER NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_saved_views_owner_member_id ON saved_views (owner_member_id);

CREATE TABLE scan_coverage (
	scan_job_id VARCHAR(36) NOT NULL,
	state VARCHAR(32) NOT NULL,
	reason VARCHAR(32) DEFAULT '' NOT NULL,
	count INTEGER DEFAULT '0' NOT NULL,
	PRIMARY KEY (scan_job_id, state, reason)
);

CREATE TABLE scan_jobs (
	id VARCHAR(36) NOT NULL,
	collector_config_id VARCHAR(36),
	source VARCHAR(32) NOT NULL,
	"trigger" VARCHAR(32) NOT NULL,
	status VARCHAR(32) NOT NULL,
	started_at DATETIME NOT NULL,
	finished_at DATETIME,
	resources_scanned INTEGER NOT NULL,
	resources_skipped INTEGER NOT NULL,
	findings_count INTEGER NOT NULL,
	error_code VARCHAR(64),
	PRIMARY KEY (id)
);

CREATE INDEX ix_scan_jobs_collector_config_id ON scan_jobs (collector_config_id);

CREATE INDEX ix_scan_jobs_status ON scan_jobs (status);

CREATE TABLE step_runs (
	id VARCHAR(36) NOT NULL,
	run_id VARCHAR(36) NOT NULL,
	step_id VARCHAR(128) NOT NULL,
	visit INTEGER NOT NULL,
	branch_token VARCHAR(128),
	status VARCHAR(32) NOT NULL,
	attempt INTEGER NOT NULL,
	input_json TEXT NOT NULL,
	output_json TEXT,
	outcome VARCHAR(32),
	error_code VARCHAR(64),
	started_at DATETIME,
	finished_at DATETIME,
	PRIMARY KEY (id),
	CONSTRAINT uq_step_run_visit UNIQUE (run_id, step_id, visit, branch_token)
);

CREATE INDEX ix_step_runs_run_id ON step_runs (run_id);

CREATE INDEX ix_step_runs_status ON step_runs (status);

CREATE TABLE team_memberships (
	team_id VARCHAR(36) NOT NULL,
	member_id VARCHAR(36) NOT NULL,
	origin VARCHAR(16) DEFAULT 'manual' NOT NULL,
	PRIMARY KEY (team_id, member_id)
);

CREATE TABLE teams (
	id VARCHAR(36) NOT NULL,
	slug VARCHAR(128) NOT NULL,
	name VARCHAR(256) NOT NULL,
	email VARCHAR(320),
	slack_channel VARCHAR(128),
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	lead_member_id VARCHAR(36),
	PRIMARY KEY (id),
	UNIQUE (slug)
);

CREATE TABLE transitions (
	id VARCHAR(36) NOT NULL,
	run_id VARCHAR(36) NOT NULL,
	from_step_run_id VARCHAR(36),
	to_step_id VARCHAR(128) NOT NULL,
	kind VARCHAR(32) NOT NULL,
	reason TEXT,
	at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_transitions_run_id ON transitions (run_id);

CREATE TABLE workflow_definitions (
	id VARCHAR(36) NOT NULL,
	name VARCHAR(128) NOT NULL,
	version INTEGER NOT NULL,
	source_format VARCHAR(32) NOT NULL,
	source_text TEXT NOT NULL,
	compiled_json TEXT NOT NULL,
	active BOOLEAN NOT NULL,
	created_by VARCHAR(128) NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_workflow_definition_version UNIQUE (name, version)
);

CREATE TABLE workflow_runs (
	id VARCHAR(36) NOT NULL,
	definition_id VARCHAR(36) NOT NULL,
	issue_id VARCHAR(36),
	parent_step_run_id VARCHAR(36),
	status VARCHAR(32) NOT NULL,
	"trigger" VARCHAR(32) NOT NULL,
	context_json TEXT NOT NULL,
	lock_version INTEGER NOT NULL,
	started_at DATETIME NOT NULL,
	finished_at DATETIME,
	PRIMARY KEY (id)
);

CREATE INDEX ix_workflow_runs_definition_id ON workflow_runs (definition_id);

CREATE INDEX ix_workflow_runs_issue_id ON workflow_runs (issue_id);

CREATE INDEX ix_workflow_runs_status ON workflow_runs (status);
