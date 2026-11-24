-- 0022 — What HEDWIG said unprompted (docs/11 §14; ADR-0026; INI-01). Owner: `initiative`.
--
-- One row per unprompted message: what it was about (a finding or a goal), where it was said,
-- and whether the user waved it away. The words themselves are the conversation message; no
-- copy here, so erasing the conversation erases them.

CREATE TABLE initiative_offer (
    id            TEXT PRIMARY KEY,
    kind          TEXT NOT NULL CHECK (kind IN ('finding', 'goal_check_in')),
    ref_id        TEXT NOT NULL,           -- the finding or goal it was about
    session_id    TEXT NOT NULL,
    message_id    TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'offered' CHECK (status IN ('offered', 'dismissed')),
    created_at    TEXT NOT NULL,
    dismissed_at  TEXT
);

CREATE INDEX initiative_offer_created ON initiative_offer (created_at);
CREATE INDEX initiative_offer_ref ON initiative_offer (kind, ref_id);
