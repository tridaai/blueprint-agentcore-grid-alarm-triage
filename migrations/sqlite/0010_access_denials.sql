-- 0010: Round 11 — access control. One insert-only table: the
-- denial log. Every refusal a principal-carrying call earns is
-- recorded here before the error propagates — principal, role,
-- surface, the target the requester supplied, the reason, the
-- pack version. The log names only what the requester supplied:
-- a denial over a hidden incident records the requested id
-- string and nothing that says whether the incident exists.
-- Payloads stay JSON text, exactly as in the earlier migrations.
CREATE TABLE IF NOT EXISTS access_denials (
    denial_id TEXT PRIMARY KEY,
    act_seq INTEGER NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_access_denials_seq ON access_denials (act_seq);
