-- 0023 — Corrections proposed in conversation (docs/06 §19; MEM-07). Owner: `memory`.
--
-- "No, she lives in Lisbon": a belief HEDWIG holds, and how the user's words say it should
-- read. Nothing changes until the user says yes; a yes is docs/06 §18's correction, and the
-- replacement's id is kept here.

CREATE TABLE correction_proposal (
    id              TEXT PRIMARY KEY,
    memory_id       TEXT NOT NULL,          -- the belief the user said is wrong
    proposed_text   TEXT NOT NULL,          -- how it should read, from the user's words
    session_id      TEXT,
    turn_id         TEXT,
    status          TEXT NOT NULL DEFAULT 'proposed'
                    CHECK (status IN ('proposed', 'accepted', 'declined', 'stale')),
    replacement_id  TEXT,                   -- the belief the correction created, once accepted
    created_at      TEXT NOT NULL,
    settled_at      TEXT
);

CREATE INDEX correction_proposal_session ON correction_proposal (session_id, status);
CREATE INDEX correction_proposal_memory ON correction_proposal (memory_id);
