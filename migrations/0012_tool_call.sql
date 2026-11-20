-- 0012 — Tool calls (docs/05 §5.5, docs/13 §12). Owner: `tools`.
--
-- docs/05's audit table. Every call is written, including failures and refusals, because a
-- tool call nobody can see afterwards is a tool call nobody can trust. `turn_id` is not a
-- foreign key: a tool can be called outside a turn (curiosity), and an audit row must never
-- fail to be written because of one. New table; nothing existing changes.

CREATE TABLE tool_call (
    id             TEXT PRIMARY KEY,
    turn_id        TEXT,
    tool_name      TEXT NOT NULL,
    args           TEXT NOT NULL,
    result_digest  TEXT,
    status         TEXT NOT NULL CHECK (status IN
                     ('requested','approved','denied','running','ok','failed','timeout','invalid')),
    error          TEXT,
    duration_ms    INTEGER,
    correlation_id TEXT NOT NULL,
    created_at     TEXT NOT NULL
);

CREATE INDEX idx_tool_call_corr ON tool_call (correlation_id);
CREATE INDEX idx_tool_call_time ON tool_call (created_at DESC);
