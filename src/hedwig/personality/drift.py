"""The seven guardrails (docs/10 §5.3), as a pure function.

The central engineering problem of an adaptive personality is not representing traits, it is
**controlling drift**. Without brakes it converges on one of two failure states: a sycophantic
mirror of the user, or an unrecognisable stranger. This module is the brakes.

Pure: given a proposal, the trait's state and its recent history, the decision is fixed. No
clock read, no I/O — so every rule, and every adversarial sequence against them, is testable
as arithmetic.

| # | Rule | On violation | Prevents |
|---|---|---|---|
| 1 | Trait exists; identity core untouchable | reject | Identity erosion |
| 2 | Evidence: >=3 items, weight >=1.2, >=2 days | reject | Reacting to one bad conversation |
| 3 | Per-proposal cap | clamp | Any single jump being noticeable |
| 4 | Weekly budget | clamp to what remains | Many small proposals adding up to a lurch |
| 5 | Floor / ceiling | clamp | Degenerate personalities |
| 6 | Lifetime cap | clamp to what remains | Becoming a different entity over years |
| 7 | No reversal within 14 days | reject | Thrashing; sycophantic mirroring |

Rules 4 and 6 clamp rather than reject when *some* budget remains: `partially_applied` exists
in the schema for exactly this, and rejecting outright would make the last stretch of every
budget unreachable (docs/27 §4.2).

Explicit instructions take a fast path through rules 1, 5 and 6 only (docs/10 §5.4).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from hedwig.core.ports.personality import DriftOutcome, Evidence, Trait, TraitState
from hedwig.personality.traits import EPSILON, DriftLimits

IDENTITY_KEYS = frozenset({"name", "pronouns", "self_description", "values", "prohibitions"})


@dataclass(frozen=True, slots=True)
class RecentChange:
    """One applied, unreverted change, as the budget and oscillation rules see it."""

    applied_at: datetime
    delta: float


@dataclass(frozen=True, slots=True)
class Verdict:
    outcome: DriftOutcome
    applied: float
    rule: str | None = None
    """The rule that rejected the proposal, or the first one that clamped it."""


def evaluate(
    *,
    trait: str,
    delta: float,
    state: TraitState | None,
    recent: Sequence[RecentChange],
    evidence: Sequence[Evidence],
    limits: DriftLimits,
    now: datetime,
    instruction: bool = False,
) -> Verdict:
    """Decide what a proposal is allowed to do."""
    # -- 1 · the trait exists, and is not the identity core ---------------------------
    if trait in IDENTITY_KEYS:
        return _reject("1_identity_core")
    if state is None or trait not in {t.value for t in Trait}:
        return _reject("1_unknown_trait")
    if abs(delta) < EPSILON:
        return _reject("0_no_change")

    clamped_by: str | None = None
    change = delta

    if not instruction:
        # -- 2 · enough evidence, from more than one day ------------------------------
        if not _sufficient(evidence, limits):
            return _reject("2_evidence")

        # -- 3 · per-proposal cap ------------------------------------------------------
        if abs(change) > limits.per_proposal_cap:
            change = _sign(change) * limits.per_proposal_cap
            clamped_by = clamped_by or "3_per_proposal_cap"

        # -- 4 · weekly budget, counting *every* applied change, instructions included ---
        # Half-open: a change made exactly a week ago belongs to *last* week. With `<=`, a
        # weekly reflection run at the same time each week would land on the boundary every
        # time and get half the documented rate — found by the twelve-week test.
        spent = sum(abs(c.delta) for c in recent if now - c.applied_at < limits.budget_window)
        remaining = limits.weekly_budget - spent
        if remaining <= EPSILON:
            return _reject("4_weekly_budget")
        if abs(change) > remaining:
            change = _sign(change) * remaining
            clamped_by = clamped_by or "4_weekly_budget"

    # -- 5 · floor and ceiling --------------------------------------------------------
    target = state.value + change
    bounded = max(state.floor, min(state.ceiling, target))
    if abs(bounded - target) > EPSILON:
        change = bounded - state.value
        clamped_by = clamped_by or "5_bounds"
    if abs(change) < EPSILON:
        return _reject("5_bounds")

    # -- 6 · lifetime cap on cumulative movement ----------------------------------------
    lifetime_remaining = limits.lifetime_cap - state.lifetime_drift
    if lifetime_remaining <= EPSILON:
        return _reject("6_lifetime_cap")
    if abs(change) > lifetime_remaining:
        change = _sign(change) * lifetime_remaining
        clamped_by = clamped_by or "6_lifetime_cap"

    # -- 7 · no reversal of a recent change. Last, in the documented order, so the rule
    #    recorded is the first one in docs/10 §5.3 that fired. Clamping above never changes
    #    the sign, so checking it here rather than first changes no decision.
    if not instruction and _reverses_recent(change, recent, limits, now):
        return _reject("7_oscillation")

    outcome = DriftOutcome.APPLIED if clamped_by is None else DriftOutcome.PARTIALLY_APPLIED
    return Verdict(outcome=outcome, applied=round(change, 9), rule=clamped_by)


def _sufficient(evidence: Sequence[Evidence], limits: DriftLimits) -> bool:
    """>=3 items, total weight >=1.2, spanning >=2 distinct days.

    Distinct *days*, because three complaints in one bad evening are one bad evening.
    Duplicate memory ids count once, so evidence cannot be forged by repetition.
    """
    unique = {item.memory_id: item for item in evidence}.values()
    if len(unique) < limits.min_evidence_items:
        return False
    if sum(item.weight for item in unique) < limits.min_evidence_weight - EPSILON:
        return False
    days = {item.observed_at.date() for item in unique}
    return len(days) >= limits.min_evidence_days


def _reverses_recent(
    delta: float, recent: Sequence[RecentChange], limits: DriftLimits, now: datetime
) -> bool:
    """The sycophancy brake (docs/10 §5.3 rule 7).

    A user who is terse on Monday and chatty on Friday would otherwise drag a trait back and
    forth forever, producing a personality with no character at all. Refusing to reverse
    within the window forces drift to reflect a *trend*.
    """
    return any(
        now - change.applied_at < limits.oscillation_window and _sign(change.delta) == -_sign(delta)
        for change in recent
    )


def _sign(value: float) -> float:
    return 1.0 if value > 0 else -1.0


def _reject(rule: str) -> Verdict:
    return Verdict(outcome=DriftOutcome.REJECTED, applied=0.0, rule=rule)
