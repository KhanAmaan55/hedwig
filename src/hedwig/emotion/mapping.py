"""Appraisal to state deltas (docs/09 §4.3).

A fixed matrix of coefficients, in code, reviewed by a human, never learned at runtime. Read
a row as: *a unit of this appraisal pushes these dimensions by these amounts.*

Two stages — event to appraisal, appraisal to deltas — rather than one, because the
appraisal dimensions are the interpretable layer. Going straight from `turn failed` to
`stress += 0.12` would work and would be unreadable a year from now, and unreviewable by
anyone who has to tune it.

The matrix is deliberately not configurable. Changing what HEDWIG's mood responds to is an
architectural change with an ADR, not a setting someone flips (docs/08 §8).
"""

from __future__ import annotations

from hedwig.core.ports.emotion import Appraisal, AppraisalDimension, Dimension

A = AppraisalDimension
D = Dimension

MATRIX: dict[AppraisalDimension, dict[Dimension, float]] = {
    A.NOVELTY: {
        D.HAPPINESS: +0.10,
        D.TRUST: 0.0,
        D.CURIOSITY: +0.60,
        D.CONFIDENCE: 0.0,
        D.ENERGY: -0.05,
        D.STRESS: +0.10,
    },
    A.GOAL_CONGRUENCE: {
        D.HAPPINESS: +0.40,
        D.TRUST: +0.15,
        D.CURIOSITY: -0.10,
        D.CONFIDENCE: +0.30,
        D.ENERGY: +0.10,
        D.STRESS: -0.30,
    },
    A.CERTAINTY: {
        D.HAPPINESS: +0.10,
        D.TRUST: +0.10,
        D.CURIOSITY: -0.20,
        D.CONFIDENCE: +0.40,
        D.ENERGY: 0.0,
        D.STRESS: -0.30,
    },
    A.AGENCY: {
        D.HAPPINESS: +0.10,
        D.TRUST: +0.05,
        D.CURIOSITY: 0.0,
        D.CONFIDENCE: +0.20,
        D.ENERGY: -0.10,
        D.STRESS: -0.10,
    },
    A.SOCIAL_VALENCE: {
        D.HAPPINESS: +0.30,
        # Trust's strongest input. A warm interaction is the observable that moves it, which
        # is the role `warmth` held before ADR-0019 folded the two together.
        D.TRUST: +0.35,
        D.CURIOSITY: +0.10,
        D.CONFIDENCE: +0.10,
        D.ENERGY: +0.05,
        D.STRESS: -0.20,
    },
    A.EFFORT: {
        D.HAPPINESS: 0.0,
        D.TRUST: 0.0,
        D.CURIOSITY: 0.0,
        D.CONFIDENCE: 0.0,
        D.ENERGY: -0.30,
        D.STRESS: +0.40,
    },
    A.NORM_FIT: {
        D.HAPPINESS: +0.20,
        D.TRUST: +0.15,
        D.CURIOSITY: 0.0,
        D.CONFIDENCE: +0.20,
        D.ENERGY: 0.0,
        D.STRESS: -0.20,
    },
}

TICK_SCALE = 0.25
"""How much of a full-unit appraisal reaches the state in one tick.

Without it the matrix coefficients *are* the per-tick movement, and a single completed turn
would move confidence by 0.3 — a third of the range, from one ordinary event. The caps in
`dynamics.py` would catch the extreme cases; this makes the ordinary ones proportionate.
"""


def deltas_for(appraisal: Appraisal) -> dict[Dimension, float]:
    """Turn one appraisal into a delta per dimension.

    Pure, and linear in the appraisal, which is what lets a tick coalesce many events by
    simply summing their deltas (docs/09 §5.1).
    """
    result: dict[Dimension, float] = dict.fromkeys(Dimension, 0.0)
    if appraisal.is_neutral:
        return result

    for appraisal_dimension, value in appraisal.values().items():
        if value == 0.0:
            continue
        row = MATRIX[appraisal_dimension]
        for dimension, coefficient in row.items():
            result[dimension] += coefficient * value * appraisal.significance * TICK_SCALE
    return result


def combine(batch: list[dict[Dimension, float]]) -> dict[Dimension, float]:
    """Sum the deltas of everything appraised since the last tick."""
    total: dict[Dimension, float] = dict.fromkeys(Dimension, 0.0)
    for deltas in batch:
        for dimension, value in deltas.items():
            total[dimension] += value
    return total
