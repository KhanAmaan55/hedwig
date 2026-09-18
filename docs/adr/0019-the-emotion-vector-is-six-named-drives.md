# ADR-0019 — The emotion vector is six named drives, and trust is one of them

**Status:** Accepted
**Date:** 2026-09-17
**Affects:** docs/09, docs/05 §5.4, docs/16 §4

## Context

docs/09 §3 specifies eight dimensions: `valence`, `arousal`, `curiosity`, `happiness`,
`confidence`, `stress`, `energy`, `warmth`. That was a design-phase decision with two parts,
both of which are now being revisited against an explicit requirement for
**happiness, trust, curiosity, confidence, energy, stress** — which is the README's original
list.

docs/09 §3.1 removed `trust` for a reason worth restating rather than waving away:

> Trust toward an entity is a slow, evidence-accumulated *relationship* property […]
> Modelling it as an emotion would let a single bad turn erase a year of relationship, and
> would let it decay back to baseline overnight — both wrong, and in a companion, actively
> hurtful.

That objection is about **dynamics**, not about the name. It applies to any dimension given
a 20-minute half-life and a ±0.15 per-tick swing. It does not apply to a dimension whose
dynamics are built so those two things cannot happen.

The second part — adding `valence` and `arousal` — was justified entirely by the avatar:
"six named drives with no underlying affective core means the avatar and the prose have to
guess at overall tone". The avatar does not exist and is several milestones away.

## Decision

**The state vector is the six requested dimensions**: `happiness`, `trust`, `curiosity`,
`confidence`, `energy`, `stress`.

`valence`, `arousal` and `warmth` are dropped. Core affect can be *derived* from the six
when something needs it (`valence` is a weighted combination of happiness, trust, confidence
and stress; `arousal` of energy, stress and curiosity), so nothing is lost that cannot be
recovered as a computed property — and a derived value cannot drift out of sync with the
state it summarises, which a stored one can.

`trust` is admitted with four constraints that answer §3.1 directly:

| Constraint | Value | What it prevents |
|---|---|---|
| Half-life | 7 days | Decaying back to baseline overnight |
| Floor | 0.25, below which emotion dynamics cannot push it | A bad day erasing accumulated trust |
| Per-tick cap | 0.05, a third of every other dimension | A single turn moving it perceptibly |
| Scope | *interaction* trust, not per-entity relationship affinity | Conflation with the relationship ledger |

With those, a run of hostile turns moves trust by hundredths and cannot take it below the
floor; a week of silence leaves it roughly where it was. That is a slow mood, not a
relationship ledger — which is what makes it safe to carry here.

**Durable, per-entity trust is still relationship memory** (docs/06 §3.3) and is still
unimplemented. This dimension is not it, and must not be used as it.

## Consequences

The mapping matrix loses three columns and gains one; `trust` inherits the social row that
`warmth` carried, since a warm interaction is the observable that moves both.

`emotion_state` in docs/05 §5.4 changes shape. It is a single guarded row with no history
dependency and it has never been written, so this is a schema definition rather than a
migration of live data.

The avatar, when it lands, reads derived valence/arousal instead of stored ones. If that
proves insufficient in practice, adding them back is additive — two columns and two rows in
the matrix — and this ADR should be superseded rather than quietly ignored.

## Alternatives considered

* **Keep eight dimensions and add trust, making nine.** Rejected: `warmth` and `trust` would
  respond to the same appraisal signal (`social_valence`) with no distinct binding between
  them, and a dimension nothing separately reads is deleted by INV-3.
* **Keep trust out, and name `warmth` "trust" in the API.** Rejected as dishonest. If the
  vector does not model trust, it should not claim to.
* **Model trust as a relationship row now.** Correct eventually, and a larger piece of work
  than this one: it needs per-entity identity, evidence accumulation and decay semantics of
  its own. Deferring it is why the scope constraint above is written down.

## Revisit if

The avatar needs a continuous mood axis that the derived valence does not provide, or
relationship memory lands — at which point the *scope* line above becomes enforceable
rather than advisory, and `trust` here should probably be renamed to make the distinction
obvious at the call site.
