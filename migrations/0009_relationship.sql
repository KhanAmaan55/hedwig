-- 0009 — Relationship memory (docs/05 §5.2, docs/06 §3.3, docs/27 §5). Owner: `memory`.
--
-- `relation` is docs/05 verbatim: one row per entity HEDWIG has a relationship with. Its
-- `trust` is per-entity and evidence-accumulated — not the emotional `trust` dimension in
-- `emotion_state`, which is a slow global mood. ADR-0020 keeps the two apart; so do the
-- tables.
--
-- `relationship_tally` exists because the update rule is applied at session end, never per
-- turn ("relationships do not lurch"), but its inputs arrive per turn. The tally accumulates
-- them durably — a restart mid-session must not forget half a conversation — and is cleared
-- once the session's update has been applied.

CREATE TABLE relation (
    entity_id           TEXT PRIMARY KEY REFERENCES entity(id),
    familiarity         REAL NOT NULL DEFAULT 0   CHECK (familiarity BETWEEN 0 AND 1),
    affinity            REAL NOT NULL DEFAULT 0   CHECK (affinity BETWEEN -1 AND 1),
    trust               REAL NOT NULL DEFAULT 0.5 CHECK (trust BETWEEN 0 AND 1),
    interaction_count   INTEGER NOT NULL DEFAULT 0,
    last_interaction_at TEXT,
    notes               TEXT NOT NULL DEFAULT '',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    version             INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE relationship_tally (
    session_id    TEXT PRIMARY KEY REFERENCES session(id),
    entity_id     TEXT NOT NULL REFERENCES entity(id),
    turns         INTEGER NOT NULL DEFAULT 0,
    valence_sum   REAL NOT NULL DEFAULT 0,
    valence_count INTEGER NOT NULL DEFAULT 0,
    updated_at    TEXT NOT NULL
);
