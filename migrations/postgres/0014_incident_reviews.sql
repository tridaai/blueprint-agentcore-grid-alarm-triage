-- 0014: Round 15 — the post-incident review. One insert-only
-- table: the incident reviews. A review re-performs one
-- incident's whole timeline from its artefacts of record —
-- foundation and membership, cause and severity, the ETR
-- record, work orders, the field record including quarantine
-- holds, restoration and closure, lineage — and grades its
-- own completeness on its face: every expected element either
-- present with its artefact ids or absent with a stated
-- reason. Fingerprinted over its pack, incident and sections,
-- superseding by link when the record moves. Payloads stay
-- JSON text, exactly as in the earlier migrations.
CREATE TABLE IF NOT EXISTS incident_reviews (
    review_id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    act_seq INTEGER NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_incident_reviews_seq ON incident_reviews (act_seq);
CREATE INDEX IF NOT EXISTS idx_incident_reviews_incident ON incident_reviews (incident_id);
