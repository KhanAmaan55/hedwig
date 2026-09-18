# 24 — Decision Records

**Status:** Design · **Depends on:** all documents

---

## Convention

This file holds the fourteen foundational decisions the rest of the documentation assumes.
From Phase 1 onward, **each new architectural decision gets its own file** at
`docs/adr/NNNN-short-title.md` using the same template, and is listed in the index below.

Template: Context → Decision → Consequences → Alternatives considered → Revisit if.

A decision is recorded here when it (a) constrains more than one subsystem, (b) would be
expensive to reverse, or (c) is one a future reader would otherwise assume was accidental.

| ADR | Title | Status |
|---|---|---|
| [0001](#adr-0001) | Modular monolith in one process | Accepted |
| [0002](#adr-0002) | Commands are calls; facts are events | Accepted |
| [0003](#adr-0003) | Not event-sourced | Accepted |
| [0004](#adr-0004) | LangGraph for orchestration, nodes as adapters | Accepted |
| [0005](#adr-0005) | SQLite as the single source of truth | Accepted |
| [0006](#adr-0006) | Four memory stores, two derived views | Accepted |
| [0007](#adr-0007) | Two-tier local models | Accepted |
| [0008](#adr-0008) | Cognition siblings communicate only by events | Accepted |
| [0009](#adr-0009) | Expression frames bypass the event bus | Accepted |
| [0010](#adr-0010) | Personality drift is guardrailed and reversible | Accepted |
| [0011](#adr-0011) | Only `USER` content may instruct | Accepted |
| [0012](#adr-0012) | Curiosity is off by default | Accepted |
| [0013](#adr-0013) | Carry `principal_id` despite being single-user | Accepted |
| [0014](#adr-0014) | Observability is a product feature | Accepted |
| [0015](adr/0015-electron-desktop-shell.md) | Electron desktop shell | Accepted |
| [0016](adr/0016-in-tree-plugin-loader.md) | In-tree plugin loader, not an extension surface | Accepted |
| [0017](adr/0017-service-registry-not-a-service-locator.md) | The service registry manages lifecycle, not lookup | Accepted |
| [0018](adr/0018-capture-reads-the-event-not-the-turn-row.md) | Capture reads the event, not the turn row | Accepted |
| [0019](adr/0019-the-emotion-vector-is-six-named-drives.md) | The emotion vector is six named drives, and trust is one of them | Accepted |

---

## ADR-0001 {#adr-0001}
### Modular monolith in one process

**Context.** The project requires strict modularity and replaceability. The reflex is to split
modules into services. The system is single-user, local-first, latency-sensitive, and must be
operable by one person on a laptop.

**Decision.** One Python process, one asyncio loop, modular internally via ports and an
in-process event bus. The bus has an internal transport seam so a module can later be extracted
without modifying it.

**Consequences.** Sub-millisecond inter-module calls; one stack trace; one thing to install. No
fault isolation between modules and no independent scaling. Modularity now depends on enforced
discipline rather than physics — so import-linter contracts and port conformance suites run in CI
from Phase 0.

**Alternatives.** (a) Microservices — rejected: cost with no benefit for one user, and network
serialisation on every memory read. (b) Plugin architecture with dynamic loading — rejected:
harder to reason about, and the extension surface is a non-goal.

**Revisit if.** A module needs a different runtime or hard resource isolation. Extract that one
module behind its existing port; do not split everything.

---

## ADR-0002 {#adr-0002}
### Commands are calls; facts are events

**Context.** `CLAUDE.md` prefers event-driven communication *where appropriate*. Applied
universally, event-driven design makes every code path an untraceable choreography.

**Decision.** If the caller needs a result, it is a direct call through a port (a command). If
the caller is announcing that something happened and does not care who reacts, it is an event.
No request/reply over the bus. Events must never be load-bearing for the correctness of the
current turn.

**Consequences.** The turn is readable top to bottom. Reactions are decoupled. Each interaction
needs a small judgement about which mechanism applies, and reviewers must enforce it.

**Alternatives.** (a) Everything events — rejected: undebuggable. (b) Everything calls —
rejected: welds cognition together.

**Revisit if.** Never expected.

---

## ADR-0003 {#adr-0003}
### Not event-sourced

**Context.** The system persists every event. Event sourcing — deriving all state by replaying
events — is the natural next step and is tempting for a system that wants full auditability.

**Decision.** Events are an audit log plus a bounded (24 h) recovery mechanism. Authoritative
state lives in ordinary tables. State is never reconstructed by replaying history.

**Consequences.** Simple, queryable state. No free time-travel of state — covered instead by
nightly identity snapshots ([08](08-state-management.md) §7.1), which address the actual need
(rolling back cognitive drift) at a fraction of the cost. Handler logic can evolve freely,
because old events are never replayed into new code.

**Alternatives.** Full event sourcing — rejected: every handler change becomes retroactively
meaningful, replays become correctness hazards, and six months of replay is operationally
unusable on a laptop.

**Revisit if.** Never expected. If deeper history is wanted, extend snapshots, not replay.

---

## ADR-0004 {#adr-0004}
### LangGraph for orchestration, with nodes as adapters

**Context.** The turn needs sequencing, conditional routing, tool loops, streaming,
human-in-the-loop interrupts, and crash resumption. LangGraph provides all of it. It is also a
young, fast-moving library, and the project's stated horizon is years.

**Decision.** Use LangGraph, with a hard rule: **a node reads state, calls one port, writes
state.** No business logic in nodes. All routing is pure functions over state.

**Consequences.** We get checkpointing, interrupts, streaming and time-travel debugging without
building them. If LangGraph must be replaced, roughly 400 lines of glue are rewritten and the
cognitive system is untouched. Nodes look thin, which occasionally reads as indirection — and is
the point.

**Alternatives.** (a) Hand-rolled state machine — rejected: we would rebuild checkpointing and
interrupts badly. (b) A heavier agent framework — rejected: inherits abstractions we do not want.

**Revisit if.** The API churns painfully, or checkpointing proves unreliable. The exit is cheap
by construction.

---

## ADR-0005 {#adr-0005}
### SQLite as the single source of truth, including vectors

**Context.** The system needs relational data, full-text search and vector search. The common
architecture is a relational database plus a dedicated vector database.

**Decision.** One SQLite file in WAL mode, with FTS5 for lexical search and `sqlite-vec` for
vectors. A separate `checkpoints.db` for framework state, and blobs on the filesystem.

**Consequences.** A memory write and its index updates are one transaction — no possibility of a
memory existing without its vector, or a vector pointing at a deleted memory. The user owns one
portable file. Zero administration. Costs: one writer at a time (mitigated by the governor and
short transactions), a practical vector ceiling around 10⁶, and fewer ANN tuning knobs than a
dedicated engine.

**Alternatives.** (a) Postgres + pgvector — rejected: a daemon to install and administer for a
single-user local app. (b) SQLite + Chroma/LanceDB — rejected: two sources of truth and a
consistency problem for every write.

**Revisit if.** Vector query p95 exceeds 100 ms, vectors approach 10⁶, or multi-device sync
becomes a goal. Both indexes are behind ports, so the swap is bounded.

---

## ADR-0006 {#adr-0006}
### Four memory stores, two derived views

**Context.** The README lists five memory types, three of which are not stores.

**Decision.** Four durable stores — episodic, semantic, social, procedural — plus two derived
views (the recent-turn window and the per-turn working set). Episodic and procedural are
additions; long-term, short-term and working are demoted from stores.

**Consequences.** Fewer stores to keep consistent and more capability: episodic memory gives
beliefs their provenance and reflection its input; procedural memory is what makes adaptation
felt. Departs from the README's wording, which is why it is written down here and in
[06](06-memory-architecture.md) §2.

**Alternatives.** Implement the README's five literally — rejected: three synchronisation
problems for no capability, and the two most valuable stores missing.

**Revisit if.** Never expected.

---

## ADR-0007 {#adr-0007}
### Two-tier local models

**Context.** Appraisal, capture, planning, summarisation and assessment are high-volume
structured operations. Using the conversational model for all of them makes every cognitive
operation cost as much as a reply, and makes nightly reflection unaffordable on a laptop.

**Decision.** A `conversational` tier (7–14 B) for user-facing prose and a `utility` tier
(1–3 B) for everything structured, routed by purpose rather than by caller choice. Failed
structured output escalates to the conversational tier once before falling back to a typed
default.

**Consequences.** The whole cognitive apparatus becomes affordable. Risk: small models are less
reliable at schema adherence — mitigated by repair, retry, escalation, typed defaults, and a
monitored schema-failure rate with a 2 % threshold. This is the project's most consequential
unvalidated assumption ([23](23-challenged-assumptions.md) §10).

**Alternatives.** (a) One model — rejected on cost. (b) Rules only, no model for cognition —
rejected: appraisal and extraction genuinely need language understanding. (c) Cloud for utility
work — rejected: violates INV-8 by default.

**Revisit if.** Schema failures exceed 2 % sustained. Then a larger utility model, or
grammar-constrained decoding.

---

## ADR-0008 {#adr-0008}
### Cognition siblings communicate only by events, via snapshot projections

**Context.** Emotion needs personality baselines; curiosity needs goals; reflection needs
everything. Direct imports would weld the cognitive core into one inseparable lump — the exact
outcome the project forbids.

**Decision.** Cognition modules never import each other. They publish **full-snapshot** state
events (`personality.profile.updated`, `emotion.state.changed`) and consumers keep small cached
projections. Enforced by an import-linter independence contract.

**Consequences.** Each cognitive module is genuinely replaceable and independently testable.
Costs: eventual consistency (microseconds in process, zero after a restart because snapshot
events replay), and small duplicated projections. Snapshot-rather-than-delta payloads make
out-of-order delivery harmless.

**Alternatives.** (a) A shared mind-state object all modules mutate — rejected: the coupling
disaster. (b) Direct calls between siblings — rejected: same. (c) Delta events — rejected:
order-dependent and restart-fragile.

**Revisit if.** Never. This is the load-bearing decoupling rule.

---

## ADR-0009 {#adr-0009}
### Expression frames bypass the event bus

**Context.** The avatar needs ~10 frames/second. Every bus event is validated and appended to a
durable outbox.

**Decision.** Expression frames go directly from the expression module to the WebSocket hub. The
bus still carries `avatar.expression.requested` at *intent* granularity (a few per turn) for the
inspector and tests. Frames are never persisted.

**Consequences.** No wasteful durable writes; no bus queue pressure. A documented exception to
[04](04-communication-and-event-bus.md), which is why it is an ADR rather than a quiet choice.
Frames are not replayable — acceptable, because they are derivable from `emotion_history` plus
the turn record.

**Alternatives.** (a) Frames on the bus — rejected: ~36 000 durable writes an hour for data with
a 100 ms lifespan. (b) A second bus instance with durability off — rejected: two buses is worse
than one documented exception.

**Revisit if.** Never expected.

---

## ADR-0010 {#adr-0010}
### Personality drift is guardrailed, evidence-based and reversible

**Context.** "Traits evolve over time" with no constraints converges on a sycophantic mirror or
an unrecognisable stranger.

**Decision.** Seven sequential guardrails; only explicit feedback, instructions and multi-day
behavioural patterns count as evidence (never emotional state); 0.03 per proposal, 0.02/week per
trait, 0.25 lifetime from anchor; 14-day anti-oscillation; per-trait floors and ceilings; an
immutable identity core; full reversibility including nightly snapshot restore. Reflection
proposes; personality decides.

**Consequences.** Drift is safe, slow and explainable, and a user can always say "go back". The
likely failure inverts to *no drift at all*, so zero applied drift for eight weeks is treated as
a bug report. Rejections are surfaced with the rule that fired, so slowness never looks arbitrary.

**Alternatives.** (a) Free adaptation — rejected: sycophancy. (b) No adaptation — rejected:
abandons a core premise. (c) User-configured personality only — rejected: it is a settings screen,
not a relationship.

**Revisit if.** Users consistently report adaptation is too slow. Relax caps in config, not in
code, and keep the guardrails.

---

## ADR-0011 {#adr-0011}
### Only `USER` content may be treated as an instruction

**Context.** HEDWIG ingests web content, local documents and tool output. Any of it may contain
text aimed at a language model.

**Decision.** Five trust tiers. Only `USER` content may instruct. Everything else is data,
enforced simultaneously by prompt structure, **capability denial** (side-effecting tools are
absent from the tool list during untrusted operations), and permanent trust propagation onto
derived memories.

**Consequences.** Prompt injection becomes bounded: a perfect injection can at most cause a
false finding, which still needs corroboration to become an active belief. Every memory carries a
tier forever, which adds a column and a rule to every path — and enables provenance, hedging and
purge.

**Alternatives.** (a) Prompt-level instructions only — rejected: a request to a model, and models
can be talked out of it. (b) Refuse untrusted content entirely — rejected: eliminates the Live
Internet tier.

**Revisit if.** Never. Capability denial is the strongest and cheapest control available.

---

## ADR-0012 {#adr-0012}
### Curiosity is disabled by default

**Context.** The curiosity engine is the only subsystem that touches the network. INV-8 requires
egress to be opt-in per destination.

**Decision.** `curiosity.enabled = false` and `privacy.allow_network = false` by default; both
must be set. Gap detection and local exploration work regardless, so the feature is useful
offline. Every outbound request is logged verbatim in an auditable egress log.

**Consequences.** A default install makes zero network requests and is fully functional. The
gap register is valuable on its own — a visible list of what HEDWIG knows it does not know. Cost:
the flagship "proactive discovery" behaviour is invisible until deliberately enabled.

**Alternatives.** (a) On by default with an allowlist — rejected: violates privacy-by-default and
makes the largest attack surface opt-out. (b) Removed entirely — rejected: a core premise of the
project.

**Revisit if.** Never for the default.

---

## ADR-0013 {#adr-0013}
### Carry `principal_id` on every table despite being single-user

**Context.** Multi-user is an explicit non-goal. Retrofitting a tenancy column into a schema with
40 tables, dozens of indexes and years of user data is one of the most painful migrations
available.

**Decision.** Every domain table carries `principal_id TEXT NOT NULL DEFAULT 'local'`. No
multi-user features are built, no code branches on it, and no indexes lead with it.

**Consequences.** Roughly 8 bytes per row and one column in every `CREATE TABLE`. In exchange, if
a second principal ever matters, the schema is already shaped for it. This is cheap insurance
against an expensive future, and the kind of decision that looks like waste until the day it does
not.

**Alternatives.** (a) Omit it — rejected: the migration cost is asymmetric with the carrying
cost. (b) Build multi-user now — rejected: a non-goal, and it would complicate every query.

**Revisit if.** Never; the cost is negligible either way.

---

## ADR-0014 {#adr-0014}
### Observability is a product feature, ranked above responsiveness

**Context.** HEDWIG's behaviour is emergent from retrieval, emotion, personality, procedures and
background work that ran unobserved. "Why did it do that?" is the default question, from both the
developer and the user.

**Decision.** Observability ranks fourth in the quality attributes, above responsiveness. The
`/explain` endpoint and the Mind Inspector are Phase-3/5 deliverables, not "later". Every
cognitive judgement records enough to be reconstructed: `working_set_log` with score breakdowns,
`llm_call` with prompt version and model id, `appraisal` with dimensions and rationale,
`personality_history` with evidence. Metrics include cognitive-health measures that detect the
*absence* of designed behaviour, not just errors.

**Consequences.** Debugging emergent behaviour is tractable, and the user can verify claims via
citation chips and provenance. Costs: schema weight, ~90-day retention of explanation records,
and a constraint on every subsystem to record enough. That constraint is the mechanism — designing
`/explain` early is what forced the tables to carry the right columns.

**Alternatives.** (a) Logs only — rejected: cannot answer "why did it say that?". (b) Add
observability after the features — rejected: the records must be written when the decision is
made, so it cannot be retrofitted.

**Revisit if.** Never expected. Prune retention windows if storage becomes an issue.
