# ADR-0021 — VRM first, with the semantic seam kept

**Status:** Accepted
**Date:** 2026-10-29
**Affects:** docs/15 §7.1, docs/18 §6, docs/28

## Context

docs/15 §7.1 phases the avatar 2D first, VRM later, and argues for it strongly:

> Starting 3D would be the classic mistake: months of rigging work before discovering the
> frame protocol has the wrong fields. 2D first is not a compromise, it is how you find out.

The requirement for Milestone 10 is a VRM avatar via three.js — the 3D renderer, first.

The argument in §7.1 is about one specific risk: that the *protocol between expression and
renderer* gets shaped by the first renderer's capabilities, and turns out wrong for every other
one. It is not an argument that 3D is harder to get looking right — that is true, but it is a
cost, not an architectural risk.

## Decision

**Build the VRM renderer first, and keep the seam §7.1 was protecting.**

* The renderer receives **semantic expression intent** — `mood {valence, arousal}`, a named
  `overlay {interest, warmth, amusement, uncertainty, fatigue}` blend, gaze target, energy —
  never emotion dimensions and never blendshape weights. This is docs/15 §3–4's
  `ExpressionFrame`, minus the fields this milestone cannot produce.
* The **resolver lives in the backend** as a pure function (docs/15 §5), so a 2D or headless
  renderer later consumes exactly what the VRM one does.
* The mapping from intent to VRM expressions and bones is **data** — a character profile
  (docs/15 §7) — not code in the renderer.

If those three hold, a 2D renderer added later is a second consumer of an existing protocol,
and the risk §7.1 named does not arise: the protocol's fields come from the resolver's
vocabulary in docs/15, not from what VRM happens to support.

## Consequences

The protocol is validated against the harder renderer first. If VRM *can't* express something
in the vocabulary, the profile marks it unsupported rather than the vocabulary bending to fit.

The frontend gains three.js and three-vrm (~700 KB). The avatar pane is lazy-loaded, so the
rest of the UI never pays for it.

No 2D fallback exists yet. docs/18 §6 specifies one for clients without WebGL; this milestone
shows an honest message instead, and the fallback is the first follow-up.

## Alternatives considered

* **2D first, as documented.** Rejected because the requirement is explicit, and because §7.1's
  real concern is handled by keeping the seam rather than by renderer order.
* **Feed raw emotion to the renderer.** The quickest path to a moving face, and exactly what
  docs/15 §7 and docs/18 §6 ("the renderer never reads the mind store") forbid: every
  renderer would then reinvent the resolver.

## Revisit if

The 2D renderer, when built, needs a field the resolver does not produce — that would mean
the vocabulary was shaped by VRM after all.
