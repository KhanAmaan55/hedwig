# ADR-0025 — Projects are goals with a journal, not a new kind of thing

**Status:** Accepted
**Date:** 2026-11-23
**Affects:** docs/08 §6, docs/06 §3, `goals/`, `reflection/session.py`, migration 0020

## Context

The vision asks HEDWIG to remember the user's ongoing projects — decisions, progress, open
questions — so that "how did that project go?" has a real answer. MEM-06 left the design open:
an entity kind plus beliefs, or a table of its own.

What a project *is* already exists. A goal (GOA-01) has a description, why it exists, how it
ends, a status (proposed, active, blocked, done, abandoned), progress and priority; session
reflection already proposes goals from conversations ("Help the user finish their Rust
compiler") and moves their progress. What is missing is the project's *history*: what was
decided, what is unresolved, what got in the way, when it moved.

## Decision

1. **A project is a goal.** No new entity, no new lifecycle. The Goals tab, the API and the CLI
   are the project view.
2. **Each goal keeps a journal**: `goal_note` (decision, progress, question, blocker, done),
   each with its text, when, and where it came from (`system`, `reflection`, `user`, and the
   session it came from).
3. **Three writers.** The goals store notes its own changes deterministically (activated,
   progress 40% → 60%, done, dropped). Session reflection notes what a conversation decided or
   left open for goals it was shown. The user can add a note. Nothing else writes.
4. **Into the turn by composition.** The goals mind reader adds each active goal's latest notes
   to its line, and — when the user's message names a finished or dropped project — that project
   too, marked finished, with its journal. That is what answers "how did it go?".

## Consequences

* One concept for the user ("what HEDWIG is helping me with"), one table added.
* A project that was never a goal (mentioned once, never proposed) has no journal. Reflection
  proposes goals for projects it hears about, so this is the rare case.
* Journal notes from reflection are a 2B model's summary: they are labelled with their source
  and date, shown in the Goals tab, and change nothing else — a wrong note is visible and
  harmless, unlike a wrong belief.

## Alternatives considered

* **An entity kind `project` plus beliefs about it.** Rejected: it duplicates the goal lifecycle,
  and beliefs are the wrong shape for a history — they supersede, a journal accumulates.
* **Journal entries as episodes.** Rejected: they would decay and be forgotten like small talk,
  and a project's decisions should not fade because nobody mentioned them for a month.
