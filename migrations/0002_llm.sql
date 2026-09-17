-- 0002 — Language model call records.
--
-- One row per model call, successful or not. This is what makes "what spent the token
-- budget last night?" and "which model produced this?" answerable after the fact
-- (docs/05 §5.6, docs/19 §6).
--
-- Prompts are NOT stored here. Logs are content-redacted by default (docs/19 §4), and a
-- table of every prompt would be the least protected copy of the most personal data on the
-- machine. Full prompt capture is an explicit, session-scoped tracing opt-in.

CREATE TABLE llm_call (
    id             TEXT PRIMARY KEY,
    principal_id   TEXT NOT NULL DEFAULT 'local',
    purpose        TEXT NOT NULL,
    tier           TEXT NOT NULL CHECK (tier IN ('conversational', 'utility', 'embedding')),
    model          TEXT NOT NULL,
    status         TEXT NOT NULL CHECK (status IN ('ok', 'failed')),
    tokens_in      INTEGER,
    tokens_out     INTEGER,
    duration_ms    REAL,
    first_token_ms REAL,
    attempts       INTEGER NOT NULL DEFAULT 1,
    escalated      INTEGER NOT NULL DEFAULT 0,
    finish_reason  TEXT,
    error          TEXT,
    correlation_id TEXT,
    created_at     TEXT NOT NULL
);

CREATE INDEX idx_llm_call_time ON llm_call (created_at DESC);
CREATE INDEX idx_llm_call_purpose ON llm_call (purpose, created_at DESC);
CREATE INDEX idx_llm_call_corr ON llm_call (correlation_id);
