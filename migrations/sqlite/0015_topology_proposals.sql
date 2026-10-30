-- 0015: Round 16 — topology dual control. Two insert-only
-- tables: the topology proposals and the decisions on them.
-- A proposal states the whole declaration it asks for and
-- changes nothing on its own. A decision is a second named
-- operator's act — confirmed or rejected — and a proposal's
-- standing is the fold of its decisions, never a stored
-- field. Only a confirmation strikes a declaration, in the
-- Round 6 table, carrying the proposal's provenance.
-- Payloads stay JSON text, exactly as in the earlier
-- migrations.
CREATE TABLE IF NOT EXISTS topology_proposals (
    proposal_id TEXT PRIMARY KEY,
    version TEXT NOT NULL,
    act_seq INTEGER NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_topology_proposals_seq ON topology_proposals (act_seq);
CREATE INDEX IF NOT EXISTS idx_topology_proposals_version ON topology_proposals (version);
CREATE TABLE IF NOT EXISTS topology_decisions (
    decision_id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL,
    act_seq INTEGER NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_topology_decisions_seq ON topology_decisions (act_seq);
CREATE INDEX IF NOT EXISTS idx_topology_decisions_proposal ON topology_decisions (proposal_id);
