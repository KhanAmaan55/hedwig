"""Importance scoring, decay and reinforcement (docs/06 §4.2, §7).

Pure functions over numbers. No I/O, no clock, no database — which is why they can be
property-tested exhaustively, and why the maths that decides what HEDWIG forgets is the
best-verified code in the system. A bug here is not a crash; it is a companion that quietly
loses a year of someone's life.

The formulas are transcribed from the documentation rather than invented here, so the
constants below are the *specification* and changing one is a documentation change.
"""

from __future__ import annotations

import math
from datetime import datetime

from hedwig.core.types import SalienceInputs

# -- importance at capture (docs/06 §4.2) ---------------------------------

BASE = 0.35
WEIGHT_USER_FLAG = 0.25
WEIGHT_EMOTION = 0.20
WEIGHT_GOAL = 0.15
WEIGHT_NOVELTY = 0.10
WEIGHT_CENTRALITY = 0.10
WEIGHT_REDUNDANCY = -0.15

# -- decay (docs/06 §7.1) -------------------------------------------------

DEFAULT_HALF_LIFE_DAYS = 30.0
FLOOR_FRACTION = 0.15
"""Salience never falls below this fraction of base importance."""

MAX_DECAY_DAYS_PER_PASS = 7.0
"""A clock jump — a wrong system time, a laptop waking after a month — must not be able to
age memories by a year in one pass (docs/17 §12)."""

# -- reinforcement (docs/06 §7.2) -----------------------------------------

REINFORCE_RETRIEVED = 0.02
REINFORCE_CITED = 0.05
REINFORCE_VALIDATED = 0.10
REINFORCE_CORROBORATED = 0.08

# -- forgetting (docs/06 §7.3) --------------------------------------------

FORGET_THRESHOLD = 0.08
FORGET_MIN_AGE_DAYS = 14.0
FORGET_MIN_IDLE_DAYS = 60.0
FORGET_RATE_CAP = 0.02
"""At most 2% of active memories per pass. A bug in the salience maths must not be able to
cause mass amnesia overnight, and a rate cap is the cheapest insurance against that class
of catastrophe (docs/06 §7.3)."""


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def base_importance(inputs: SalienceInputs) -> float:
    """The judgement made at capture, frozen for the memory's life (docs/06 §4.2).

    Note that `emotional_charge` contributes by *magnitude*: a strongly negative moment is
    as memorable as a strongly positive one.
    """
    score = (
        BASE
        + WEIGHT_USER_FLAG * float(inputs.user_flagged)
        + WEIGHT_EMOTION * abs(inputs.emotional_charge)
        + WEIGHT_GOAL * clamp(inputs.goal_relevance)
        + WEIGHT_NOVELTY * clamp(inputs.novelty)
        + WEIGHT_CENTRALITY * clamp(inputs.entity_centrality)
        + WEIGHT_REDUNDANCY * clamp(inputs.redundancy)
    )
    return clamp(score)


def salience_floor(base: float) -> float:
    """The level below which a memory cannot decay while it remains important."""
    return FLOOR_FRACTION * clamp(base)


def half_life_days(base: float, access_count: int, *, base_half_life: float) -> float:
    """How long this memory takes to lose half its salience.

    Two multipliers, both from docs/06 §7.1: important memories fade slower, and frequently
    recalled ones fade slower still. The consequence is the intended one — a trivial
    memory touched once fades below the retrieval threshold in about a month, while an
    important, frequently-recalled one effectively never fades.
    """
    importance_factor = 1.0 + 2.0 * clamp(base)
    access_factor = 1.0 + 0.5 * math.log1p(max(0, access_count))
    return base_half_life * importance_factor * access_factor


def decayed_salience(
    *,
    salience: float,
    base: float,
    access_count: int,
    elapsed_days: float,
    base_half_life: float = DEFAULT_HALF_LIFE_DAYS,
    pinned: bool = False,
) -> float:
    """Exponential decay toward a floor set by base importance.

        salience = floor + (salience - floor) * 2 ^ (-days / half_life)

    Pinned memories are returned untouched: the user said never forget this, and no
    arithmetic gets to overrule that.
    """
    if pinned or elapsed_days <= 0:
        return clamp(salience)

    # A clock that jumped must not destroy months of memory in one pass.
    days = min(elapsed_days, MAX_DECAY_DAYS_PER_PASS)

    floor = salience_floor(base)
    if salience <= floor:
        return clamp(floor)

    life = half_life_days(base, access_count, base_half_life=base_half_life)
    if life <= 0:  # pragma: no cover - guarded by config validation
        return clamp(floor)

    decayed = floor + (salience - floor) * (2.0 ** (-days / life))
    return clamp(decayed)


def reinforcement(
    *,
    retrieved: bool = False,
    cited: bool = False,
    validated: bool = False,
    corroborated: bool = False,
) -> float:
    """How much to raise salience for a memory that proved useful (docs/06 §7.2).

    The ordering is the point: retrieval alone counts for less than being *used*, and being
    used counts for less than being *validated* by the user.
    """
    return (
        REINFORCE_RETRIEVED * retrieved
        + REINFORCE_CITED * cited
        + REINFORCE_VALIDATED * validated
        + REINFORCE_CORROBORATED * corroborated
    )


def reinforced_salience(salience: float, amount: float) -> float:
    return clamp(salience + max(0.0, amount))


def is_forgettable(
    *,
    salience: float,
    pinned: bool,
    age_days: float,
    idle_days: float,
    is_sole_source: bool,
    threshold: float = FORGET_THRESHOLD,
) -> bool:
    """Whether a memory may be tombstoned (docs/06 §7.3).

    All five conditions must hold. The `is_sole_source` guard is the subtle one: forgetting
    the only episode that justifies an active belief would leave HEDWIG holding an opinion
    it cannot account for — which is exactly how humans acquire prejudices, and not a
    feature we want.
    """
    return (
        salience < threshold
        and not pinned
        and age_days > FORGET_MIN_AGE_DAYS
        and idle_days > FORGET_MIN_IDLE_DAYS
        and not is_sole_source
    )


def forget_budget(active_count: int, *, rate: float = FORGET_RATE_CAP) -> int:
    """How many memories one pass may forget.

    Always at least one once there is anything to forget, so a small store still tidies
    itself, and never more than the rate cap, so a large one cannot be emptied by a bug.
    """
    if active_count <= 0:
        return 0
    return max(1, int(active_count * rate))


def elapsed_days(since: datetime | None, now: datetime) -> float:
    """Days between two instants, floored at zero.

    Floored rather than allowed negative: a clock that went backwards should stall decay,
    not reverse it.
    """
    if since is None:
        return 0.0
    return max(0.0, (now - since).total_seconds() / 86_400.0)
