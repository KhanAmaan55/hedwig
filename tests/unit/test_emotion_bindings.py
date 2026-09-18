"""State to behaviour (docs/09 §6).

The rule under test is INV-3: **every dimension must change at least one measurable
behaviour.** A dimension nothing reads is decoration, and decoration invites the user to
believe something false.

The prohibition tests at the bottom are the other half of that bargain. Emotion is allowed
to change how HEDWIG speaks; it is not allowed to change what is true, what is safe, or what
is refused — and those are asserted at both extremes rather than trusted.
"""

from __future__ import annotations

import pytest

from hedwig.core.ports.brain import TurnPolicy
from hedwig.core.ports.emotion import DIMENSIONS, Dimension, EmotionState
from hedwig.emotion.bindings import behaviour, style_directives, turn_policy

FLAT = EmotionState()


def _at(dimension: Dimension, value: float) -> EmotionState:
    return EmotionState().with_dimension(dimension, value)


def _extremes() -> list[EmotionState]:
    states = [EmotionState()]
    for dimension in DIMENSIONS:
        states.append(_at(dimension, 0.0))
        states.append(_at(dimension, 1.0))
    states.append(
        EmotionState(
            happiness=1.0, trust=1.0, curiosity=1.0, confidence=1.0, energy=1.0, stress=0.0
        )
    )
    states.append(
        EmotionState(
            happiness=0.0, trust=0.25, curiosity=0.0, confidence=0.0, energy=0.0, stress=1.0
        )
    )
    return states


# =========================================================================
# INV-3: every dimension does something
# =========================================================================


def test_every_dimension_changes_behaviour() -> None:
    """The check that keeps the vector honest. If this fails, delete a dimension."""
    for dimension in DIMENSIONS:
        low = behaviour(_at(dimension, 0.0))
        high = behaviour(_at(dimension, 1.0))
        assert low != high, f"{dimension.value} changes nothing and should not exist"


def test_curiosity_widens_retrieval_diversity() -> None:
    assert behaviour(_at(Dimension.CURIOSITY, 0.0)).diversity == pytest.approx(0.2)
    assert behaviour(_at(Dimension.CURIOSITY, 1.0)).diversity == pytest.approx(0.7)


def test_stress_shortens_replies_by_up_to_forty_percent() -> None:
    assert behaviour(_at(Dimension.STRESS, 0.0)).max_tokens_multiplier == pytest.approx(1.0)
    assert behaviour(_at(Dimension.STRESS, 1.0)).max_tokens_multiplier == pytest.approx(0.6)


def test_low_confidence_buys_more_evidence_rather_than_more_hedging() -> None:
    """docs/09 §6: uncertainty should send HEDWIG to look, not just to qualify."""
    assert behaviour(_at(Dimension.CONFIDENCE, 0.2)).token_budget_multiplier == pytest.approx(1.3)
    assert behaviour(_at(Dimension.CONFIDENCE, 0.9)).token_budget_multiplier == pytest.approx(1.0)


def test_exhaustion_closes_the_door_on_background_work() -> None:
    assert behaviour(_at(Dimension.ENERGY, 0.1)).admits_background_work is False
    assert behaviour(_at(Dimension.ENERGY, 0.9)).admits_background_work is True


# =========================================================================
# Style directives
# =========================================================================


def test_directives_are_instructions_not_descriptions() -> None:
    """A model handed "you are feeling stressed" narrates a mood at the user, which docs/09
    §9 lists as a failure mode by name. Directives say what to *do*."""
    banned = ("you are feeling", "you feel", "your mood", "i am feeling", "i feel")
    for state in _extremes():
        for directive in style_directives(state):
            lowered = directive.lower()
            assert not any(phrase in lowered for phrase in banned), directive


def test_high_curiosity_invites_a_follow_up_question() -> None:
    assert any("follow-up" in d for d in style_directives(_at(Dimension.CURIOSITY, 0.9)))


def test_confidence_switches_between_asserting_and_admitting_doubt() -> None:
    assertive = style_directives(_at(Dimension.CONFIDENCE, 0.9))
    hedging = style_directives(_at(Dimension.CONFIDENCE, 0.2))

    assert any("directly" in d for d in assertive)
    assert any("unsure" in d for d in hedging)
    assert assertive != hedging


def test_low_trust_prefers_asking_over_assuming() -> None:
    guarded = style_directives(_at(Dimension.TRUST, 0.26))
    familiar = style_directives(_at(Dimension.TRUST, 0.9))

    assert any("asking over assuming" in d for d in guarded)
    assert any("personally" in d for d in familiar)


def test_a_flat_mood_says_almost_nothing() -> None:
    """Emotion modulates; it does not editorialise. A neutral state should not arrive with
    a paragraph of instructions attached."""
    assert len(style_directives(FLAT)) <= 2


# =========================================================================
# Projection onto the turn policy
# =========================================================================


def test_a_flat_mood_reproduces_the_configured_defaults() -> None:
    """The property that makes emotion safe to switch off: a mood at baseline behaves
    exactly like no emotion engine at all."""
    base = TurnPolicy(token_budget=3000, max_tokens=1024, diversity=0.3)
    projected = turn_policy(EmotionState(curiosity=0.2, stress=0.0, confidence=0.6), base=base)

    assert projected.token_budget == base.token_budget
    assert projected.max_tokens == base.max_tokens


def test_the_policy_scales_the_base_rather_than_replacing_it() -> None:
    small = turn_policy(_at(Dimension.STRESS, 0.5), base=TurnPolicy(max_tokens=100))
    large = turn_policy(_at(Dimension.STRESS, 0.5), base=TurnPolicy(max_tokens=1000))

    assert small.max_tokens * 10 == pytest.approx(large.max_tokens, abs=1)


def test_max_tokens_never_reaches_zero() -> None:
    """A reply of length zero is not a short reply, it is a broken one."""
    for state in _extremes():
        assert turn_policy(state, base=TurnPolicy(max_tokens=1)).max_tokens >= 1


def test_diversity_stays_inside_the_mmr_range() -> None:
    for state in _extremes():
        assert 0.0 <= turn_policy(state).diversity <= 1.0


def test_token_budget_stays_positive() -> None:
    for state in _extremes():
        assert turn_policy(state).token_budget > 0


# =========================================================================
# The hard prohibitions (docs/09 §6.1)
# =========================================================================


def test_no_mood_can_turn_tools_on_or_off() -> None:
    """Approval requirements are policy, never mood. A confident HEDWIG does not get to
    skip approval, and a stressed one does not get to stop using tools."""
    for state in _extremes():
        assert turn_policy(state, base=TurnPolicy(allow_tools=True)).allow_tools is True
        assert turn_policy(state, base=TurnPolicy(allow_tools=False)).allow_tools is False


def test_no_mood_changes_the_sampling_temperature() -> None:
    """Temperature is the closest thing in the policy to "how true the answer is". Style may
    vary with mood; correctness may not."""
    for state in _extremes():
        assert turn_policy(state, base=TurnPolicy(temperature=0.4)).temperature == 0.4


def test_bindings_are_a_pure_function_of_the_state() -> None:
    for state in _extremes():
        assert behaviour(state) == behaviour(state)
