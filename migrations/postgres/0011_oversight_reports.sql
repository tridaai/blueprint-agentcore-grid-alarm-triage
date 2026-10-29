-- 0011: Round 12 — operations oversight reporting. One
-- insert-only table: the oversight reports. A report freezes a
-- stated window's aggregate figures over the artefacts of
-- record — volumes, severity mix, ETR accuracy by operating
-- mode, restoration outcomes, storm activity, handover
-- position — fingerprinted over its pack, window and sections,
-- superseding by link when the record moves. No report names
-- an incident. Payloads stay JSON text, exactly as in the
-- earlier migrations.
CREATE TABLE IF NOT EXISTS oversight_reports (
    report_id TEXT PRIMARY KEY,
    act_seq INTEGER NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_oversight_reports_seq ON oversight_reports (act_seq);
