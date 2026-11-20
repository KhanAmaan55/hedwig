-- 0010 — One relationship subscription instead of two (docs/27 §5.1). Owner: `memory`.
--
-- Relationship memory read `conversation.turn.completed` and `conversation.session.ended`
-- through two subscriptions, which the bus does not order against each other (docs/04 §4). An
-- end could overtake the last turn's tally. It now reads both through one subscription,
-- `memory-relationship`.
--
-- A new subscription name has no delivery history, and startup recovery re-delivers every
-- event in the replay window that a subscription has not processed (core/bus/in_process.py,
-- `_recover`). Without this migration every recent turn would be tallied a second time.
--
-- Additive only: the old rows are copied, never changed or deleted. No cursor is set, on
-- purpose — recovery then checks every event in the window against the copied ledger, which is
-- exact, whereas either old cursor alone could skip the other's undelivered events.

INSERT OR IGNORE INTO processed_event
    (subscription, event_id, status, attempts, last_error, processed_at)
SELECT 'memory-relationship', event_id, status, attempts, last_error, processed_at
FROM processed_event
WHERE subscription IN ('memory-relationship-turns', 'memory-relationship-sessions');
