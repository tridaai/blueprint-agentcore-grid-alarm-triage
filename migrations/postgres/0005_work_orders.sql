-- 0005: Round 5 — work-order drafts (content-addressed versions,
-- superseding by link), crew assignment acts and operator
-- decision acts. The acts live in their own tables so a draft row
-- is never updated: approval and assignment are records about the
-- draft, not edits to it. Payloads are JSON text, exactly as in
-- the SQLite dialect — one contract across stores.
CREATE TABLE IF NOT EXISTS work_orders (
    order_id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    drafted_seq INTEGER NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_work_orders_incident ON work_orders (incident_id);

CREATE TABLE IF NOT EXISTS work_order_assignments (
    assignment_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL,
    crew_id TEXT NOT NULL,
    act_seq INTEGER NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wo_assign_order ON work_order_assignments (order_id);
CREATE INDEX IF NOT EXISTS idx_wo_assign_crew ON work_order_assignments (crew_id);

CREATE TABLE IF NOT EXISTS work_order_decisions (
    decision_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL,
    act_seq INTEGER NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wo_decisions_order ON work_order_decisions (order_id);
