-- 0006: Round 6 — field updates (insert-only artefacts against an
-- incident), topology version declaration acts, and incident
-- revision records (re-correlation under a newer topology version
-- produces a new incident row linked by one of these records; the
-- prior incident row is never touched). Payloads stay JSON text,
-- exactly as in the earlier migrations.
CREATE TABLE IF NOT EXISTS field_updates (
    update_id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    act_seq INTEGER NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_field_updates_incident ON field_updates (incident_id);

CREATE TABLE IF NOT EXISTS topology_declarations (
    declaration_id TEXT PRIMARY KEY,
    version TEXT NOT NULL,
    act_seq INTEGER NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_topo_decl_version ON topology_declarations (version);

CREATE TABLE IF NOT EXISTS incident_revisions (
    revision_id TEXT PRIMARY KEY,
    prior_incident_id TEXT NOT NULL,
    revised_incident_id TEXT NOT NULL,
    act_seq INTEGER NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_revisions_prior ON incident_revisions (prior_incident_id);
CREATE INDEX IF NOT EXISTS idx_revisions_revised ON incident_revisions (revised_incident_id);
