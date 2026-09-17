-- 0004 — Memory: episodic, semantic, entities, lineage, access, tombstones.
--
-- Follows docs/05 §5.2 exactly. The conventions from docs/05 §3 hold throughout: ULID text
-- ids, ISO-8601 UTC text timestamps, `version` on mutable rows, `principal_id` everywhere,
-- and no destructive deletes — forgetting writes a tombstone (docs/06 §7.3).
--
-- Two column choices carry more weight than they look:
--
--   * `base_importance` is separate from `salience`. Salience decays and is reinforced;
--     base importance is the judgement made at capture and never changes. Without the
--     split, a genuinely important memory nobody has touched for a year is
--     indistinguishable from noise.
--   * `pinned` is a column, not a convention. A companion that forgets something the user
--     asked it to keep has committed the one unforgivable failure.
--
-- Social and procedural memory (docs/06 §3.3-3.4) are not here: they belong with the
-- personality and relationship work that gives them meaning. Vectors are not here either;
-- see docs/26 for why the semantic channel is deferred rather than faked.

-- ---------------------------------------------------------------------------
-- Episodic memory (docs/06 §3.1)
-- ---------------------------------------------------------------------------

CREATE TABLE episode (
    id               TEXT PRIMARY KEY,
    principal_id     TEXT NOT NULL DEFAULT 'local',
    kind             TEXT NOT NULL CHECK (kind IN
                       ('interaction','observation','reflection','session_summary',
                        'period_summary','self_action','milestone')),
    occurred_at      TEXT NOT NULL,
    ended_at         TEXT,
    title            TEXT NOT NULL,
    content          TEXT NOT NULL,
    session_id       TEXT REFERENCES session(id),
    salience         REAL NOT NULL DEFAULT 0.5 CHECK (salience BETWEEN 0 AND 1),
    base_importance  REAL NOT NULL DEFAULT 0.5 CHECK (base_importance BETWEEN 0 AND 1),
    emotional_charge REAL NOT NULL DEFAULT 0 CHECK (emotional_charge BETWEEN -1 AND 1),
    confidence       REAL NOT NULL DEFAULT 1.0 CHECK (confidence BETWEEN 0 AND 1),
    access_count     INTEGER NOT NULL DEFAULT 0,
    last_accessed_at TEXT,
    last_decay_at    TEXT,
    pinned           INTEGER NOT NULL DEFAULT 0,
    trust_tier       TEXT NOT NULL DEFAULT 'self',
    source_ref       TEXT,
    model_id         TEXT,
    tombstoned_at    TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    version          INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX idx_episode_time ON episode (occurred_at DESC) WHERE tombstoned_at IS NULL;
CREATE INDEX idx_episode_salience ON episode (salience DESC) WHERE tombstoned_at IS NULL;
CREATE INDEX idx_episode_session ON episode (session_id);
CREATE INDEX idx_episode_kind ON episode (kind, occurred_at DESC);

-- ---------------------------------------------------------------------------
-- Semantic memory (docs/06 §3.2)
-- ---------------------------------------------------------------------------

CREATE TABLE belief (
    id               TEXT PRIMARY KEY,
    principal_id     TEXT NOT NULL DEFAULT 'local',
    statement        TEXT NOT NULL,
    subject_id       TEXT REFERENCES entity(id),
    predicate        TEXT,
    object_text      TEXT,
    confidence       REAL NOT NULL DEFAULT 0.5 CHECK (confidence BETWEEN 0 AND 1),
    status           TEXT NOT NULL DEFAULT 'tentative' CHECK (status IN
                       ('tentative','active','superseded','retracted')),
    valid_from       TEXT NOT NULL,
    valid_to         TEXT,
    superseded_by    TEXT REFERENCES belief(id),
    salience         REAL NOT NULL DEFAULT 0.5 CHECK (salience BETWEEN 0 AND 1),
    base_importance  REAL NOT NULL DEFAULT 0.5 CHECK (base_importance BETWEEN 0 AND 1),
    access_count     INTEGER NOT NULL DEFAULT 0,
    last_accessed_at TEXT,
    last_decay_at    TEXT,
    pinned           INTEGER NOT NULL DEFAULT 0,
    trust_tier       TEXT NOT NULL DEFAULT 'self',
    source_ref       TEXT,
    model_id         TEXT,
    tombstoned_at    TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    version          INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX idx_belief_subject ON belief (subject_id, status);
CREATE INDEX idx_belief_active ON belief (status, salience DESC) WHERE tombstoned_at IS NULL;

-- One active assertion per (subject, predicate, object). The mechanism that stops the
-- belief table filling with near-duplicates (docs/05 §5.2).
CREATE UNIQUE INDEX idx_belief_spo ON belief (subject_id, predicate, object_text)
    WHERE status = 'active' AND predicate IS NOT NULL AND tombstoned_at IS NULL;

-- ---------------------------------------------------------------------------
-- Entities and links
-- ---------------------------------------------------------------------------

CREATE TABLE entity (
    id            TEXT PRIMARY KEY,
    principal_id  TEXT NOT NULL DEFAULT 'local',
    kind          TEXT NOT NULL DEFAULT 'topic' CHECK (kind IN
                    ('person','place','topic','project','artifact','org','self')),
    name          TEXT NOT NULL,
    canonical_key TEXT NOT NULL,
    aliases       TEXT NOT NULL DEFAULT '[]',
    first_seen_at TEXT NOT NULL,
    mention_count INTEGER NOT NULL DEFAULT 0,
    -- Entity resolution is non-destructive: a wrong merge must be reversible.
    merged_into   TEXT REFERENCES entity(id),
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    version       INTEGER NOT NULL DEFAULT 1,
    UNIQUE (kind, canonical_key)
);

CREATE TABLE memory_entity_link (
    memory_id   TEXT NOT NULL,
    memory_kind TEXT NOT NULL CHECK (memory_kind IN ('episode','belief','procedure')),
    entity_id   TEXT NOT NULL REFERENCES entity(id),
    role        TEXT NOT NULL DEFAULT 'mentioned',
    weight      REAL NOT NULL DEFAULT 1.0,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (memory_id, entity_id, role)
);

CREATE INDEX idx_mel_entity ON memory_entity_link (entity_id, memory_kind);

-- ---------------------------------------------------------------------------
-- Lineage — which memories produced which (docs/05 §5.2)
-- ---------------------------------------------------------------------------

CREATE TABLE memory_derivation (
    derived_id   TEXT NOT NULL,
    derived_kind TEXT NOT NULL,
    source_id    TEXT NOT NULL,
    source_kind  TEXT NOT NULL,
    relation     TEXT NOT NULL CHECK (relation IN
                   ('summarised_from','extracted_from','merged_from','corroborated_by',
                    'contradicted_by','promoted_from')),
    created_at   TEXT NOT NULL,
    PRIMARY KEY (derived_id, source_id, relation)
);

CREATE INDEX idx_derivation_source ON memory_derivation (source_id);

-- ---------------------------------------------------------------------------
-- Access log — drives reinforcement (docs/06 §7.2)
-- ---------------------------------------------------------------------------

CREATE TABLE memory_access (
    id              TEXT PRIMARY KEY,
    memory_id       TEXT NOT NULL,
    memory_kind     TEXT NOT NULL,
    accessed_at     TEXT NOT NULL,
    retrieval_score REAL,
    -- Retrieved and actually used are different things, and reinforcement weights them
    -- differently. Distinguishing them requires recording both.
    used_in_reply   INTEGER NOT NULL DEFAULT 0,
    turn_id         TEXT,
    correlation_id  TEXT
);

CREATE INDEX idx_access_memory ON memory_access (memory_id, accessed_at DESC);

-- ---------------------------------------------------------------------------
-- Tombstones — forgetting, reversibly (docs/06 §7.3)
-- ---------------------------------------------------------------------------

CREATE TABLE tombstone (
    id                TEXT PRIMARY KEY,
    memory_id         TEXT NOT NULL,
    memory_kind       TEXT NOT NULL,
    reason            TEXT NOT NULL CHECK (reason IN
                        ('decayed','merged','contradicted','user_request','privacy_purge')),
    salience_at_death REAL,
    archived_body     TEXT,
    reversible        INTEGER NOT NULL DEFAULT 1,
    created_at        TEXT NOT NULL
);

CREATE INDEX idx_tombstone_memory ON tombstone (memory_id);

-- ---------------------------------------------------------------------------
-- Lexical search (docs/05 §5.3)
-- ---------------------------------------------------------------------------
-- External-content FTS5, so the base tables stay authoritative and the index is derived.
-- Triggers keep it in step inside the same transaction as the write, which is the whole
-- reason for keeping search in the same file as the data (ADR-0005).

CREATE VIRTUAL TABLE episode_fts USING fts5(
    title, content,
    content='episode', content_rowid='rowid',
    tokenize='porter unicode61'
);

CREATE TRIGGER episode_fts_insert AFTER INSERT ON episode BEGIN
    INSERT INTO episode_fts (rowid, title, content) VALUES (new.rowid, new.title, new.content);
END;

CREATE TRIGGER episode_fts_delete AFTER DELETE ON episode BEGIN
    INSERT INTO episode_fts (episode_fts, rowid, title, content)
    VALUES ('delete', old.rowid, old.title, old.content);
END;

CREATE TRIGGER episode_fts_update AFTER UPDATE OF title, content ON episode BEGIN
    INSERT INTO episode_fts (episode_fts, rowid, title, content)
    VALUES ('delete', old.rowid, old.title, old.content);
    INSERT INTO episode_fts (rowid, title, content) VALUES (new.rowid, new.title, new.content);
END;

CREATE VIRTUAL TABLE belief_fts USING fts5(
    statement,
    content='belief', content_rowid='rowid',
    tokenize='porter unicode61'
);

CREATE TRIGGER belief_fts_insert AFTER INSERT ON belief BEGIN
    INSERT INTO belief_fts (rowid, statement) VALUES (new.rowid, new.statement);
END;

CREATE TRIGGER belief_fts_delete AFTER DELETE ON belief BEGIN
    INSERT INTO belief_fts (belief_fts, rowid, statement) VALUES ('delete', old.rowid, old.statement);
END;

CREATE TRIGGER belief_fts_update AFTER UPDATE OF statement ON belief BEGIN
    INSERT INTO belief_fts (belief_fts, rowid, statement) VALUES ('delete', old.rowid, old.statement);
    INSERT INTO belief_fts (rowid, statement) VALUES (new.rowid, new.statement);
END;
