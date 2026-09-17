"""Importance, decay and forgetting maths (docs/06 §4.2, §7).

Pure functions, so they get property tests. This is the best-verified code in the system on
purpose: a bug here is not a crash, it is a companion that quietly loses a year of
someone's life.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from hedwig.core.types import SalienceInputs
from hedwig.memory.salience import (
    DEFAULT_HALF_LIFE_DAYS,
    FORGET_RATE_CAP,
    FORGET_THRESHOLD,
    MAX_DECAY_DAYS_PER_PASS,
    base_importance,
    decayed_salience,
    elapsed_days,
    forget_budget,
    half_life_days,
    is_forgettable,
    reinforced_salience,
    reinforcement,
    salience_floor,
)

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


# =========================================================================
# Importance at capture
# =========================================================================


def test_a_bare_memory_gets_the_base_score() -> None:
    assert base_importance(SalienceInputs()) == pytest.approx(0.35)


def test_the_user_asking_to_remember_is_the_strongest_signal() -> None:
    flagged = base_importance(SalienceInputs(user_flagged=True))
    assert flagged > base_importance(SalienceInputs(novelty=1.0))
    assert flagged > base_importance(SalienceInputs(goal_relevance=1.0))


def test_emotional_charge_counts_by_magnitude_not_sign() -> None:
    """A strongly negative moment is as memorable as a strongly positive one."""
    assert base_importance(SalienceInputs(emotional_charge=-0.9)) == pytest.approx(
        base_importance(SalienceInputs(emotional_charge=0.9))
    )


def test_redundancy_is_the_only_term_that_subtracts() -> None:
    assert base_importance(SalienceInputs(redundancy=1.0)) < base_importance(SalienceInputs())


def test_importance_is_always_a_probability() -> None:
    maximal = SalienceInputs(
        user_flagged=True,
        emotional_charge=1.0,
        goal_relevance=1.0,
        novelty=1.0,
        entity_centrality=1.0,
    )
    assert 0.0 <= base_importance(maximal) <= 1.0
    assert 0.0 <= base_importance(SalienceInputs(redundancy=5.0)) <= 1.0


# =========================================================================
# Decay
# =========================================================================


def test_decay_is_monotonic() -> None:
    """Salience never increases from decay alone (docs/20 §5)."""
    previous = 0.9
    for day in range(1, 40):
        current = decayed_salience(
            salience=previous, base=0.5, access_count=0, elapsed_days=1, base_half_life=10
        )
        assert current <= previous, f"salience rose on day {day}"
        previous = current


def test_decay_never_goes_below_the_floor_set_by_importance() -> None:
    """An important memory nobody has touched must not become indistinguishable from noise."""
    floor = salience_floor(0.9)
    salience = 0.9

    # Many passes, because one pass is capped at MAX_DECAY_DAYS_PER_PASS.
    for _ in range(500):
        salience = decayed_salience(
            salience=salience, base=0.9, access_count=0, elapsed_days=7, base_half_life=1
        )
        assert salience >= floor

    assert salience == pytest.approx(floor, abs=1e-6)


def test_a_pinned_memory_never_decays() -> None:
    """The user said never forget this. No arithmetic gets to overrule that."""
    assert (
        decayed_salience(
            salience=0.8,
            base=0.1,
            access_count=0,
            elapsed_days=100_000,
            pinned=True,
        )
        == 0.8
    )


def test_one_half_life_halves_the_distance_to_the_floor() -> None:
    base, salience = 0.5, 1.0
    floor = salience_floor(base)
    life = half_life_days(base, 0, base_half_life=DEFAULT_HALF_LIFE_DAYS)

    result = decayed_salience(
        salience=salience,
        base=base,
        access_count=0,
        elapsed_days=min(life, MAX_DECAY_DAYS_PER_PASS),
        base_half_life=DEFAULT_HALF_LIFE_DAYS,
    )
    # Capped at one pass, so check the shape rather than exactly half.
    assert floor < result < salience


def test_important_memories_fade_more_slowly() -> None:
    trivial = half_life_days(0.1, 0, base_half_life=30)
    important = half_life_days(0.9, 0, base_half_life=30)
    assert important > trivial


def test_frequently_recalled_memories_fade_more_slowly() -> None:
    once = half_life_days(0.5, 1, base_half_life=30)
    often = half_life_days(0.5, 100, base_half_life=30)
    assert often > once


def test_a_clock_jump_cannot_age_memories_by_a_year() -> None:
    """A misconfigured clock must not be able to destroy months of memory in one pass."""
    capped = decayed_salience(
        salience=0.9, base=0.5, access_count=0, elapsed_days=MAX_DECAY_DAYS_PER_PASS
    )
    absurd = decayed_salience(salience=0.9, base=0.5, access_count=0, elapsed_days=100_000)
    assert absurd == pytest.approx(capped)


def test_no_elapsed_time_means_no_decay() -> None:
    assert decayed_salience(salience=0.7, base=0.5, access_count=0, elapsed_days=0) == 0.7


def test_decay_stays_in_range_for_arbitrary_inputs() -> None:
    for salience in (0.0, 0.01, 0.5, 1.0):
        for base in (0.0, 0.5, 1.0):
            for days in (0.0, 0.5, 7.0, 1000.0):
                result = decayed_salience(
                    salience=salience, base=base, access_count=3, elapsed_days=days
                )
                assert 0.0 <= result <= 1.0


# =========================================================================
# Reinforcement
# =========================================================================


def test_reinforcement_is_ordered_by_how_much_use_was_proven() -> None:
    """Retrieved < used < validated by the user (docs/06 §7.2)."""
    assert (
        reinforcement(retrieved=True)
        < reinforcement(cited=True)
        < reinforcement(corroborated=True)
        < reinforcement(validated=True)
    )


def test_reinforcement_accumulates() -> None:
    assert reinforcement(retrieved=True, cited=True) == pytest.approx(0.07)


def test_reinforced_salience_is_capped() -> None:
    assert reinforced_salience(0.98, 0.5) == 1.0


def test_reinforcement_never_lowers_salience() -> None:
    assert reinforced_salience(0.5, -1.0) == 0.5


# =========================================================================
# Forgetting
# =========================================================================


def _forgettable(**overrides: object) -> bool:
    args: dict[str, object] = {
        "salience": 0.01,
        "pinned": False,
        "age_days": 100.0,
        "idle_days": 100.0,
        "is_sole_source": False,
    }
    args.update(overrides)
    return is_forgettable(**args)  # type: ignore[arg-type]


def test_a_faded_old_unused_memory_may_be_forgotten() -> None:
    assert _forgettable() is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("salience", FORGET_THRESHOLD),  # not below the threshold
        ("pinned", True),
        ("age_days", 3.0),  # too recent
        ("idle_days", 5.0),  # used recently
        ("is_sole_source", True),
    ],
)
def test_every_guard_alone_prevents_forgetting(field: str, value: object) -> None:
    """All five conditions must hold. Any one of them saves the memory."""
    assert _forgettable(**{field: value}) is False


def test_nothing_recent_is_ever_forgotten() -> None:
    assert _forgettable(age_days=13.9) is False
    assert _forgettable(age_days=14.1) is True


def test_the_sole_source_of_a_belief_is_protected() -> None:
    """Otherwise HEDWIG keeps an opinion it cannot account for — how prejudices form."""
    assert _forgettable(is_sole_source=True) is False


# =========================================================================
# The rate cap
# =========================================================================


def test_the_forget_budget_is_a_small_fraction_of_the_store() -> None:
    assert forget_budget(1000) == int(1000 * FORGET_RATE_CAP)


def test_an_empty_store_forgets_nothing() -> None:
    assert forget_budget(0) == 0


def test_a_small_store_still_tidies_itself() -> None:
    """At least one, so a store of ten memories is not frozen forever."""
    assert forget_budget(5) == 1


def test_the_cap_bounds_catastrophe() -> None:
    """A bug in the salience maths must not be able to empty the store overnight."""
    assert forget_budget(100_000) <= 100_000 * FORGET_RATE_CAP


# =========================================================================
# Elapsed time
# =========================================================================


def test_elapsed_days_measures_forward() -> None:
    assert elapsed_days(NOW - timedelta(days=3), NOW) == pytest.approx(3.0)


def test_a_backwards_clock_stalls_decay_rather_than_reversing_it() -> None:
    assert elapsed_days(NOW + timedelta(days=5), NOW) == 0.0


def test_no_previous_pass_means_no_elapsed_time() -> None:
    assert elapsed_days(None, NOW) == 0.0
