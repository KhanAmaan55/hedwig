"""The transition rules, as arithmetic (docs/09 §5).

Pure functions over numbers: no clock read, no database, no events. That is deliberate and
it is the reason the emotion engine is testable at all — a simulated week runs in
milliseconds, and the same inputs always produce the same output to the last bit.

Three properties the update equation guarantees, each pre-empting a specific failure:

1. **Bounded rate of change.** One dramatic event cannot swing the mood. A companion whose
   mood snaps is unsettling, and no real state moves that fast.
2. **Return to baseline.** Without homeostasis, state random-walks into a corner and stays
   there, which is the most common failure of naive emotion models.
3. **Floors.** `trust` cannot be pushed below 0.25 by dynamics at all, which is what makes
   it safe to model trust as a drive (ADR-0019).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from hedwig.core.ports.emotion import DIMENSIONS, Dimension, EmotionState

HALF_LIFE: dict[Dimension, timedelta] = {
    Dimension.HAPPINESS: timedelta(hours=2),
    # Seven days. A week of silence leaves trust roughly where it was, which is the whole
    # difference between a drive and a relationship ledger (ADR-0019).
    Dimension.TRUST: timedelta(days=7),
    Dimension.CURIOSITY: timedelta(hours=4),
    Dimension.CONFIDENCE: timedelta(hours=1),
    Dimension.ENERGY: timedelta(hours=8),
    Dimension.STRESS: timedelta(minutes=45),
}

MAX_DELTA_PER_TICK: dict[Dimension, float] = dict.fromkeys(DIMENSIONS, 0.15)
MAX_DELTA_PER_TICK[Dimension.TRUST] = 0.05
"""A third of every other dimension: a single turn must not move trust perceptibly."""

FLOOR: dict[Dimension, float] = dict.fromkeys(DIMENSIONS, 0.0)
FLOOR[Dimension.TRUST] = 0.25
"""No sequence of events, however bad, takes trust below this. A bad day is not evidence
that the relationship is over (docs/09 §3.1)."""

CEILING = 1.0

MAX_ELAPSED = timedelta(hours=12)
"""A clock jump must not collapse the whole vector to baseline in one tick. Twelve hours is
past every half-life but energy's, so a genuine overnight gap still resolves fully."""


@dataclass(frozen=True, slots=True)
class Traits:
    """The personality projection emotion reads (docs/10 §4).

    Every trait is 0.5 until the personality engine exists, so the baselines below are the
    midpoints of their documented expressions rather than invented constants.
    """

    optimism: float = 0.5
    agreeableness: float = 0.5
    curiosity: float = 0.5
    assertiveness: float = 0.5


NEUTRAL_TRAITS = Traits()
"""What emotion reads until personality exists. Explicit, so the day a real profile arrives
the difference is visible in one place."""


@dataclass(frozen=True, slots=True)
class Baselines:
    """Where each dimension idles (docs/09 §3).

    `energy` is absent: it is the one dimension whose baseline is a function of time rather
    than of personality, and it is computed per tick by `circadian_energy`.
    """

    happiness: float
    trust: float
    curiosity: float
    confidence: float
    stress: float

    @classmethod
    def from_traits(cls, traits: Traits | None = None) -> Baselines:
        traits = traits or NEUTRAL_TRAITS
        return cls(
            happiness=0.40 + 0.30 * traits.optimism,
            trust=0.50 + 0.20 * traits.agreeableness,
            curiosity=0.30 + 0.50 * traits.curiosity,
            confidence=0.40 + 0.40 * traits.assertiveness,
            stress=0.15,
        )

    def at(self, dimension: Dimension, *, energy: float) -> float:
        if dimension is Dimension.ENERGY:
            return energy
        value: float = getattr(self, dimension.value)
        return value

    def as_dict(self, *, energy: float) -> dict[str, float]:
        return {d.value: round(self.at(d, energy=energy), 6) for d in DIMENSIONS}


def circadian_energy(local_time: datetime) -> float:
    """Energy's baseline as a function of the *user's local* hour (docs/09 §5.3).

        0.45 + 0.30 * sin(2pi * (hour - 9) / 24), clamped to [0.25, 0.85]

    Peaks mid-afternoon, troughs around 03:00. A companion whose energy tracks UTC while the
    user is in Lisbon is worse than one with no circadian model at all, which is why this
    takes a local time and the caller is responsible for converting.

    An honesty note that belongs next to the code, not only in the document: this is a
    simulation of a pattern, not fatigue. It may shorten a reply; it may not claim tiredness.
    """
    hours = local_time.hour + local_time.minute / 60 + local_time.second / 3600
    raw = 0.45 + 0.30 * math.sin(2 * math.pi * (hours - 9) / 24)
    return max(0.25, min(0.85, raw))


def decay_toward(
    *, value: float, baseline: float, elapsed: timedelta, half_life: timedelta
) -> float:
    """Exponential approach to baseline: the fraction of the gap closed in `elapsed`.

    Returns the *delta*, not the new value, so the caller can sum it with the appraisal
    delta and clamp once.
    """
    if elapsed <= timedelta(0) or half_life <= timedelta(0):
        return 0.0
    capped = min(elapsed, MAX_ELAPSED)
    closed: float = 1.0 - 2.0 ** (-capped / half_life)
    return closed * (baseline - value)


def clamp(dimension: Dimension, value: float) -> float:
    return max(FLOOR[dimension], min(CEILING, value))


def cap_delta(dimension: Dimension, delta: float) -> float:
    limit = MAX_DELTA_PER_TICK[dimension]
    return max(-limit, min(limit, delta))


def integrate(
    state: EmotionState,
    *,
    deltas: dict[Dimension, float],
    baselines: Baselines,
    energy_baseline: float,
    elapsed: timedelta,
    now: datetime,
) -> EmotionState:
    """One tick of the update equation (docs/09 §5.1).

    Order matters and is not arbitrary: the appraisal delta is capped *before* decay is
    added, so decay can never be used to smuggle a larger swing past the cap, and the clamp
    is applied once at the end so a dimension cannot leave its range even transiently.
    """
    updated: dict[Dimension, float] = {}
    for dimension in DIMENSIONS:
        current = state[dimension]
        baseline = baselines.at(dimension, energy=energy_baseline)
        change = cap_delta(dimension, deltas.get(dimension, 0.0))
        pull = decay_toward(
            value=current,
            baseline=baseline,
            elapsed=elapsed,
            half_life=HALF_LIFE[dimension],
        )
        updated[dimension] = round(clamp(dimension, current + change + pull), 9)
    return state.with_values(updated, at=now)
