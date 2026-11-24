-- 0021 — The monthly identity report (docs/12 §7, §16; REF-03). Owner: `reflection`. New table.
--
-- One row per month: that the report exists, its summary, and whether the user has seen it.
-- Everything else in a report is assembled from the live tables when it is read, so an erased
-- memory leaves past reports and a reverted change shows as reverted.

CREATE TABLE identity_report (
    month       TEXT PRIMARY KEY,           -- "YYYY-MM"
    summary_id  TEXT,                       -- the month's period_summary episode, if one was made
    created_at  TEXT NOT NULL,
    seen_at     TEXT
);
