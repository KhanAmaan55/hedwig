-- 0001 — Platform tables.
--
-- Infrastructure only: the event outbox, state documents, task runs and blob metadata.
-- No domain tables. Episodes, beliefs, entities and the rest arrive with the memory
-- subsystem (docs/05 §5.2).
--
-- Conventions (docs/05 §3): ULID text ids, ISO-8601 UTC text timestamps, `version` on
-- every mutable row, `principal_id` everywhere, no destructive deletes in domain tables.

-- ---------------------------------------------------------------------------
-- Event bus (docs/04 §3)
-- ---------------------------------------------------------------------------

CREATE TABLE event (
    id             TEXT PRIMARY KEY,
    type           TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    occurred_at    TEXT NOT NULL,
    source         TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    causation_id   TEXT,
    principal_id   TEXT NOT NULL DEFAULT 'local',
    payload        TEXT NOT NULL
) STRICT;

CREATE INDEX idx_event_time ON event (occurred_at DESC);
CREATE INDEX idx_event_corr ON event (correlation_id);
CREATE INDEX idx_event_type ON event (type, occurred_at DESC);

-- At-least-once delivery requires idempotency, and idempotency requires a record of
-- what each subscription has already seen.
CREATE TABLE processed_event (
    subscription TEXT NOT NULL,
    event_id     TEXT NOT NULL REFERENCES event (id),
    status       TEXT NOT NULL CHECK (status IN ('ok', 'failed', 'dead')),
    attempts     INTEGER NOT NULL DEFAULT 1,
    last_error   TEXT,
    processed_at TEXT NOT NULL,
    PRIMARY KEY (subscription, event_id)
) STRICT;

CREATE TABLE subscription_cursor (
    subscription  TEXT PRIMARY KEY,
    last_event_id TEXT,
    updated_at    TEXT NOT NULL
) STRICT;

-- Nothing is discarded silently. A dead letter stays visible until someone resolves it.
CREATE TABLE event_dead_letter (
    id           TEXT PRIMARY KEY,
    subscription TEXT NOT NULL,
    event_id     TEXT NOT NULL REFERENCES event (id),
    attempts     INTEGER NOT NULL,
    error        TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    resolved_at  TEXT
) STRICT;

CREATE INDEX idx_dead_letter_open ON event_dead_letter (created_at) WHERE resolved_at IS NULL;

-- ---------------------------------------------------------------------------
-- State manager (docs/08)
-- ---------------------------------------------------------------------------

CREATE TABLE state_document (
    namespace    TEXT NOT NULL,
    key          TEXT NOT NULL,
    value        TEXT NOT NULL,
    version      INTEGER NOT NULL DEFAULT 1,
    principal_id TEXT NOT NULL DEFAULT 'local',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (namespace, key)
) STRICT;

-- Every write, so "who changed this and why?" is always answerable (docs/08 §3).
CREATE TABLE state_history (
    id          TEXT PRIMARY KEY,
    namespace   TEXT NOT NULL,
    key         TEXT NOT NULL,
    version     INTEGER NOT NULL,
    value       TEXT NOT NULL,
    reason      TEXT NOT NULL,
    recorded_at TEXT NOT NULL
) STRICT;

CREATE INDEX idx_state_history_doc ON state_history (namespace, key, version DESC);

-- Point-in-time copies of whole namespaces. Generalises the nightly identity snapshot in
-- docs/08 §7.1: rolling back cognitive drift must not touch anything else.
CREATE TABLE state_snapshot (
    id         TEXT PRIMARY KEY,
    label      TEXT NOT NULL,
    namespaces TEXT NOT NULL,
    payload    TEXT NOT NULL,
    taken_at   TEXT NOT NULL
) STRICT;

CREATE INDEX idx_state_snapshot_time ON state_snapshot (taken_at DESC);

-- ---------------------------------------------------------------------------
-- Task scheduler (docs/17 §5-6)
-- ---------------------------------------------------------------------------

CREATE TABLE task_run (
    id             TEXT PRIMARY KEY,
    task_name      TEXT NOT NULL,
    trigger        TEXT NOT NULL CHECK (trigger IN ('schedule', 'idle', 'manual', 'event')),
    status         TEXT NOT NULL CHECK (
        status IN ('queued', 'running', 'completed', 'failed', 'cancelled', 'preempted')
    ),
    queued_at      TEXT NOT NULL,
    started_at     TEXT,
    finished_at    TEXT,
    cursor         TEXT,
    stats          TEXT,
    error          TEXT,
    correlation_id TEXT NOT NULL
) STRICT;

CREATE INDEX idx_task_run_name ON task_run (task_name, queued_at DESC);

-- One run per task at a time, enforced by the database rather than by hoping.
CREATE UNIQUE INDEX idx_task_run_active ON task_run (task_name)
    WHERE status IN ('queued', 'running');

CREATE TABLE task_state (
    task_name       TEXT PRIMARY KEY,
    last_success_at TEXT,
    last_run_id     TEXT REFERENCES task_run (id),
    paused          INTEGER NOT NULL DEFAULT 0,
    run_count       INTEGER NOT NULL DEFAULT 0,
    failure_count   INTEGER NOT NULL DEFAULT 0,
    updated_at      TEXT NOT NULL
) STRICT;

-- ---------------------------------------------------------------------------
-- File storage (docs/05 §2)
-- ---------------------------------------------------------------------------

CREATE TABLE blob (
    digest       TEXT PRIMARY KEY,
    size_bytes   INTEGER NOT NULL,
    media_type   TEXT NOT NULL,
    ref_count    INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL,
    accessed_at  TEXT,
    principal_id TEXT NOT NULL DEFAULT 'local'
) STRICT;

CREATE INDEX idx_blob_created ON blob (created_at DESC);
