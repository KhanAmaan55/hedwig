"""Layer 3 — Cognition: the emotion engine (docs/09).

A deterministic numeric state that modulates behaviour. The README is explicit and correct:
*emotions influence behaviour but are not real feelings*. This module takes that seriously in
both directions — it builds a real mechanism, and it refuses to claim the mechanism is
experience.

The constraint that keeps it honest: every dimension must change at least one measurable
behaviour, or it is deleted (INV-3). Emotion that only appears in prose about how HEDWIG
feels is decoration, and decoration invites the user to believe something false.

No model is involved anywhere in this module, by design (docs/09 §4.2).
"""

from __future__ import annotations

from hedwig.emotion.appraisal import RULES, Appraiser
from hedwig.emotion.bindings import behaviour, style_directives, turn_policy
from hedwig.emotion.dynamics import (
    FLOOR,
    HALF_LIFE,
    MAX_DELTA_PER_TICK,
    Baselines,
    Traits,
    circadian_energy,
    integrate,
)
from hedwig.emotion.engine import SUBSCRIPTION, TICK_SECONDS, EmotionEngine
from hedwig.emotion.mapping import MATRIX, combine, deltas_for
from hedwig.emotion.mind import EmotionMindReader
from hedwig.emotion.store import EmotionStore

__all__ = [
    "FLOOR",
    "HALF_LIFE",
    "MATRIX",
    "MAX_DELTA_PER_TICK",
    "RULES",
    "SUBSCRIPTION",
    "TICK_SECONDS",
    "Appraiser",
    "Baselines",
    "EmotionEngine",
    "EmotionMindReader",
    "EmotionStore",
    "Traits",
    "behaviour",
    "circadian_energy",
    "combine",
    "deltas_for",
    "integrate",
    "style_directives",
    "turn_policy",
]
