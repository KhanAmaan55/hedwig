"""The seven guardrails (docs/10 §5.3).

The central engineering problem of an adaptive personality is not representing traits — it is
controlling drift. These tests are the brakes, one rule at a time, then the adversarial
sequences someone would actually try: many tiny proposals, alternating directions, evidence
forged by repetition, and a whole simulated year of pressure.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from hedwig.core.ports.personality import DriftOutcome, Evidence, Trait, TraitState
from hedwig.personality.drift import RecentChange, Verdict, evaluate
from hedwig.personality.traits import DriftLimits

NOW = datetime(2026, 6, 15, 12, tzinfo=UTC)
LIMITS = DriftLimits()


def _state(
    value: float = 0.5, *, lifetime: float = 0.0, floor: float = 0.1, ceiling: float = 0.9
) -> TraitState:
    return TraitState(
        trait=Trait.VERBOSITY,
        value=value,
        anchor=0.5,
        floor=floor,
        ceiling=ceiling,
        lifetime_drift=lifetime,
    )


def _evidence(count: int = 3, *, days: int = 2, weight: float = 0.5) -> list[Evidence]:
    return [
        Evidence(
            memory_id=f"ep_{index}",
            weight=weight,
            observed_at=NOW - timedelta(days=index % days),
        )
        for index in range(count)
    ]


def _evaluate(delta: float = 0.02, **overrides: object) -> Verdict:
    args: dict[str, object] = {
        "trait": "verbosity",
        "delta": delta,
        "state": _state(),
        "recent": [],
        "evidence": _evidence(),
        "limits": LIMITS,
        "now": NOW,
    }
    args.update(overrides)
    return evaluate(**args)  # type: ignore[arg-type]


# =========================================================================
# A proposal that should pass
# =========================================================================


def test_a_well_evidenced_small_proposal_applies_in_full() -> None:
    verdict = _evaluate(0.02)

    assert verdict.outcome is DriftOutcome.APPLIED
    assert verdict.applied == pytest.approx(0.02)


# =========================================================================
# Rule 1 — the identity core is untouchable
# =========================================================================


@pytest.mark.parametrize("key", ["values", "prohibitions", "self_description", "name", "pronouns"])
def test_the_identity_core_cannot_be_drifted(key: str) -> None:
    """Traits are style and may drift; the core is character and may not (docs/10 §3.2)."""
    verdict = _evaluate(trait=key, evidence=_evidence(10, days=5, weight=1.0))

    assert verdict.outcome is DriftOutcome.REJECTED
    assert verdict.rule == "1_identity_core"


def test_an_invented_trait_is_rejected() -> None:
    verdict = _evaluate(trait="obsequiousness", state=None)

    assert verdict.rule == "1_unknown_trait"


# =========================================================================
# Rule 2 — evidence, from more than one day
# =========================================================================


def test_two_pieces_of_evidence_are_not_enough() -> None:
    assert _evaluate(evidence=_evidence(2, weight=1.0)).rule == "2_evidence"


def test_light_evidence_is_not_enough_however_much_of_it() -> None:
    """Three faint signals sum to 0.6, below the 1.2 floor."""
    assert _evaluate(evidence=_evidence(3, weight=0.2)).rule == "2_evidence"


def test_one_bad_evening_is_not_a_trend() -> None:
    """Three heavy complaints on the same day are one bad evening (docs/10 §5.2)."""
    same_day = _evidence(5, days=1, weight=1.0)

    assert _evaluate(evidence=same_day).rule == "2_evidence"


def test_evidence_cannot_be_forged_by_repetition() -> None:
    """The same memory cited five times is one piece of evidence."""
    one = Evidence(memory_id="ep_1", weight=1.0, observed_at=NOW)
    other = Evidence(memory_id="ep_2", weight=1.0, observed_at=NOW - timedelta(days=1))
    forged = [one, one, one, other, other]

    assert _evaluate(evidence=forged).rule == "2_evidence"


# =========================================================================
# Rule 3 — no single jump is noticeable
# =========================================================================


def test_a_large_proposal_is_clamped_to_the_per_proposal_cap() -> None:
    verdict = _evaluate(0.5)

    assert verdict.outcome is DriftOutcome.PARTIALLY_APPLIED
    assert verdict.applied == pytest.approx(0.02)  # then the weekly budget
    assert verdict.rule == "3_per_proposal_cap"


def test_the_cap_is_symmetric() -> None:
    assert _evaluate(-0.5).applied == pytest.approx(-0.02)


# =========================================================================
# Rule 4 — many small proposals cannot add up to a lurch
# =========================================================================


def test_the_weekly_budget_clamps_the_remainder() -> None:
    recent = [RecentChange(applied_at=NOW - timedelta(days=2), delta=0.015)]
    verdict = _evaluate(0.02, recent=recent)

    assert verdict.applied == pytest.approx(0.005)
    assert verdict.rule == "4_weekly_budget"


def test_a_spent_week_rejects() -> None:
    recent = [RecentChange(applied_at=NOW - timedelta(days=1), delta=0.02)]

    assert _evaluate(0.01, recent=recent).rule == "4_weekly_budget"


def test_the_budget_renews_after_a_week() -> None:
    recent = [RecentChange(applied_at=NOW - timedelta(days=8), delta=0.02)]

    assert _evaluate(0.02, recent=recent).outcome is DriftOutcome.APPLIED


def test_a_change_exactly_a_week_ago_belongs_to_last_week() -> None:
    """Regression: with an inclusive window, a weekly reflection run at the same time each week
    landed on the boundary every time and got half the documented rate."""
    recent = [RecentChange(applied_at=NOW - timedelta(days=7), delta=0.02)]

    assert _evaluate(0.02, recent=recent).outcome is DriftOutcome.APPLIED


def test_an_instruction_still_spends_the_week() -> None:
    """Instructions bypass rule 4 but are counted by it, so inferred drift cannot pile onto a
    fresh instruction and produce a lurch (docs/27 §4.2)."""
    recent = [RecentChange(applied_at=NOW - timedelta(hours=1), delta=0.06)]

    assert _evaluate(0.02, recent=recent).rule == "4_weekly_budget"


# =========================================================================
# Rule 5 — no degenerate personalities
# =========================================================================


def test_a_trait_cannot_cross_its_ceiling() -> None:
    verdict = _evaluate(0.02, state=_state(0.89, ceiling=0.9))

    assert verdict.applied == pytest.approx(0.01)
    assert verdict.rule == "5_bounds"


def test_a_trait_already_at_its_floor_cannot_go_lower() -> None:
    assert _evaluate(-0.02, state=_state(0.1, floor=0.1)).rule == "5_bounds"


# =========================================================================
# Rule 6 — not a different entity in a year
# =========================================================================


def test_the_lifetime_cap_clamps_the_last_stretch() -> None:
    verdict = _evaluate(0.02, state=_state(lifetime=0.24))

    assert verdict.applied == pytest.approx(0.01)
    assert verdict.rule == "6_lifetime_cap"


def test_a_spent_lifetime_rejects_even_an_instruction() -> None:
    """The fast path is still bounded by rule 6 (docs/10 §5.4)."""
    verdict = _evaluate(-0.06, state=_state(lifetime=0.25), instruction=True, evidence=[])

    assert verdict.rule == "6_lifetime_cap"


# =========================================================================
# Rule 7 — the sycophancy brake
# =========================================================================


def test_reversing_a_recent_change_is_refused() -> None:
    """Terse on Monday, chatty on Friday must not drag a trait back and forth forever."""
    recent = [RecentChange(applied_at=NOW - timedelta(days=10), delta=-0.02)]

    assert _evaluate(0.02, recent=recent).rule == "7_oscillation"


def test_continuing_in_the_same_direction_is_fine() -> None:
    recent = [RecentChange(applied_at=NOW - timedelta(days=10), delta=0.01)]

    assert _evaluate(0.01, recent=recent).outcome is DriftOutcome.APPLIED


def test_a_reversal_after_fourteen_days_is_a_trend_not_a_thrash() -> None:
    recent = [RecentChange(applied_at=NOW - timedelta(days=15), delta=-0.02)]

    assert _evaluate(0.02, recent=recent).outcome is DriftOutcome.APPLIED


def test_an_explicit_instruction_may_reverse_a_recent_change() -> None:
    """The user asked. Rule 7 brakes *inferred* drift, not a direct request."""
    recent = [RecentChange(applied_at=NOW - timedelta(days=1), delta=-0.06)]
    verdict = _evaluate(0.06, recent=recent, instruction=True, evidence=[])

    assert verdict.outcome is DriftOutcome.APPLIED


def test_the_rule_recorded_is_the_first_one_in_the_documented_order() -> None:
    """Spent budget *and* a reversal: rule 4 comes first in docs/10 §5.3, so rule 4 is named."""
    recent = [RecentChange(applied_at=NOW - timedelta(days=1), delta=-0.02)]

    assert _evaluate(0.02, recent=recent).rule == "4_weekly_budget"


# =========================================================================
# The instruction fast path
# =========================================================================


def test_an_instruction_skips_the_evidence_and_the_small_caps() -> None:
    verdict = _evaluate(-0.06, instruction=True, evidence=[])

    assert verdict.outcome is DriftOutcome.APPLIED
    assert verdict.applied == pytest.approx(-0.06)


def test_a_zero_change_is_not_a_change() -> None:
    assert _evaluate(0.0).outcome is DriftOutcome.REJECTED


# =========================================================================
# Adversarial sequences
# =========================================================================


def test_a_flood_of_tiny_proposals_cannot_beat_the_weekly_budget() -> None:
    """A thousand 0.001 nudges in a week move a trait by the weekly budget, no more."""
    recent: list[RecentChange] = []
    moved = 0.0
    for minute in range(1000):
        at = NOW + timedelta(minutes=minute)
        verdict = evaluate(
            trait="verbosity",
            delta=0.001,
            state=_state(0.5 + moved),
            recent=recent,
            evidence=_evidence(),
            limits=LIMITS,
            now=at,
        )
        if verdict.applied:
            recent.append(RecentChange(applied_at=at, delta=verdict.applied))
            moved += verdict.applied

    assert moved == pytest.approx(LIMITS.weekly_budget)


def test_alternating_pressure_produces_no_net_movement_within_the_window() -> None:
    """Up, then down, then up: after the first, every reversal is refused."""
    recent = [RecentChange(applied_at=NOW - timedelta(days=3), delta=0.02)]
    down = _evaluate(-0.02, recent=recent)

    assert down.outcome is DriftOutcome.REJECTED


def test_rejection_never_moves_anything() -> None:
    for verdict in (
        _evaluate(trait="values"),
        _evaluate(evidence=[]),
        _evaluate(0.02, recent=[RecentChange(applied_at=NOW, delta=0.02)]),
    ):
        assert verdict.applied == 0.0
