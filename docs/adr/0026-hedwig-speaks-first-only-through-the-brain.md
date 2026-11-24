# ADR-0026 — HEDWIG speaks first only through the brain, about what the user asked for

**Status:** Accepted
**Date:** 2026-11-24
**Affects:** docs/11 §7 and §14, docs/07 §5, `initiative/`, `brain/graph.py`, `api/routes/stream.py`, migration 0022

## Context

Until now HEDWIG only ever answered. Findings and goal follow-ups reached the user when the user
next spoke (docs/11 §7 modes 1 and 2). The live channel now exists, so HEDWIG can say something
first — the behaviour most likely to make a companion feel present, and the one most likely to
make it a nuisance.

## Decision

1. **Only about what the user asked for or is working on.** A finding for a question the user
   asked, or one that overlaps an active user-stated goal; or a check-in on such a goal's open
   question. Never re-engagement, never self-generated goals, never "interesting facts".
2. **Only when every measurable gate of docs/11 §7 holds**, with a hard cap of one a day.
3. **Only through the brain.** `Brain.speak_first` takes the session lock, opens or resumes the
   session (announcing it like any other), and records the message. The initiative module
   decides what and when; it never writes conversation rows itself, and it never lands inside a
   turn.
4. **Templated text, with the content's trust tier.** No model composes the opening.
5. **Delivered through presence**, an in-process registry of subscribed sockets — ephemeral, not
   the bus. The message is in the conversation either way; the push only makes it appear now. Each
   delivery has its own deadline, so one stalled socket delays no one.
6. **On by default**, with `initiative.enabled` to turn it off and *Not interested* on every
   message.

## Consequences

The first unprompted message is a small, exact one. A user who never opens the app gets nothing
(no OS notifications yet). Every offer is a row (`initiative_offer`) and a conversation message,
so it is explainable, exportable and purged with its session.

## Alternatives

(a) A LangGraph `InitiativeGraph` (docs/07 §5) — rejected for now: a fixed sequence of gates
earns no graph, as with the reflection tiers. (b) Model-written openings — rejected until one
passes the honesty gate. (c) Publishing offers on the durable bus for the socket to consume —
rejected: sockets are ephemeral and an offer to nobody should not be replayed later. (d) Off by
default like curiosity's network access (ADR-0012) — rejected: nothing leaves the machine, the
cap is one a day, and the designed behaviour should be the default one.
