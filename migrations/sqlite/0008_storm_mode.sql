-- 0008: Round 8 — storm mode (Law 8). Incidents gain the
-- operating-mode stamp they are correlated under (written once
-- at creation — standing a storm down never rewrites it), and two
-- insert-only tables land: the storm declaration acts (declare /
-- extend / stand_down — the fold of these acts is the storm
-- state) and the post-storm review artefacts. Payloads stay JSON
-- text, exactly as in the earlier migrations.
ALTER TABLE incidents ADD COLUMN operating_mode TEXT NOT NULL DEFAULT 'normal';
ALTER TABLE incidents ADD COLUMN storm_pack_version TEXT;
ALTER TABLE incidents ADD COLUMN storm_declaration_id TEXT;

CREATE TABLE IF NOT EXISTS storm_declarations (
    declaration_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    opens_declaration_id TEXT,
    act_seq INTEGER NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_storm_declarations_window ON storm_declarations (opens_declaration_id);
CREATE INDEX IF NOT EXISTS idx_storm_declarations_kind ON storm_declarations (kind);

CREATE TABLE IF NOT EXISTS storm_reviews (
    review_id TEXT PRIMARY KEY,
    declaration_id TEXT NOT NULL,
    act_seq INTEGER NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_storm_reviews_declaration ON storm_reviews (declaration_id);
