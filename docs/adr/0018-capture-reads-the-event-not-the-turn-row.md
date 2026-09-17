# ADR-0018 — Capture reads the event, not the turn row

**Status:** Accepted
**Date:** 2026-09-16
**Affects:** docs/06 §4, docs/07 §3, docs/26 §3.1

## Context

Two architecture documents specify the end of a turn in different orders.

* **docs/07 §3** — the turn graph runs `learn → finalize`. `learn` publishes
  `conversation.turn.completed`; `finalize` persists the turn record.
* **docs/06 §4** — the capture sequence persists the turn *first*, then publishes
  `conversation.turn.completed`, then a subscriber captures.

Milestone 6 connects both ends for the first time, so the disagreement stops being
theoretical. Whichever order we choose, a process death in the gap loses something, and
what it loses is different in each case.

## Decision

**The graph order in docs/07 stands: `learn` publishes, then `finalize` persists.** The
capture subscriber is made independent of the turn row: the `conversation.turn.completed`
payload carries the input, the reply, the status and the recalled memory ids, so capture
never reads back what `finalize` has not yet written.

Capture's only database prerequisite is the `session` row, and that is written by `ingest`
at the start of the turn.

## Consequences

If the process dies between `learn` and `finalize`, a memory may exist for a turn with no
turn row. That is the cheaper loss:

* The **message log** is the authoritative record of what was said, it is written at
  `ingest` and at `finalize`, and docs/05 §7 keeps it indefinitely precisely so any lost
  extraction can be re-derived from it.
* The **turn row** is timing, status and links. Losing one costs a line in a latency report.

The reverse ordering fails worse. A persisted turn that memory never saw is silent
forgetting — the user said something, the system agreed it happened, and nothing remembers
it. That is the failure the whole memory subsystem exists to prevent, and it would be
invisible.

A second consequence, accepted deliberately: the event payload is larger than the "key
payload fields" listed in docs/04 §5. The alternative is a subscriber that queries the
database on every turn to reassemble what the publisher already had in hand.

## Alternatives considered

* **Move persistence into `learn`, before the publish.** Collapses the two nodes' concerns
  and contradicts the node table in docs/07 §3.1, which is also the contract the
  single-writer state test enforces.
* **Publish from `finalize` instead of `learn`.** Leaves `learn` with nothing to do, and
  `learn` is the documented hook for everything that learns from a turn.
* **Two-phase: publish an unpersisted marker, confirm after `finalize`.** Correct, and
  entirely disproportionate. It buys consistency for a row nothing depends on.
* **Make capture read the turn row and rely on the bus's retry to cover the race.** Turns
  an ordinary path into one that depends on redelivery timing, and the first symptom would
  be intermittently missing memories.

## Revisit if

The turn row becomes load-bearing for anything beyond reporting — for example if
`working_set_id` is needed to reconstruct what a memory was derived from, or if a turn row
gains a state machine that other modules read. At that point the two-phase alternative is
the one to build.
