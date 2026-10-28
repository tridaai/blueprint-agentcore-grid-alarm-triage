-- 0009: Round 9 — shift handovers. Two insert-only tables: the
-- handover artefacts (assembled only from artefacts of record,
-- content-addressed, superseding by link when the record moves)
-- and the acceptance acts, each binding the record hash of the
-- handover it accepts. Payloads stay JSON text, exactly as in
-- the earlier migrations.
CREATE TABLE IF NOT EXISTS shift_handovers (
    handover_id TEXT PRIMARY KEY,
    handed_over_by TEXT NOT NULL,
    act_seq INTEGER NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_shift_handovers_seq ON shift_handovers (act_seq);

CREATE TABLE IF NOT EXISTS handover_acceptances (
    acceptance_id TEXT PRIMARY KEY,
    handover_id TEXT NOT NULL,
    act_seq INTEGER NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_handover_acceptances_handover ON handover_acceptances (handover_id);
