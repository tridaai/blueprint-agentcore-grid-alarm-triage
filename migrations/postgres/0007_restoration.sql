-- 0007: Round 7 — restoration signals (insert-only readings from
-- the meter/OMS-shaped and topology-shaped sources), restoration
-- confirmations (computed artefacts, superseding by link), the
-- gated close act (binds a confirmation, the R4 closure and its
-- ETR outcome, and the work-order state), work-order completion
-- acts, incident split records (a partial restoration divided by
-- a named act) and post-close contradiction records. Payloads are
-- JSON text, exactly as in the SQLite dialect — one contract
-- across stores.
CREATE TABLE IF NOT EXISTS restoration_signals (
    signal_id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    source TEXT NOT NULL,
    act_seq INTEGER NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_restoration_signals_incident ON restoration_signals (incident_id);

CREATE TABLE IF NOT EXISTS restoration_confirmations (
    confirmation_id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    act_seq INTEGER NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_restoration_confirmations_incident ON restoration_confirmations (incident_id);

CREATE TABLE IF NOT EXISTS incident_close_acts (
    act_id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL UNIQUE,
    act_seq INTEGER NOT NULL,
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS work_order_completions (
    completion_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL,
    incident_id TEXT NOT NULL,
    act_seq INTEGER NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wo_completions_order ON work_order_completions (order_id);

CREATE TABLE IF NOT EXISTS incident_splits (
    split_id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    restored_incident_id TEXT NOT NULL,
    continuing_incident_id TEXT NOT NULL,
    act_seq INTEGER NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_splits_incident ON incident_splits (incident_id);
CREATE INDEX IF NOT EXISTS idx_splits_restored ON incident_splits (restored_incident_id);
CREATE INDEX IF NOT EXISTS idx_splits_continuing ON incident_splits (continuing_incident_id);

CREATE TABLE IF NOT EXISTS post_close_contradictions (
    contradiction_id TEXT PRIMARY KEY,
    closed_incident_id TEXT NOT NULL,
    fresh_incident_id TEXT,
    act_seq INTEGER NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_contradictions_closed ON post_close_contradictions (closed_incident_id);
CREATE INDEX IF NOT EXISTS idx_contradictions_fresh ON post_close_contradictions (fresh_incident_id);
