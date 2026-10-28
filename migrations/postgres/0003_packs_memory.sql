-- 0003: Round 3 — root-cause classifications, the prior-incident
-- memory records (insert-only), and sealed evidence packs.
-- Payloads are JSON text, exactly as in the SQLite dialect — one
-- contract across stores.
CREATE TABLE IF NOT EXISTS cause_classifications (
    classification_id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_classification_incident ON cause_classifications (incident_id);

CREATE TABLE IF NOT EXISTS memory_records (
    record_id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    feeder_id TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_feeder ON memory_records (feeder_id);

CREATE TABLE IF NOT EXISTS evidence_packs (
    pack_id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    sealed_seq INTEGER NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_packs_incident ON evidence_packs (incident_id);
