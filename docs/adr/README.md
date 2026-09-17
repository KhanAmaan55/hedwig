# Architecture Decision Records

The fourteen foundational decisions that the rest of the documentation assumes live in
[`../24-decision-records.md`](../24-decision-records.md) — they were made together, as one
coherent design, and are easier to read together.

**From Phase 1 onward, each new architectural decision gets its own file here**, named
`NNNN-short-title.md`, starting at `0015`. Add it to the index table in
`../24-decision-records.md` in the same commit.

## When to write one

Write an ADR when the decision:

- constrains more than one subsystem, or
- would be expensive to reverse, or
- is one a future reader would otherwise assume was accidental.

Do not write one for a choice contained inside a single module — that belongs in the module's
own `README.md`.

## Template

```markdown
# ADR-NNNN — Short title

**Status:** Proposed | Accepted | Superseded by ADR-MMMM
**Date:** YYYY-MM-DD
**Affects:** docs/NN, docs/NN

## Context

What forced a decision. Include the constraint that makes this non-obvious.

## Decision

What we are doing, stated so it can be checked.

## Consequences

What this buys, and what it costs. State the costs plainly — an ADR with no costs is
advocacy, not a record.

## Alternatives considered

Each rejected option, with the reason. This is the section future readers actually need.

## Revisit if

The specific signal that should reopen this. "Never expected" is a valid answer, and it is
more useful than an invented trigger.
```

## Superseding

Never edit an accepted ADR's decision. Write a new one, set the old one's status to
`Superseded by ADR-MMMM`, and update the affected subsystem documents.
