-- 0012: Round 13 — the model gateway's cost ledger. One
-- insert-only table: every call the gateway routes, every
-- refusal it issues and every error the wrapped port raises
-- lands here as a record before control returns to the caller.
-- A record carries the measured input and output sizes, the
-- token estimates the model pack's stated rule derives from
-- them, the pack's rates and the exact integer cost. Totals
-- are never stored — they fold from these records, and the
-- fold is re-performed by an independent path. Payloads stay
-- JSON text, exactly as in the earlier migrations.
CREATE TABLE IF NOT EXISTS model_calls (
    call_id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    act_seq INTEGER NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_model_calls_seq ON model_calls (act_seq);
CREATE INDEX IF NOT EXISTS idx_model_calls_incident ON model_calls (incident_id);
