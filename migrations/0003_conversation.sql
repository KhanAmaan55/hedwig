-- 0003 — Conversation: sessions, messages, turns.
--
-- docs/05 §5.1. Migration 0001 was platform infrastructure only, so these were still
-- missing; memory needs them for two reasons:
--
--   * **Short-term memory is a query over `message`** (docs/06 §2, §5.1). There is no
--     short-term store to build — "the last N turns" is `ORDER BY seq DESC LIMIT N`, and
--     this is the table it reads.
--   * Episodes reference the session they happened in, which is what lets a session
--     summary be linked to the conversation it summarises.
--
-- The raw message log is kept indefinitely (docs/05 §7): it is the fallback from which any
-- lost extraction can be re-derived, which is why capture is allowed to be lossy.

CREATE TABLE session (
    id           TEXT PRIMARY KEY,
    principal_id TEXT NOT NULL DEFAULT 'local',
    channel      TEXT NOT NULL DEFAULT 'api'
                 CHECK (channel IN ('cli','web','api','internal')),
    started_at   TEXT NOT NULL,
    ended_at     TEXT,
    end_reason   TEXT CHECK (end_reason IN ('explicit','timeout','shutdown') OR end_reason IS NULL),
    turn_count   INTEGER NOT NULL DEFAULT 0,
    summary_id   TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    version      INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX idx_session_started ON session (started_at DESC);
CREATE INDEX idx_session_open ON session (started_at DESC) WHERE ended_at IS NULL;

CREATE TABLE message (
    id           TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL REFERENCES session(id),
    seq          INTEGER NOT NULL,
    role         TEXT NOT NULL CHECK (role IN ('user','hedwig','system','tool')),
    text         TEXT NOT NULL,
    trust_tier   TEXT NOT NULL DEFAULT 'user',
    token_count  INTEGER,
    meta         TEXT,
    created_at   TEXT NOT NULL,
    -- The window reads by descending seq, so ordering must be unambiguous per session.
    UNIQUE (session_id, seq)
);

CREATE INDEX idx_message_session ON message (session_id, seq DESC);

CREATE TABLE turn (
    id               TEXT PRIMARY KEY,
    session_id       TEXT NOT NULL REFERENCES session(id),
    user_message_id  TEXT REFERENCES message(id),
    reply_message_id TEXT REFERENCES message(id),
    correlation_id   TEXT NOT NULL,
    started_at       TEXT NOT NULL,
    completed_at     TEXT,
    status           TEXT NOT NULL DEFAULT 'running' CHECK (status IN
                       ('running','completed','refused','failed','truncated','interrupted')),
    latency_ms       INTEGER,
    tokens_in        INTEGER,
    tokens_out       INTEGER,
    created_at       TEXT NOT NULL
);

CREATE INDEX idx_turn_session ON turn (session_id, started_at DESC);
CREATE INDEX idx_turn_corr ON turn (correlation_id);
