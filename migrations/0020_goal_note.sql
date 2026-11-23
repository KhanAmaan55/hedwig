-- 0020 — A journal per goal: project memory (ADR-0025, docs/08 §13; MEM-06). Owner: `goals`.
-- New table only.
--
-- What was decided, what moved, what is still open, what got in the way — so "how did that
-- project go?" has an answer. Written by the goals store (its own changes), session reflection
-- (what a conversation decided), and the user. It quotes conversations: purge sweeps it by goal,
-- session and phrase (ADR-0024, `RECORD`).

CREATE TABLE goal_note (
    id          TEXT PRIMARY KEY,
    goal_id     TEXT NOT NULL REFERENCES goal(id),
    kind        TEXT NOT NULL CHECK (kind IN ('decision','progress','question','blocker','done')),
    text        TEXT NOT NULL,
    source      TEXT NOT NULL CHECK (source IN ('system','reflection','user')),
    session_id  TEXT,
    created_at  TEXT NOT NULL
);

CREATE INDEX idx_goal_note_goal ON goal_note (goal_id, created_at DESC);
