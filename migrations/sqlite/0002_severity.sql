-- 0002: severity assessments + acknowledgements (Round 2), and the
-- topology index gains per-device customer counts and the device
-- each critical load is served from. Payloads stay JSON text.
CREATE TABLE IF NOT EXISTS severity_assessments (
    assessment_id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    assessed_seq INTEGER NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_severity_incident ON severity_assessments (incident_id);

CREATE TABLE IF NOT EXISTS severity_acknowledgements (
    ack_id TEXT PRIMARY KEY,
    assessment_id TEXT NOT NULL,
    incident_id TEXT NOT NULL,
    acknowledged_by TEXT NOT NULL,
    role TEXT NOT NULL,
    acknowledged_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ack_assessment ON severity_acknowledgements (assessment_id);

ALTER TABLE topology_devices ADD COLUMN customers INTEGER;
ALTER TABLE topology_critical_loads ADD COLUMN device_id TEXT;
