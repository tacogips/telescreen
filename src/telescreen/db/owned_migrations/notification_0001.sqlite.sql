-- Frozen notification_0001 baseline. Do not edit.

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

CREATE TABLE notification_settings (
	member_id VARCHAR(36) NOT NULL, 
	category VARCHAR(32) NOT NULL, 
	channel VARCHAR(16) NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (member_id, category)
);

CREATE TABLE retention_notification_receipts (
	id VARCHAR(36) NOT NULL, 
	payload_sha256 VARCHAR(64) NOT NULL, 
	applied_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE notification_inbox (
	id VARCHAR(36) NOT NULL, 
	payload_sha256 VARCHAR(64) NOT NULL, 
	applied_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE notification_delivery_state (
	notification_id VARCHAR(36) NOT NULL, 
	attempt INTEGER NOT NULL, 
	max_attempts INTEGER NOT NULL, 
	available_at DATETIME NOT NULL, 
	leased_until DATETIME, 
	PRIMARY KEY (notification_id)
);

CREATE INDEX ix_notification_delivery_state_available_at ON notification_delivery_state (available_at);

CREATE TABLE notification_directory_entries (
	kind VARCHAR(32) NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	revision INTEGER NOT NULL, 
	present BOOLEAN NOT NULL, 
	lookup_key VARCHAR(128), 
	payload_json TEXT NOT NULL, 
	applied_at DATETIME NOT NULL, 
	PRIMARY KEY (kind, id)
);

CREATE INDEX ix_notification_directory_entries_lookup ON notification_directory_entries (kind, lookup_key);

CREATE TABLE notification_audit_records (
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

CREATE INDEX ix_notification_audit_records_at ON notification_audit_records (at);

CREATE TABLE notification_audit_chain_head (
	id INTEGER NOT NULL, 
	seq INTEGER NOT NULL, 
	hash VARCHAR(64) NOT NULL, 
	PRIMARY KEY (id)
);
