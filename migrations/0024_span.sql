-- 0024 — Where the time went (docs/19 §5, §13; OBS-01). Owner: `core.tracing`.
--
-- One row per turn-graph stage and per bus handler delivery, keyed by the correlation id the
-- work ran under. Names, timings and counts only: never a message, a memory or a prompt
-- (docs/19 §4). Kept 30 days.

CREATE TABLE span (
    id              TEXT PRIMARY KEY,
    correlation_id  TEXT NOT NULL,
    name            TEXT NOT NULL,              -- "recall", "compose"; a subscription's name
    kind            TEXT NOT NULL CHECK (kind IN ('stage', 'handler')),
    started_at      TEXT NOT NULL,
    duration_ms     REAL NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('ok', 'failed')),
    detail          TEXT                        -- JSON: counts and types, never content
);

CREATE INDEX span_correlation ON span (correlation_id, started_at);
CREATE INDEX span_started ON span (started_at);
