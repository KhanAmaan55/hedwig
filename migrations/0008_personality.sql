-- 0008 — Personality (docs/05 §5.4, docs/10, docs/27). Owner: `personality`.
--
-- Traits as rows, not a JSON blob: each trait carries its own anchor, bounds and lifetime
-- drift, and the guardrails in docs/10 §5.3 are per-trait. A blob would make every guardrail
-- a parse-modify-write, and "how has warmth moved this year?" a full-table scan.
--
-- Nothing here is ever deleted. A revert is a new history row plus a `reverted_at` stamp on
-- the old one — "you've gotten weird, go back" must leave a trail of having happened.

CREATE TABLE personality_trait (
    name           TEXT PRIMARY KEY,
    value          REAL NOT NULL CHECK (value BETWEEN 0 AND 1),
    anchor         REAL NOT NULL CHECK (anchor BETWEEN 0 AND 1),
    floor          REAL NOT NULL CHECK (floor BETWEEN 0 AND 1),
    ceiling        REAL NOT NULL CHECK (ceiling BETWEEN 0 AND 1),
    -- Cumulative |change|, not distance from the anchor: oscillation burns budget even when
    -- it nets to nothing (docs/27 §4.2).
    lifetime_drift REAL NOT NULL DEFAULT 0 CHECK (lifetime_drift >= 0),
    updated_at     TEXT NOT NULL,
    version        INTEGER NOT NULL DEFAULT 1,
    CHECK (floor <= anchor AND anchor <= ceiling),
    CHECK (floor <= value AND value <= ceiling)
);

-- Every proposal and every decision, including the rule that refused it. Silent rejection
-- would make drift feel arbitrary (docs/10 §9).
CREATE TABLE drift_proposal (
    id                TEXT PRIMARY KEY,
    trait             TEXT NOT NULL,
    proposed_delta    REAL NOT NULL,
    rationale         TEXT NOT NULL DEFAULT '',
    evidence          TEXT NOT NULL DEFAULT '[]',   -- JSON [{memory_id, weight, observed_at}]
    evidence_count    INTEGER NOT NULL DEFAULT 0,
    source            TEXT NOT NULL CHECK (source IN ('inferred','instruction')),
    status            TEXT NOT NULL CHECK (status IN
                        ('pending','applied','partially_applied','rejected','expired')),
    rejection_rule    TEXT,
    -- No REFERENCES: reflection_run does not exist yet, and a proposal must be recordable
    -- before the module that will make most of them is built.
    reflection_run_id TEXT,
    created_at        TEXT NOT NULL,
    decided_at        TEXT
);

CREATE INDEX idx_drift_proposal_trait ON drift_proposal (trait, created_at DESC);

CREATE TABLE personality_history (
    id           TEXT PRIMARY KEY,
    trait        TEXT NOT NULL REFERENCES personality_trait(name),
    old_value    REAL NOT NULL,
    new_value    REAL NOT NULL,
    delta        REAL NOT NULL,
    source       TEXT NOT NULL CHECK (source IN
                   ('inferred','instruction','revert','reset')),
    proposal_id  TEXT REFERENCES drift_proposal(id),
    applied_at   TEXT NOT NULL,
    reverted_at  TEXT
);

CREATE INDEX idx_personality_history_trait ON personality_history (trait, applied_at DESC);

-- Immutable by default. A row changes only by explicit user consent, and `prohibitions`
-- never changes at all (docs/10 §3.2).
CREATE TABLE identity_core (
    key         TEXT PRIMARY KEY CHECK (key IN
                  ('name','pronouns','self_description','values','prohibitions')),
    value       TEXT NOT NULL,
    revision    INTEGER NOT NULL DEFAULT 1,
    changed_by  TEXT NOT NULL CHECK (changed_by IN ('bootstrap','user_consent')),
    updated_at  TEXT NOT NULL
);
