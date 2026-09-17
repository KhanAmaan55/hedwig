# 23 — Challenged Assumptions and Open Questions

**Status:** Design · **Depends on:** all documents

---

## 1. Purpose

`CLAUDE.md` asks the architect to challenge assumptions where necessary. This document collects
every place the design departs from the README, every premise that looks solid and is not, and
every question the design cannot answer yet.

Two sections: **corrections already made** (§2–§9, each folded into the relevant subsystem
document) and **open questions** (§10–§18, unresolved and needing a decision or evidence).

---

## 2. "Long-term / short-term / working memory" are not three stores

**README:** lists long-term, short-term, working, semantic and relationship memory as five
memory types.

**Problem:** three of the five are not stores. "Long-term" is a property every store has;
"short-term" is a query (`ORDER BY seq DESC LIMIT 8`); "working" is a per-turn projection.
Building five stores means three synchronisation problems for no capability. Meanwhile the two
most important stores are missing: **episodic** (the record of what happened, without which
semantic memory has no provenance and reflection has no input) and **procedural** (learned
interaction preferences, which is what makes long-term adaptation *felt* rather than merely
known).

**Resolution:** four durable stores — episodic, semantic, social, procedural — plus two derived
views. Fewer stores, more capability. [06](06-memory-architecture.md) §2.

---

## 3. Trust is not an emotion

**README:** lists trust among the emotional states.

**Problem:** trust is per-entity, slow, and accumulated from evidence. Emotions in this design
are global and fast, with half-lives in minutes to hours, decaying to a baseline. Modelling
trust as an emotion means one bad conversation can erase a year of relationship, and an
overnight decay restores it — both wrong, and in a companion, actively hurtful.

**Resolution:** trust moves to relationship memory alongside familiarity and affinity, with a
deliberately slow learning rate. The emotion vector gets `warmth` for the transient social
feeling. [09](09-emotion-engine.md) §3.1, [06](06-memory-architecture.md) §3.3.

---

## 4. Six named drives are not enough substrate for expression

**README:** curiosity, happiness, trust, confidence, stress, energy.

**Problem:** with no underlying affective core, the avatar and the prose have to infer overall
tone from six semi-independent numbers, and every renderer has to reimplement that inference.

**Resolution:** add `valence` and `arousal` (the circumplex model) as the substrate; keep named
drives as overlays. Renderers read core affect and refine with whatever overlays they support.
This *simplifies* the output end while making it richer. [09](09-emotion-engine.md) §3.1,
[15](15-avatar-controller.md) §4.

Also: no anger, fear, sadness or disgust. There is nothing for HEDWIG to be angry at, and a
companion that performs distress at its user is a manipulation vector.
[09](09-emotion-engine.md) §3.2.

---

## 5. "Emotions influence behaviour" needs teeth

**README:** correct in principle, silent on mechanism.

**Problem:** the default outcome of "emotions influence behaviour" is prose about feelings that
changes nothing measurable. That is worse than no emotion model, because it invites the user to
believe something false about the system.

**Resolution:** INV-3 — every dimension must have at least one measurable behaviour binding, and
a test parses the binding table and fails if any dimension is unbound or any binding's parameter
is never read. Plus explicit prohibitions: emotion may not touch factual content, tool safety,
refusals, or honesty. [09](09-emotion-engine.md) §6.

---

## 6. "Traits evolve over time" is the most dangerous sentence in the README

**README:** personality traits evolve.

**Problem:** an adaptive personality with no brakes has exactly two attractors — a sycophantic
mirror of the user, or an unrecognisable stranger. Both are failures, and the first is the more
likely and the more insidious, because it feels like the system working.

**Resolution:** seven sequential guardrails, evidence requirements, a 0.02/week per-trait budget,
a 0.25 lifetime cap from anchor, a 14-day anti-oscillation rule (the sycophancy brake), an
immutable identity core, and full reversibility. Emotion is never admissible as evidence, which
breaks the emotion↔personality feedback loop. [10](10-personality-engine.md) §5.

Worth stating plainly: with seven guardrails, **the more likely failure is that personality
never changes at all.** Hence a metric that treats eight weeks of zero drift as a bug report.

---

## 7. "Curiosity" as described is a security hole and an annoyance

**README:** when idle, discover information, read documentation, monitor AI news.

**Problems:** (a) undirected reading fills memory with noise and drowns retrieval; (b) this is
the only subsystem that ingests adversarial third-party content, making it the largest attack
surface in the system by a wide margin; (c) a companion that reports what it found is a
companion you turn off in week two.

**Resolution:** gap-directed only (nothing is fetched that is not traceable to a recorded gap);
local search before network; hard daily budgets; allowlist with no wildcards; seven-layer
injection defence with capability denial as the strongest layer; findings never become active
beliefs without corroboration; passive delivery by default with an interruption policy and
asymmetric dismissal learning; **disabled by default**.
[11](11-curiosity-engine.md), [13](13-knowledge-and-tools.md) §6.

---

## 8. "Local-first" collides with the cost of cognition

**README:** local-first, Ollama, models swappable.

**Problem:** the design calls for appraisal per event, extraction per turn, summarisation over
hundreds of items nightly, and assessment per finding. Doing all of that on one 8 B model on a
laptop makes every cognitive operation cost as much as a reply, and nightly reflection would run
for hours and starve the machine.

**Resolution:** two model tiers (a small utility model for the high-volume structured work, the
conversational model only for user-facing prose), rules-first appraisal that avoids the model for
~85 % of events, a 30 s coalescing emotion tick, batch processing, and a resource governor with
a model lock and preemption. [14](14-language-model-gateway.md) §3,
[09](09-emotion-engine.md) §4.2, [17](17-backend-architecture.md) §7.

Residual risk: small models are unreliable at schema adherence. Mitigated by repair + retry +
tier escalation + typed defaults, and monitored by a schema-failure metric with a 2 % threshold.
If that metric misbehaves in practice, the honest options are a larger utility model or
grammar-constrained decoding — not more prompt engineering.

---

## 9. The avatar must not read the model's output

**README:** already says the avatar reflects internal state rather than LLM output — and this is
the single best architectural instinct in the document.

**Elaboration:** the tempting design (inline `[smiles]` markup) would make the face lie when
state and text disagree, sever emotion's most legible behavioural binding, make expressiveness
model-dependent, and leak markup into replies. So expression is a **pure function of state**,
emitted as semantic frames that a renderer maps through a data-only character profile.
[15](15-avatar-controller.md) §2.

---

## 10. Open: is the utility model good enough?

The entire cognitive layer depends on a 3 B model reliably producing valid JSON for appraisal,
capture, deliberation and assessment.

| If it works | If it does not |
|---|---|
| A full cognitive apparatus runs on a laptop | Either a larger utility model (roughly 3× the cost of everything) or grammar-constrained decoding, or fewer structured operations |

**Resolution path:** Phase 3 measures `hedwig_llm_schema_failures_total` per purpose against a
2 % threshold on real usage. This is the first thing to check when Phase 3 lands, and the answer
changes the cost model of the whole project.

---

## 11. Open: how good is retrieval, really?

Phases 1 and 4 are both memory, and both are marked high risk, because the honest position is
that nobody knows how well hybrid retrieval over a personal memory corpus works until there is a
real corpus.

The eval harness (200 labelled memories, recall@5 ≥ 0.80) is a proxy built from synthetic data.
Synthetic corpora are systematically easier than real ones: the queries are written knowing the
answers.

**Resolution path:** build a real labelled corpus from the developer's own first three months of
use (consented, local). Until then, treat the 0.80 gate as a floor, not evidence of quality.
Candidate improvements if it disappoints: a cross-encoder reranker, 2-hop entity graph expansion,
learned salience.

---

## 12. Open: multi-device is unsolved, and the design constrains it

Deferred everywhere, but the shape of the problem should be recorded while the constraints are
fresh.

| Data | Merge difficulty |
|---|---|
| Episodes | Easy — append-only, ULID-ordered |
| Beliefs | **Hard** — supersession chains from two devices can conflict irreconcilably |
| Emotion state | Easy — last-write-wins, it is transient |
| Personality | **Hard** — drift budgets are per-period; two devices could each spend the full weekly budget |
| Goals | Medium — status transitions conflict |
| Vectors | Easy — recomputable |

The honest assessment: SQLite-per-device plus sync would need CRDT-shaped state for beliefs and
personality, which is a redesign of [08](08-state-management.md), not an addition. The plausible
cheap alternative is **one authoritative instance plus thin clients** — which the current API
already supports, and which is probably the right answer. Recorded here so that "just add sync"
is never mistaken for a small feature.

---

## 13. Open: does the user actually want to see all of this?

The Mind Inspector is justified on debuggability and trust grounds, and is ranked above
responsiveness in the quality attributes. But there is a real possibility that seeing the
machinery — decay scores, trait bars, appraisal dimensions — **destroys the companion
experience** by making the seams visible.

Two defensible positions:

| Position | Implication |
|---|---|
| Transparency builds trust; the illusion was never the goal (INV-10 says so) | Keep the inspector prominent |
| Legibility and companionship are in tension; the inspector should be a developer tool behind a flag | Ship it collapsed by default |

**Current decision:** prominent but collapsible, defaulting to collapsed for a new install and
discoverable through citation chips (which are the gentlest possible entry point into the
machinery). Revisit with real usage; this is a genuine open question, not a resolved one.

---

## 14. Open: how much does forgetting need to be visible?

Forgetting is designed to be reversible, capped and logged. But should HEDWIG *tell* the user
when it forgets something?

- Telling the user is honest, and it lets them pin what matters.
- Telling the user is also unsettling in a way that erodes exactly the continuity the project
  exists to create — "I'm losing my memory of March" is not a message a companion should send.

**Current decision:** forgetting is visible in the inspector and in the monthly report
(aggregate counts, and a list of the highest-`base_importance` items dropped), never as an
unprompted message. Weak confidence; revisit after Phase 5.

---

## 15. Open: goals are still underspecified

[08](08-state-management.md) §6 defines a goal model, but the hardest part is unresolved: **when
should HEDWIG generate its own goals, and what stops that from becoming an agenda?**

Current position is conservative: `self_generated` goals may only be about understanding the
user or maintaining the system, never about outcomes in the world; the active set is capped at
12; and the `InitiativeGraph` may only ever speak in service of a *user-stated* goal.

Open: whether that is too conservative to produce anything interesting. Evidence will come from
Phase 7.

---

## 16. Open: what happens after a long absence?

If the user disappears for six months, what should HEDWIG be on their return?

| Option | Problem |
|---|---|
| Unchanged | Ignores that time passed; feels mechanical |
| Decayed familiarity | Punishes the user for living their life |
| Continued reflection over the gap | Produces summaries of nothing; wastes budget |

**Current design:** memory decays normally (with the clock-jump clamp of 7 days' decay per pass,
so nothing catastrophic happens); familiarity decays *very* slowly and never below a floor set by
peak familiarity; reflection has nothing to reflect on and does nothing; the greeting appraisal
gets a novelty and social-valence boost. Untested and probably not quite right.

---

## 17. Open: the ethics of the thing we are building

The design includes an honesty invariant, no engagement metrics, no re-engagement pings, and
easy export. That handles the mechanisms of manipulation we can name.

It does not resolve the underlying question: **a system engineered to be a believable companion
that remembers everything about one person is, by construction, engineered for attachment.**
Removing dark patterns reduces the risk; it does not eliminate it, and no amount of architecture
will.

Recorded, not solved. The concrete commitments are in
[21](21-security-privacy-ethics.md) §6, and the deliberate refusal to build usage-based
"wellbeing" interventions (which would be patronising and surveilling) is part of the position
rather than an omission from it.

---

## 18. Open: minor but real

| Question | Status |
|---|---|
| Is a 30 s emotion tick perceptible in conversation? | Guess: no. Measure in Phase 3 |
| Is 2 %/night the right forgetting cap? | Arbitrary but safe. Tune with real growth data |
| Should summaries be retrievable at the same weight as their sources? | Currently quota-limited to 25 %. No evidence either way |
| Does HyDE query expansion earn its ~120 ms? | Test in the Phase-1 eval harness; it is one flag |
| Does T3 (weekly) earn its own code path, or should it be T2 with a flag? | Listed as a T3 future improvement to collapse it |
| Should the user be able to write directly to semantic memory ("remember that X")? | Probably yes, at `USER` trust. Not designed yet |
| Is `sqlite-vec` fast enough at 10⁶ vectors? | Unknown. The port makes the answer cheap to change |
| ULID + ISO text costs ~2× storage vs integers | Accepted for comprehensibility ([01](01-vision-and-scope.md) §6) |
| Is the 12-directive prompt cap the right number? | Model-dependent. Measure adherence per model in Phase 3 |

---

## 19. The tradeoff register

Every load-bearing tradeoff in one place. Each row's detail is in the linked document.

| # | Tradeoff | Chosen | Cost accepted | Reverse if |
|---|---|---|---|---|
| 1 | Monolith vs. services | Monolith ([02](02-system-architecture.md)) | No isolation or independent scaling | A module needs a different runtime |
| 2 | Modularity mechanism | Ports + events, not processes ([03](03-module-contracts.md)) | Discipline required, enforced by tests | Never |
| 3 | Everything-events vs. command/event split | Split ([04](04-communication-and-event-bus.md)) | Judgement call per interaction | Never |
| 4 | Event sourcing | No; events are audit + short replay ([ADR-0003](24-decision-records.md#adr-0003)) | No free state reconstruction | Never |
| 5 | Store | SQLite for everything ([05](05-data-model-and-database.md)) | Single writer, vector ceiling | Multi-device or 10⁶ vectors |
| 6 | Memory taxonomy | 4 stores + 2 views ([06](06-memory-architecture.md)) | Departs from the README | Never |
| 7 | Capture timing | Async, after the reply | No read-your-own-write in a turn | Users notice |
| 8 | Decay | Nightly batch | 24 h staleness | Retrieval suffers |
| 9 | Orchestration | LangGraph, nodes as adapters ([07](07-brain-langgraph-workflow.md)) | A dependency on a young library | API churn becomes painful |
| 10 | Mind snapshot | One per turn | This turn cannot react to its own appraisal | Users notice |
| 11 | Emotion update | 30 s coalescing tick ([09](09-emotion-engine.md)) | Up to 30 s latency | Perceptible |
| 12 | Emotion mapping | Fixed hand-tuned matrix | Not adaptive | Evidence for a better matrix |
| 13 | Personality drift | Seven guardrails, very slow ([10](10-personality-engine.md)) | Adaptation may feel absent | Users complain; relax in config |
| 14 | Trait rendering | Deterministic bands | Less nuance than model-written prose | Evals show banding too coarse |
| 15 | Curiosity | Gap-directed, off by default ([11](11-curiosity-engine.md)) | Less serendipity, invisible until enabled | Never for the default |
| 16 | Proactivity | Passive delivery default | Findings may go unseen | Users report missing things |
| 17 | Model tiering | Two tiers ([14](14-language-model-gateway.md)) | Utility-model reliability risk | Schema failures > 2 % |
| 18 | Structured output | Repair + retry + escalate + typed default | Complexity | Grammar-constrained decoding arrives |
| 19 | Avatar | State-driven semantic frames ([15](15-avatar-controller.md)) | Cannot react to reply nuance | Never |
| 20 | Avatar phasing | 2D before 3D | Less impressive early | Never |
| 21 | Frames bypass the bus | Yes ([ADR-0009](24-decision-records.md#adr-0009)) | An exception to the comms rules | Never |
| 22 | Auth | Local bearer token ([16](16-api-structure.md)) | Not exposure-ready without TLS | Multi-user (a non-goal) |
| 23 | Concurrency | Single asyncio loop ([17](17-backend-architecture.md)) | No CPU parallelism | A real CPU-bound component |
| 24 | Frontend | React + Zustand ([18](18-frontend-architecture.md)) | Bundle size | Never (local app) |
| 25 | Inspector prominence | Prominent, collapsed by default | May break immersion | §13 is genuinely open |
| 26 | Telemetry | None, ever ([21](21-security-privacy-ethics.md)) | No usage data | Never |
| 27 | Encryption at rest | Deferred; full-disk encryption recommended | Disk-access exposure | User request or shared machine |
| 28 | Testing | Recorded fixtures + injected clock ([20](20-testing-strategy.md)) | Fixture maintenance | Never |
| 29 | Quality gates | Absolute zero for honesty and injection | Occasional friction | Never |
| 30 | Docs | Architecture tests parse the markdown | Slightly unusual | Parsing becomes fragile |
