-- 0014 — Curiosity: the gap register and findings (docs/05 §5.5, docs/11 §13). Owner: `curiosity`.
--
-- docs/05's `gap` and `finding`, without `source` (no allowlist yet, docs/11 §13): a finding
-- carries its sources as JSON. `finding.status` adds 'dismissed' and 'confirmed', the user's
-- two answers to the feed. New tables; nothing existing changes.

CREATE TABLE gap (
    id            TEXT PRIMARY KEY,
    question      TEXT NOT NULL,
    origin        TEXT NOT NULL CHECK (origin IN
                    ('unanswered_question','low_confidence_belief','contradiction',
                     'user_interest','goal_support','source_watch','user')),
    origin_ref    TEXT,
    priority      REAL NOT NULL DEFAULT 0.5 CHECK (priority BETWEEN 0 AND 1),
    status        TEXT NOT NULL DEFAULT 'open'
                  CHECK (status IN ('open','exploring','satisfied','abandoned','blocked')),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    goal_id       TEXT REFERENCES goal(id),
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    version       INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX idx_gap_open ON gap (status, priority DESC);

CREATE TABLE finding (
    id              TEXT PRIMARY KEY,
    gap_id          TEXT REFERENCES gap(id),
    summary         TEXT NOT NULL,
    sources         TEXT NOT NULL DEFAULT '[]',
    quality_score   REAL NOT NULL DEFAULT 0,
    novelty_score   REAL NOT NULL DEFAULT 0,
    relevance_score REAL NOT NULL DEFAULT 0,
    trust_tier      TEXT NOT NULL DEFAULT 'untrusted',
    status          TEXT NOT NULL DEFAULT 'new'
                    CHECK (status IN ('new','promoted','surfaced','discarded','quarantined',
                                      'dismissed','confirmed')),
    promoted_belief_id TEXT REFERENCES belief(id),
    surfaced_at     TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX idx_finding_status ON finding (status, created_at DESC);
