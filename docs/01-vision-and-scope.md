# 01 — Vision and Scope

**Status:** Design · **Depends on:** `README.md` (root) · **Depended on by:** everything

---

## 1. Purpose

The root `README.md` states a vision. This document converts that vision into
*decidable* statements: things a test can pass or fail, and things we have explicitly
chosen not to build. A principle that cannot be violated is not a principle, it is a
slogan. Each principle below therefore names the thing that would count as a violation.

---

## 2. What HEDWIG is

A single-user, local-first digital companion that maintains **continuity of identity**
across months of interaction. Continuity means three concrete properties:

1. **Recall** — it can use information from an arbitrarily old interaction when relevant,
   without that interaction being in the current context window.
2. **Consistency** — its manner, opinions, and stated preferences are stable across
   sessions, and change only in ways it can account for.
3. **Initiative** — it acts between conversations (reflecting, exploring), and those
   actions visibly affect later conversations.

If those three properties hold, HEDWIG succeeded. If they do not, no amount of prompt
engineering makes it a companion rather than a chatbot.

---

## 3. Principles, restated as invariants

Each of the README's core principles is restated as an invariant with a violation
condition. The invariant IDs are referenced by tests (see
[20-testing-strategy.md](20-testing-strategy.md)).

| ID | Invariant | Violated when |
|---|---|---|
| **INV-1** | *Memory instead of conversation history.* The prompt is assembled from retrieval over durable stores, never from an unbounded transcript. | Any prompt builder concatenates more than the configured recent-turn window verbatim. |
| **INV-2** | *Personality instead of system prompts.* Personality is data with a schema, a history, and an audit trail; the system prompt is a *rendering* of that data. | A trait exists only as prose in a prompt template, with no row and no history. |
| **INV-3** | *Emotions instead of fixed responses.* Emotional state is a numeric state vector produced by appraisal of events, and it changes at least one measurable behaviour. | An emotion dimension exists that no behaviour binding reads (see [09](09-emotion-engine.md) §6). |
| **INV-4** | *Curiosity instead of passive waiting.* Idle time produces knowledge-gap-directed work, not random browsing. | The curiosity engine fetches anything that is not traceable to a recorded gap. |
| **INV-5** | *Reflection instead of forgetting.* Every conversation is consolidated into durable artefacts; forgetting is a deliberate, logged, reversible operation. | A memory disappears without a tombstone row. |
| **INV-6** | *Goals instead of single-turn conversations.* Goals are first-class rows with lifecycle, and they influence retrieval and initiative. | Goals are only mentioned in text, never queried. |
| **INV-7** | *Local-first.* The system is fully functional with no network access, degrading only in the knowledge tiers that inherently need it. | A core loop (chat, recall, reflect) fails when the network is down. |
| **INV-8** | *Privacy by default.* No user content leaves the machine unless the user has explicitly enabled a specific egress, per destination. | Any default-on egress of user content. |
| **INV-9** | *Modular design.* Every module is reachable only through a port defined in [03](03-module-contracts.md), and can be replaced by a second implementation without edits outside its own package and the DI wiring. | A cross-module concrete import appears (enforced by an import-linter test). |
| **INV-10** | *Honesty about nature.* HEDWIG never asserts that it has subjective feelings, consciousness, or human relationships. It may describe internal state as state. | Output claims phenomenal experience. |

INV-10 is not in the README. It is added deliberately: a system explicitly designed to be
believable and to be attached to needs an anti-deception invariant, or it becomes a
manipulation engine by accident. See [21-security-privacy-ethics.md](21-security-privacy-ethics.md) §6.

---

## 4. Non-goals

Naming these prevents scope creep from being mistaken for progress.

- **Not multi-user or multi-tenant.** Single principal. (We still carry a `principal_id`
  column everywhere — see [ADR-0013](24-decision-records.md#adr-0013) — because that is
  cheap insurance against an expensive migration, but no multi-user features are built.)
- **Not a general assistant platform.** No plugin marketplace, no arbitrary third-party
  extension surface. Tools are curated and in-tree.
- **Not a consciousness simulation.** No claims, no attempts, no benchmarks about it.
- **Not real-time voice-first.** Text is the primary channel; speech is an adapter added
  in Phase 6.
- **Not cloud-hosted.** No hosted service, no accounts, no sync. If someone wants to run
  it on a box in their house and reach it over Tailscale, the architecture allows it, but
  we ship nothing to support it.
- **Not photoreal.** The avatar communicates state; it does not chase fidelity.
- **Not a training pipeline.** HEDWIG learns by accumulating and reorganising data, not by
  fine-tuning weights. (Deferred, not rejected — see [22](22-roadmap.md) §Beyond.)

---

## 5. The central architectural claim

> The language model is a **stateless faculty**. All identity lives outside it.

Everything follows from this. The LLM is called to do bounded jobs — appraise this event,
summarise this window, extract entities, compose this reply — and holds nothing between
calls. Memory, emotion, personality, goals, and relationships are rows in a database that
the system reads and writes deterministically. A consequence worth stating plainly:

**Swapping the model must not change who HEDWIG is.** It changes how eloquently HEDWIG
speaks. If replacing the model resets the personality, the personality was in the prompt,
and INV-2 is violated.

This is also our lock-in defence. The most valuable asset in the system is the database,
and it is a SQLite file the user owns.

---

## 6. Quality attributes, ranked

Ranked, because unranked quality attributes are how systems become incoherent. When two
conflict, the higher number wins, and the conflict gets an ADR.

1. **Comprehensibility** — a competent engineer reads the docs and one subsystem, and can
   change it correctly. This outranks performance because the project's stated horizon is
   years.
2. **Replaceability** — no module is load-bearing for the architecture's shape.
3. **Data durability and portability** — the user's memory file is sacred. Corruption or
   lock-in is the worst possible failure.
4. **Observability** — an emotional, memory-driven agent that cannot explain itself is
   undebuggable and untrustworthy. This is why the Mind Inspector and the
   `/explain` endpoint are Phase-3 features, not Phase-9 features.
5. **Responsiveness** — first token under 2 s on target hardware; background work never
   starves the conversation.
6. **Believability** — the companion quality. It is fifth, not first, because it is an
   emergent product of the others.
7. **Throughput** — near-irrelevant. One user, a handful of concurrent operations.

---

## 7. Target environment

| Dimension | Assumption |
|---|---|
| Host | Single developer machine: Apple Silicon Mac (16 GB+) or Linux with a consumer GPU |
| Runtime | Python 3.12+, Node 20+ for the frontend |
| Models | Ollama-served local models; a 7–14 B class conversational model and a small (1–3 B) utility model |
| Storage | Single SQLite database file plus a blob directory; expected growth ~1–3 GB/year of active use |
| Network | Assumed absent by default. Available intermittently for the Live Internet knowledge tier |
| Uptime | Long-running background process, expected to survive sleep/wake and to be killed without ceremony |

The stale `.venv` in this repository is Python 3.9. It cannot run the intended stack and
should be deleted; see [22-roadmap.md](22-roadmap.md) §Phase 0.

---

## 8. Glossary

Used consistently across all documents. Deviating in code is a review defect.

| Term | Meaning |
|---|---|
| **Turn** | One user input and the system's complete response to it, including background effects. |
| **Session** | A bounded sequence of turns, closed by explicit end or inactivity timeout. |
| **Episode** | A durable record of something that happened, with a time range. The unit of episodic memory. |
| **Belief** | A durable proposition HEDWIG holds, with confidence and provenance. The unit of semantic memory. |
| **Entity** | A person, place, topic, project, or artefact that episodes and beliefs refer to. |
| **Salience** | A memory's current importance score; drives retrieval and survival. |
| **Appraisal** | The evaluation of an event along emotional dimensions, producing state deltas. |
| **Trait** | A slowly-changing personality parameter. |
| **Drive** | A fast-changing emotional/motivational parameter (curiosity, energy, stress). |
| **Gap** | A recorded knowledge deficiency that the curiosity engine may act on. |
| **Finding** | Unverified information produced by exploration, not yet promoted to a belief. |
| **Mind State** | The durable, non-memory cognitive state: emotion, personality, goals, relationships. |
| **Working Set** | The bounded collection of retrieved material assembled for one turn. Ephemeral. |
| **Expression Frame** | A renderer-independent description of how the avatar should look right now. |
| **Trust tier** | The provenance class of a piece of content, determining whether it may be treated as instruction. |

---

## 9. Future improvements

| Deferred | Trigger to reconsider |
|---|---|
| Multi-user support | A second human actually wants to use one install. |
| Voice-first interaction | Text loop is stable and latency budget has headroom. |
| Fine-tuning on accumulated memory | ≥6 months of real data exists and retrieval quality has plateaued. |
| Mobile or remote client | The WebSocket protocol has been stable for a full phase. |
| Federated/multi-device memory sync | Only after the conflict-resolution design in [23](23-challenged-assumptions.md) §12 is solved. |
