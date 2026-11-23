-- 0018 — Evidence for personality drift (docs/10 §13, PER-02). Owner: `personality`. New table.
--
-- One row per observation that the user reacted to how HEDWIG talks: which trait, which way,
-- the exact words and the message they came from (§5.1: traceable, or it is not evidence).
-- `used_by` names the drift proposal that spent it, so no observation moves a trait twice.
-- It quotes the user: purge sweeps it by message id and phrase (ADR-0024, `RECORD`).

CREATE TABLE personality_evidence (
    id          TEXT PRIMARY KEY,
    trait       TEXT NOT NULL,
    direction   INTEGER NOT NULL CHECK (direction IN (-1, 1)),
    weight      REAL NOT NULL CHECK (weight > 0 AND weight <= 1),
    kind        TEXT NOT NULL DEFAULT 'feedback',
    source_id   TEXT NOT NULL,              -- the user's message
    quote       TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    used_by     TEXT,                       -- drift_proposal.id
    UNIQUE (source_id, trait)
);

CREATE INDEX idx_personality_evidence_open ON personality_evidence (trait, used_by, observed_at);
