-- 0011 — Goals (docs/05 §5.4, docs/08 §6). Owner: `goals`.
--
-- docs/05's table, plus `context` and `completion`: why the goal exists and how HEDWIG will
-- know it is done, which the vision asks of every goal and the table did not hold. New table;
-- nothing existing changes.

CREATE TABLE goal (
    id               TEXT PRIMARY KEY,
    principal_id     TEXT NOT NULL DEFAULT 'local',
    kind             TEXT NOT NULL CHECK (kind IN ('user_stated','self_generated','maintenance')),
    description      TEXT NOT NULL,
    context          TEXT NOT NULL DEFAULT '',
    completion       TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL CHECK (status IN
                       ('proposed','active','blocked','done','abandoned')),
    priority         REAL NOT NULL DEFAULT 0.5 CHECK (priority BETWEEN 0 AND 1),
    progress         REAL NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 1),
    parent_id        TEXT REFERENCES goal(id),
    origin_memory_id TEXT,
    origin           TEXT NOT NULL DEFAULT 'user' CHECK (origin IN ('user','reflection','system')),
    due_at           TEXT,
    last_touched_at  TEXT NOT NULL,
    last_progress_at TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    version          INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX idx_goal_active ON goal (status, priority DESC);
