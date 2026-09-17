-- 0005 — What was retrieved for a turn.
--
-- docs/05 §5.1 specifies `working_set_log` and a `turn.working_set_id` pointing at it.
-- Migration 0003 created the conversation tables without them, because nothing assembled a
-- working set yet. Milestone 6 does (docs/26 §7).
--
-- This table answers "why did you bring that up?" (docs/16 §6): the queries that were run,
-- the policy they ran under, and every item with the arithmetic that ranked it. A ranking
-- nobody can explain is a ranking nobody can debug.
--
-- It is explanatory, never authoritative. Deleting it loses the ability to explain past
-- turns and nothing else, which is why docs/05 §7 prunes it after 90 days.

CREATE TABLE working_set_log (
    id            TEXT PRIMARY KEY,
    turn_id       TEXT,
    query         TEXT NOT NULL,          -- JSON array: the expanded queries (docs/06 §5.2)
    policy        TEXT NOT NULL,          -- JSON RetrievalPolicy as it was applied
    items         TEXT NOT NULL,          -- JSON [{memory_id, score, trust, source}]
    token_count   INTEGER NOT NULL,
    dropped_count INTEGER NOT NULL,       -- budget starvation, visible rather than silent
    created_at    TEXT NOT NULL
);

CREATE INDEX idx_working_set_turn ON working_set_log (turn_id);
CREATE INDEX idx_working_set_created ON working_set_log (created_at);

-- Neither side declares a foreign key. The log row is written before the turn row (so
-- `working_set_log.turn_id` would point at a row that does not exist yet), and the turn
-- must be recordable even if the log write failed — an explanation that could block a turn
-- from being recorded would have the dependency exactly backwards.
ALTER TABLE turn ADD COLUMN working_set_id TEXT;
