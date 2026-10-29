-- 0013: Round 14 — the injection quarantine. Two insert-only
-- tables. quarantine_holds carries one row for every piece of
-- outside text the screen stopped: the raw text lives here and
-- nowhere else, named by its channel and the artefact it arrived
-- on, with the pack rules it tripped. quarantine_releases
-- carries the named reviewer acts that release a hold. Whether
-- a hold stands released is a fold of the release rows, never a
-- stored flag, and neither table exposes an update or a delete.
-- Payloads stay JSON text, exactly as in the earlier migrations.
CREATE TABLE IF NOT EXISTS quarantine_holds (
    hold_id TEXT PRIMARY KEY,
    incident_id TEXT,
    act_seq INTEGER NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quarantine_holds_seq ON quarantine_holds (act_seq);
CREATE INDEX IF NOT EXISTS idx_quarantine_holds_incident ON quarantine_holds (incident_id);
CREATE TABLE IF NOT EXISTS quarantine_releases (
    release_id TEXT PRIMARY KEY,
    hold_id TEXT NOT NULL,
    act_seq INTEGER NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quarantine_releases_seq ON quarantine_releases (act_seq);
CREATE INDEX IF NOT EXISTS idx_quarantine_releases_hold ON quarantine_releases (hold_id);
