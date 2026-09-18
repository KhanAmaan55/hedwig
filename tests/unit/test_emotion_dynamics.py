"""The transition rules, as arithmetic (docs/09 §5).

Pure functions, so these are pure tests: no clock, no database, no bus. Everything the
emotion engine actually *decides* is in here, which is why this file is the densest in the
subsystem.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from hedwig.core.ports.emotion import DIMENSIONS, Dimension, EmotionState
from hedwig.emotion.dynamics import (
    FLOOR,
    HALF_LIFE,
    MAX_DELTA_PER_TICK,
    MAX_ELAPSED,
    Baselines,
    Traits,
    cap_delta,
    circadian_energy,
    clamp,
    decay_toward,
    integrate,
)

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
BASE = Baselines.from_traits()


def _integrate(
    state: EmotionState, deltas: dict[Dimension, float], **kwargs: object
) -> EmotionState:
    return integrate(
        state,
        deltas=deltas,
        baselines=kwargs.get("baselines", BASE),  # type: ignore[arg-type]
        energy_baseline=kwargs.get("energy_baseline", 0.5),  # type: ignore[arg-type]
        elapsed=kwargs.get("elapsed", timedelta(seconds=30)),  # type: ignore[arg-type]
        now=kwargs.get("now", NOW),  # type: ignore[arg-type]
    )


# =========================================================================
# Baselines
# =========================================================================


def test_neutral_traits_give_the_documented_midpoints() -> None:
    """Personality does not exist yet, so every trait reads 0.5 and the baselines are the
    midpoints of their documented expressions rather than invented constants."""
    assert BASE.happiness == pytest.approx(0.55)
    assert BASE.trust == pytest.approx(0.60)
    assert BASE.curiosity == pytest.approx(0.55)
    assert BASE.confidence == pytest.approx(0.60)
    assert BASE.stress == pytest.approx(0.15)


def test_personality_moves_where_normal_is() -> None:
    """docs/09 §5.1 property 3: a curious personality idles curious."""
    curious = Baselines.from_traits(Traits(curiosity=1.0))
    incurious = Baselines.from_traits(Traits(curiosity=0.0))

    assert curious.curiosity > BASE.curiosity > incurious.curiosity


# =========================================================================
# Decay — homeostasis
# =========================================================================


def test_one_half_life_closes_half_the_gap() -> None:
    delta = decay_toward(
        value=0.0, baseline=1.0, elapsed=timedelta(hours=1), half_life=timedelta(hours=1)
    )

    assert delta == pytest.approx(0.5)


def test_decay_pulls_toward_baseline_from_both_directions() -> None:
    above = decay_toward(
        value=0.9, baseline=0.5, elapsed=timedelta(hours=1), half_life=timedelta(hours=1)
    )
    below = decay_toward(
        value=0.1, baseline=0.5, elapsed=timedelta(hours=1), half_life=timedelta(hours=1)
    )

    assert above < 0 < below


def test_no_elapsed_time_is_no_decay() -> None:
    assert (
        decay_toward(value=0.9, baseline=0.1, elapsed=timedelta(0), half_life=timedelta(hours=1))
        == 0.0
    )


def test_a_backwards_clock_stalls_decay_rather_than_reversing_it() -> None:
    assert (
        decay_toward(
            value=0.9, baseline=0.1, elapsed=timedelta(hours=-5), half_life=timedelta(hours=1)
        )
        == 0.0
    )


def test_a_clock_jump_cannot_collapse_the_vector_in_one_tick() -> None:
    """A misconfigured clock must not be able to erase a mood instantly."""
    capped = decay_toward(
        value=0.9, baseline=0.1, elapsed=MAX_ELAPSED, half_life=timedelta(hours=1)
    )
    absurd = decay_toward(
        value=0.9, baseline=0.1, elapsed=timedelta(days=400), half_life=timedelta(hours=1)
    )

    assert absurd == pytest.approx(capped)


def test_every_dimension_converges_to_baseline_without_input() -> None:
    """docs/09 §10: within five half-lives, from either side."""
    state = EmotionState(
        happiness=1.0, trust=1.0, curiosity=0.0, confidence=0.0, energy=1.0, stress=1.0
    )

    # Twelve-hour steps, because MAX_ELAPSED caps what a single tick may resolve: a
    # hundred simulated days is fourteen half-lives even for trust, the slowest dimension.
    for _ in range(200):
        state = _integrate(state, {}, elapsed=MAX_ELAPSED)

    for dimension in DIMENSIONS:
        assert state[dimension] == pytest.approx(BASE.at(dimension, energy=0.5), abs=0.01)


# =========================================================================
# Caps — no whiplash
# =========================================================================


def test_no_dimension_can_move_more_than_its_cap_in_one_tick() -> None:
    state = EmotionState()
    after = _integrate(state, dict.fromkeys(DIMENSIONS, 10.0), elapsed=timedelta(0))

    for dimension in DIMENSIONS:
        moved = after[dimension] - state[dimension]
        assert moved <= MAX_DELTA_PER_TICK[dimension] + 1e-9


def test_trust_moves_at_a_third_the_rate_of_everything_else() -> None:
    """A single turn must not move trust perceptibly (ADR-0019)."""
    assert MAX_DELTA_PER_TICK[Dimension.TRUST] == pytest.approx(0.05)
    for dimension in DIMENSIONS:
        if dimension is not Dimension.TRUST:
            assert MAX_DELTA_PER_TICK[dimension] == pytest.approx(0.15)


def test_decay_cannot_smuggle_a_larger_swing_past_the_cap() -> None:
    """The appraisal delta is capped *before* decay is added, on purpose."""
    state = EmotionState(stress=0.15)
    after = _integrate(state, {Dimension.STRESS: 5.0}, elapsed=timedelta(0))

    assert after.stress - state.stress <= MAX_DELTA_PER_TICK[Dimension.STRESS] + 1e-9


def test_cap_is_symmetric() -> None:
    assert cap_delta(Dimension.STRESS, -9.0) == -MAX_DELTA_PER_TICK[Dimension.STRESS]
    assert cap_delta(Dimension.STRESS, +9.0) == +MAX_DELTA_PER_TICK[Dimension.STRESS]


# =========================================================================
# Floors and ranges
# =========================================================================


def test_nothing_ever_leaves_its_range() -> None:
    state = EmotionState()
    for sign in (+1.0, -1.0):
        driven = state
        for _ in range(200):
            driven = _integrate(
                driven, dict.fromkeys(DIMENSIONS, sign * 10.0), elapsed=timedelta(0)
            )
        for dimension in DIMENSIONS:
            assert FLOOR[dimension] <= driven[dimension] <= 1.0


def test_trust_cannot_be_driven_below_its_floor() -> None:
    """The constraint that makes trust safe to model as a drive (ADR-0019).

    A hundred consecutive maximally-bad ticks — far worse than any real week — still leaves
    trust at the floor rather than at zero.
    """
    state = EmotionState()
    for _ in range(100):
        state = _integrate(state, {Dimension.TRUST: -10.0}, elapsed=timedelta(0))

    assert state.trust == pytest.approx(FLOOR[Dimension.TRUST])
    assert FLOOR[Dimension.TRUST] == pytest.approx(0.25)


def test_only_trust_has_a_floor() -> None:
    """Every other dimension may legitimately reach zero; trust may not."""
    for dimension in DIMENSIONS:
        if dimension is not Dimension.TRUST:
            assert FLOOR[dimension] == 0.0


def test_clamp_is_per_dimension() -> None:
    assert clamp(Dimension.STRESS, -1.0) == 0.0
    assert clamp(Dimension.TRUST, -1.0) == 0.25
    assert clamp(Dimension.HAPPINESS, 5.0) == 1.0


# =========================================================================
# Half-lives
# =========================================================================


def test_trust_outlasts_every_other_dimension_by_an_order_of_magnitude() -> None:
    """Seven days against forty-five minutes to eight hours. This is the number that stops
    trust decaying back to baseline overnight (docs/09 §3.1)."""
    others = [HALF_LIFE[d] for d in DIMENSIONS if d is not Dimension.TRUST]

    assert HALF_LIFE[Dimension.TRUST] == timedelta(days=7)
    assert HALF_LIFE[Dimension.TRUST] > max(others) * 10


def test_stress_is_the_fastest_to_recover() -> None:
    """Pressure should fade within a session; a mood should not."""
    assert HALF_LIFE[Dimension.STRESS] == min(HALF_LIFE.values())


# =========================================================================
# The circadian curve
# =========================================================================


def test_energy_peaks_mid_afternoon_and_troughs_before_dawn() -> None:
    peak = max(range(24), key=lambda h: circadian_energy(NOW.replace(hour=h)))

    assert 14 <= peak <= 16
    assert circadian_energy(NOW.replace(hour=15)) > circadian_energy(NOW.replace(hour=9))
    assert circadian_energy(NOW.replace(hour=9)) > circadian_energy(NOW.replace(hour=3))


def test_the_small_hours_are_a_flat_floor_rather_than_a_point() -> None:
    """The clamp in docs/09 §5.3 flattens the bottom of the sine, so there is no single
    trough hour — 3 am and 1 am are equally low. That is correct: the model should not
    pretend to know that 03:07 is worse than 02:41."""
    overnight = {circadian_energy(NOW.replace(hour=h)) for h in (0, 1, 2, 3, 4, 5, 6)}

    assert overnight == {0.25}


def test_the_circadian_curve_stays_inside_its_documented_clamp() -> None:
    for hour in range(24):
        assert 0.25 <= circadian_energy(NOW.replace(hour=hour)) <= 0.85


def test_the_curve_is_continuous_across_midnight() -> None:
    """A step change at midnight would make energy jump, which is the one thing a circadian
    model must not do."""
    before = circadian_energy(NOW.replace(hour=23, minute=59))
    after = circadian_energy(NOW.replace(hour=0, minute=0))

    assert abs(after - before) < 0.01


def test_energy_follows_the_clock_rather_than_a_personality_baseline() -> None:
    night = _integrate(
        EmotionState(energy=0.5), {}, energy_baseline=0.25, elapsed=timedelta(days=1)
    )
    day = _integrate(EmotionState(energy=0.5), {}, energy_baseline=0.85, elapsed=timedelta(days=1))

    assert night.energy < day.energy


# =========================================================================
# Derived core affect
# =========================================================================


def test_valence_spans_the_full_range() -> None:
    worst = EmotionState(happiness=0.0, trust=0.25, confidence=0.0, stress=1.0)
    best = EmotionState(happiness=1.0, trust=1.0, confidence=1.0, stress=0.0)

    assert worst.valence < 0 < best.valence
    assert worst.valence >= -1.0 and best.valence <= 1.0


def test_stress_pulls_valence_down_and_happiness_pulls_it_up() -> None:
    calm = EmotionState(stress=0.0)
    strained = EmotionState(stress=0.9)

    assert calm.valence > strained.valence


def test_arousal_rises_with_energy_stress_and_curiosity() -> None:
    quiet = EmotionState(energy=0.1, stress=0.0, curiosity=0.0)
    lively = EmotionState(energy=0.9, stress=0.5, curiosity=0.9)

    assert 0.0 <= quiet.arousal < lively.arousal <= 1.0


def test_derived_values_cannot_disagree_with_the_state() -> None:
    """The reason they are computed rather than stored (docs/09 §3.2)."""
    state = EmotionState(happiness=0.8)
    happier = EmotionState(happiness=0.9)

    assert happier.valence > state.valence


# =========================================================================
# Integration bookkeeping
# =========================================================================


def test_integration_stamps_the_time_and_bumps_the_version() -> None:
    after = _integrate(EmotionState(), {}, now=NOW)

    assert after.updated_at == NOW
    assert after.version == 2


def test_distance_is_the_l1_norm() -> None:
    a = EmotionState(happiness=0.5, stress=0.1)
    b = EmotionState(happiness=0.6, stress=0.3)

    assert a.distance(b) == pytest.approx(0.3)
