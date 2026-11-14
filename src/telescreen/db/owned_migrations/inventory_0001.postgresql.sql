-- Frozen owner baseline at shared revision 0009_inventory_counter.

-- Schema verified against db/schema/telescreen.nodetto.jsonl.

CREATE TABLE data_asset_events (
	id VARCHAR(36) NOT NULL,
	asset_id VARCHAR(36) NOT NULL,
	kind VARCHAR(32) NOT NULL,
	occurred_at TIMESTAMP WITH TIME ZONE,
	recorded_at TIMESTAMP WITH TIME ZONE NOT NULL,
	actor_ref VARCHAR(256) NOT NULL,
	scan_job_id VARCHAR(36),
	items_added INTEGER,
	items_removed INTEGER,
	item_count_after INTEGER,
	values_added INTEGER,
	values_removed INTEGER,
	value_count_after INTEGER,
	field_key VARCHAR(48),
	old_value TEXT,
	new_value TEXT,
	note TEXT,
	PRIMARY KEY (id)
);

CREATE INDEX ix_data_asset_events_asset_recorded ON data_asset_events (asset_id, recorded_at);

CREATE TABLE data_asset_items (
	id VARCHAR(36) NOT NULL,
	asset_id VARCHAR(36) NOT NULL,
	kind VARCHAR(8) NOT NULL,
	fingerprint VARCHAR(64) NOT NULL,
	key_version INTEGER DEFAULT '1' NOT NULL,
	first_seen_at TIMESTAMP WITH TIME ZONE NOT NULL,
	removed_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	CONSTRAINT uq_data_asset_items_key UNIQUE (asset_id, kind, fingerprint)
);

CREATE INDEX ix_data_asset_items_asset_state ON data_asset_items (asset_id, kind, removed_at);

CREATE TABLE data_assets (
	id VARCHAR(36) NOT NULL,
	number INTEGER NOT NULL,
	resource_key VARCHAR(64) NOT NULL,
	source VARCHAR(64) NOT NULL,
	origin VARCHAR(16) NOT NULL,
	kind VARCHAR(16) NOT NULL,
	state VARCHAR(16) NOT NULL,
	name VARCHAR(512) NOT NULL,
	display_locator TEXT NOT NULL,
	collector_config_id VARCHAR(36),
	sweep_covered BOOLEAN DEFAULT '0' NOT NULL,
	item_count INTEGER,
	first_seen_at TIMESTAMP WITH TIME ZONE NOT NULL,
	last_seen_at TIMESTAMP WITH TIME ZONE,
	deleted_at TIMESTAMP WITH TIME ZONE,
	overdue_notified_for VARCHAR(10),
	created_by VARCHAR(256),
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (number),
	UNIQUE (resource_key)
);

CREATE INDEX ix_data_assets_config_state ON data_assets (collector_config_id, state, last_seen_at);

CREATE INDEX ix_data_assets_source ON data_assets (source);

CREATE INDEX ix_data_assets_state_last_seen ON data_assets (state, last_seen_at);

CREATE TABLE data_field_values (
	asset_id VARCHAR(36) NOT NULL,
	field_id VARCHAR(36) NOT NULL,
	value_json TEXT NOT NULL,
	value_date VARCHAR(10),
	updated_by VARCHAR(256) NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	revision_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (asset_id, field_id)
);

CREATE INDEX ix_data_field_values_field_date ON data_field_values (field_id, value_date);

CREATE TABLE data_fields (
	id VARCHAR(36) NOT NULL,
	key VARCHAR(48) NOT NULL,
	label VARCHAR(80) NOT NULL,
	description VARCHAR(500),
	field_type VARCHAR(16) NOT NULL,
	options_json TEXT,
	required BOOLEAN DEFAULT '0' NOT NULL,
	position INTEGER NOT NULL,
	role VARCHAR(16),
	archived_at TIMESTAMP WITH TIME ZONE,
	created_by VARCHAR(256) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (key)
);

CREATE TABLE inventory_audit_outbox (
	id VARCHAR(36) NOT NULL,
	payload_json TEXT NOT NULL,
	recorded_at TIMESTAMP WITH TIME ZONE NOT NULL,
	delivered_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id)
);

CREATE TABLE inventory_counters (
	name VARCHAR(64) NOT NULL,
	value INTEGER NOT NULL,
	PRIMARY KEY (name)
);

CREATE TABLE inventory_inbox (
	id VARCHAR(36) NOT NULL,
	stream_id VARCHAR(256) NOT NULL,
	sequence INTEGER NOT NULL,
	payload_sha256 VARCHAR(64) NOT NULL,
	applied_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (stream_id, sequence),
	CONSTRAINT uq_inventory_inbox_id UNIQUE (id)
);

CREATE TABLE inventory_inbox_streams (
	id VARCHAR(256) NOT NULL,
	last_sequence INTEGER NOT NULL,
	PRIMARY KEY (id)
);

CREATE TABLE inventory_retention_outbox (
	id VARCHAR(36) NOT NULL,
	payload_json TEXT NOT NULL,
	recorded_at TIMESTAMP WITH TIME ZONE NOT NULL,
	delivered_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id)
);
