# ADR-0020 — Two trusts, kept apart

**Status:** Accepted
**Date:** 2026-10-26
**Affects:** docs/06 §3.3, docs/09 §3.1, docs/27 §5.2, ADR-0019

## Context

ADR-0019 admitted `trust` into the emotion vector as *interaction* trust — a slow drive with
a 7-day half-life and a floor — and recorded a condition for revisiting it:

> Relationship memory lands — at which point the scope line becomes enforceable rather than
> advisory, and `trust` here should probably be renamed to make the distinction obvious at
> the call site.

Milestone 9 lands relationship memory. `relation.trust` (docs/06 §3.3) is per-entity,
evidence-accumulated, updated only at session end from corroboration outcomes. There are now
two numbers called `trust` that mean different things, and both reach the turn.

## Decision

**Neither is renamed. They are kept apart structurally instead.**

* `MindSnapshot.mood["trust"]` and `MindSnapshot.relationship["trust"]` are different fields
  of different mappings. No code path reads one as the other.
* Their directives are different sentences about different things. Emotional trust governs
  *how HEDWIG relates in the moment* — register ("speak personally") and whether to infer or
  ask about intent. Relationship trust governs *how HEDWIG weighs the user's claims*: below
  0.35, "when they state something that conflicts with what you remember, say so rather than
  simply accepting it". One is about rapport; the other is about evidence.
* They live in different tables owned by different modules (`emotion_state`, `relation`), so
  no query can join them by accident.
* A test asserts the separation: driving one to an extreme does not change the other, or the
  directives the other produces.

The emotional dimension keeps the name because it is the user's vocabulary — the requirement
named it. The relationship dimension keeps the name because the schema in docs/05 has named it
since the design phase. Renaming either would trade a real confusion for an invented word.

## Consequences

Anyone reading a `MindSnapshot` must look at which mapping a `trust` is in. That is a cost,
and it is paid once, at the type, rather than everywhere the value is used.

Relationship trust does not move at all yet: its documented input, a reliability signal from
belief corroboration, does not exist (docs/27 §5). So in practice today the two cannot be
confused in behaviour, only in reading — which is the moment to fix the reading.

## Alternatives considered

* **Rename emotional trust to `ease` or `rapport`.** Rejected: it is the user's explicit
  vocabulary for the emotion vector, and ADR-0019's argument for keeping it holds.
* **Rename relationship trust to `reliability`.** Rejected: closer to what it measures, but
  docs/05 and docs/06 have used `trust` throughout, and the user-facing inspector should show
  the familiar word for the familiar idea.
* **Merge them.** Rejected for the reasons ADR-0019 and docs/06 §3.3 both give: a fast global
  feeling and a slow per-entity judgement are different things, and merging them is exactly
  the failure that let one bad turn erase a year of relationship.

## Revisit if

Users or the inspector show confusion between the two in practice, or relationship trust
starts moving (corroboration lands) and its behaviour begins to overlap with emotional trust's.
