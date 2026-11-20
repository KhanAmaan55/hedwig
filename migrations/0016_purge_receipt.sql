-- 0016 — Purge receipts (docs/21 §7.4, ADR-0024). Owner: `privacy`. New table only.
--
-- A record that a purge happened and what it removed, so the user can see it did. Counts only:
-- never the phrase, never ids, never text — a receipt that quoted what was erased would undo it.

CREATE TABLE purge_receipt (
    id              TEXT PRIMARY KEY,
    scope_kind      TEXT NOT NULL CHECK (scope_kind IN ('memory','session','phrase')),
    created_at      TEXT NOT NULL,
    counts          TEXT NOT NULL DEFAULT '{}',
    verified        TEXT NOT NULL CHECK (verified IN ('file','query','failed')),
    also_elsewhere  INTEGER NOT NULL DEFAULT 0,
    backups_deleted INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_purge_receipt_time ON purge_receipt (created_at DESC);
