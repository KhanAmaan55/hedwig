-- 0013 — The egress audit (docs/13 §13, docs/21 §5). Owner: `knowledge`.
--
-- Every request HEDWIG makes to the network, with what it sent. Local only, like everything
-- else; it exists so the user can see exactly what left their machine. New table.

CREATE TABLE egress_log (
    id          TEXT PRIMARY KEY,
    purpose     TEXT NOT NULL,          -- 'search', 'read', 'robots'
    method      TEXT NOT NULL,
    host        TEXT NOT NULL,
    url         TEXT NOT NULL,
    query       TEXT,                   -- the search text, when there is one
    status      INTEGER,
    bytes       INTEGER,
    outcome     TEXT NOT NULL,          -- 'ok', 'refused', 'error', 'quarantined', 'disallowed'
    detail      TEXT,
    created_at  TEXT NOT NULL
);

CREATE INDEX idx_egress_time ON egress_log (created_at DESC);
