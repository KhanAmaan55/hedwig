-- 0006 — Emotional state (docs/05 §5.4, docs/09, ADR-0019).
--
-- Three tables with three different jobs:
--
--   * `emotion_state` is the current reading. Exactly one row, enforced by the primary key
--     check, because "the current mood" is singular and a table that could hold two of them
--     would eventually hold two of them.
--   * `emotion_history` is the timeline the Mind Inspector draws. Written only when the
--     state moved enough to matter, so idle hours do not fill it with identical rows.
--   * `appraisal` is why. Each row is one event read as seven numbers plus the deltas that
--     were actually applied — the difference between "HEDWIG got stressed" and "HEDWIG got
--     stressed because three tool calls failed in a row".
--
-- The six columns are the vector fixed by ADR-0019. `valence` and `arousal` are derived
-- from these in code and deliberately not stored: a stored summary can disagree with what
-- it summarises, and a derived one cannot.

CREATE TABLE emotion_state (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    happiness   REAL NOT NULL CHECK (happiness  BETWEEN 0 AND 1),
    trust       REAL NOT NULL CHECK (trust      BETWEEN 0 AND 1),
    curiosity   REAL NOT NULL CHECK (curiosity  BETWEEN 0 AND 1),
    confidence  REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    energy      REAL NOT NULL CHECK (energy     BETWEEN 0 AND 1),
    stress      REAL NOT NULL CHECK (stress     BETWEEN 0 AND 1),
    -- When the integrator last ran. Decay is computed from this rather than from wall-clock
    -- "now", so a process that was asleep for six hours resolves that gap exactly once.
    ticked_at   TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    version     INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE emotion_history (
    id             TEXT PRIMARY KEY,
    recorded_at    TEXT NOT NULL,
    dims           TEXT NOT NULL,     -- JSON snapshot of all six dimensions
    cause          TEXT NOT NULL CHECK (cause IN ('appraisal','decay','restore','manual')),
    appraisal_id   TEXT REFERENCES appraisal(id),
    correlation_id TEXT
);

CREATE INDEX idx_emotion_history_time ON emotion_history (recorded_at DESC);

CREATE TABLE appraisal (
    id              TEXT PRIMARY KEY,
    target_event_id TEXT,
    event_type      TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    dims            TEXT NOT NULL,   -- JSON: the seven appraisal dimensions
    deltas          TEXT NOT NULL,   -- JSON: dimension -> delta actually applied
    -- One line naming the rule that fired. Never user text: this table is read by the
    -- inspector and exported, and a conversation must not leak into it sideways.
    rationale       TEXT NOT NULL DEFAULT '',
    correlation_id  TEXT
);

CREATE INDEX idx_appraisal_time ON appraisal (created_at DESC);
CREATE INDEX idx_appraisal_event ON appraisal (target_event_id);
