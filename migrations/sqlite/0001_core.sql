-- 0001: append-only alarms, incidents + membership, evidence chain,
-- incident briefs, notifications, and the versioned topology index.
CREATE TABLE IF NOT EXISTS alarms (
    alarm_id TEXT PRIMARY KEY,
    stream_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    feeder_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    severity TEXT NOT NULL,
    raised_at TEXT NOT NULL,
    message TEXT NOT NULL,
    payload TEXT NOT NULL,
    ingest_seq INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alarms_feeder ON alarms (feeder_id);
CREATE INDEX IF NOT EXISTS idx_alarms_raised ON alarms (raised_at);

CREATE TABLE IF NOT EXISTS incidents (
    incident_id TEXT PRIMARY KEY,
    feeder_id TEXT NOT NULL,
    substation_id TEXT NOT NULL,
    topology_version TEXT NOT NULL,
    correlation_pack_version TEXT NOT NULL,
    anchor_alarm_id TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS incident_alarms (
    incident_id TEXT NOT NULL,
    alarm_id TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    PRIMARY KEY (incident_id, alarm_id)
);
CREATE INDEX IF NOT EXISTS idx_membership_alarm ON incident_alarms (alarm_id);

CREATE TABLE IF NOT EXISTS evidence_items (
    item_id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    object_key TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    item_hash TEXT NOT NULL,
    recorded_on TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evidence_incident ON evidence_items (incident_id);

CREATE TABLE IF NOT EXISTS incident_briefs (
    brief_id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    model_profile TEXT NOT NULL,
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    notification_id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS topology_versions (
    version TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    loaded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS topology_substations (
    version TEXT NOT NULL,
    substation_id TEXT NOT NULL,
    name TEXT NOT NULL,
    PRIMARY KEY (version, substation_id)
);

CREATE TABLE IF NOT EXISTS topology_feeders (
    version TEXT NOT NULL,
    feeder_id TEXT NOT NULL,
    substation_id TEXT NOT NULL,
    name TEXT NOT NULL,
    PRIMARY KEY (version, feeder_id)
);

CREATE TABLE IF NOT EXISTS topology_devices (
    version TEXT NOT NULL,
    device_id TEXT NOT NULL,
    feeder_id TEXT NOT NULL,
    parent_device_id TEXT,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    PRIMARY KEY (version, device_id)
);

CREATE TABLE IF NOT EXISTS topology_critical_loads (
    version TEXT NOT NULL,
    load_id TEXT NOT NULL,
    feeder_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    PRIMARY KEY (version, load_id)
);
