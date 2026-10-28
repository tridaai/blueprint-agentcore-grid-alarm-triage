-- 0004: Round 4 — ETR estimates (append-only, superseding by
-- link), incident closure acts (one per incident), and the ETR
-- outcome records the accuracy ledger is computed from.
-- Payloads stay JSON text, exactly as in the earlier migrations.
CREATE TABLE IF NOT EXISTS etr_estimates (
    estimate_id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    estimated_seq INTEGER NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_etr_incident ON etr_estimates (incident_id);

CREATE TABLE IF NOT EXISTS incident_closures (
    closure_id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL UNIQUE,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_closures_incident ON incident_closures (incident_id);

CREATE TABLE IF NOT EXISTS etr_outcomes (
    outcome_id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    closure_id TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_outcomes_incident ON etr_outcomes (incident_id);
