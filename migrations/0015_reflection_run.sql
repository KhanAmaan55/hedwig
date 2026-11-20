-- 0015 — Nightly reflection's records (docs/12 §15). Owner: `reflection`. New tables only.
--
-- `reflection_run`: one row per run, so a run interrupted at a stage boundary resumes from
-- `next_stage` the next night, and every night's work is auditable.
-- `belief_judgement`: the model's verdict on a pair of beliefs, so no pair is judged twice. A
-- verdict is a *proposal* until the user resolves it (or auto-apply is configured): measured on a
-- 2B model, its "contradicts" was wrong often enough to rewrite true memories (docs/12 §15).

CREATE TABLE reflection_run (
    id           TEXT PRIMARY KEY,
    tier         TEXT NOT NULL DEFAULT 'T2',
    status       TEXT NOT NULL CHECK (status IN ('running','completed','yielded','failed')),
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    next_stage   TEXT,
    stats        TEXT NOT NULL DEFAULT '{}',
    content_hash TEXT,
    error        TEXT
);

CREATE INDEX idx_reflection_run_time ON reflection_run (started_at DESC);

CREATE TABLE belief_judgement (
    pair        TEXT PRIMARY KEY,           -- "<older id>|<newer id>"
    verdict     TEXT NOT NULL CHECK (verdict IN ('same','contradicts','different')),
    status      TEXT NOT NULL DEFAULT 'proposed'
                CHECK (status IN ('proposed','applied','resolved')),
    resolution  TEXT,                       -- the user's choice, when they made one
    created_at  TEXT NOT NULL,
    resolved_at TEXT
);

CREATE INDEX idx_belief_judgement_open ON belief_judgement (status);
