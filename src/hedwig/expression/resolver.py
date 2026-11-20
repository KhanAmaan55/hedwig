"""State to expression (docs/15 §5, docs/28 §4).

A pure function. The whole expressive behaviour of the avatar is decided here and testable
without a renderer; the renderer only animates toward what this returns.

Reads emotion, and since AVA-01 one fact about activity: whether HEDWIG is composing a reply.
Thinking looks up and aside, as people do when they are working something out. The user's
presence and personality traits remain out (docs/15 §5), as do `concentration` and `surprise`.
Speaking is the renderer's to show: it knows when audio is playing (docs/15 §13).
"""

from __future__ import annotations

import math

from hedwig.core.ports.emotion import EmotionState
from hedwig.core.ports.expression import ExpressionIntent, GazeTarget, Mood, Overlay


def resolve(state: EmotionState | None, *, thinking: bool = False) -> ExpressionIntent:
    """The expression for an emotional state. `None` (no emotion engine) is a neutral face."""
    if state is None:
        return (
            ExpressionIntent(gaze=GazeTarget.THINKING, action="thinking")
            if thinking
            else ExpressionIntent()
        )

    valence = state.valence
    arousal = state.arousal
    raw = {
        Overlay.INTEREST: 0.7 * state.curiosity + 0.3 * arousal,
        # docs/15 used the `warmth` dimension; ADR-0019 folded it into `trust`.
        Overlay.WARMTH: 0.8 * state.trust + 0.2 * max(0.0, valence),
        Overlay.AMUSEMENT: _unit(2 * (state.happiness - 0.5)) * max(0.0, valence),
        # Below the midpoint, not `1 - confidence`: the latter puts a worried face on a confident
        # character at rest (0.38 at confidence 0.62), which is the face lying.
        Overlay.UNCERTAINTY: _unit(2 * (0.5 - state.confidence)),
        Overlay.FATIGUE: _unit(2 * (0.5 - state.energy)),
    }
    return ExpressionIntent(
        mood=Mood(valence=round(valence, 4), arousal=round(arousal, 4)),
        overlay=normalise({k: _unit(v) for k, v in raw.items()}),
        gaze=GazeTarget.THINKING if thinking else GazeTarget.USER,
        energy=round(_unit(state.energy), 4),
        action="thinking" if thinking else "idle",
    )


def normalise(overlay: dict[Overlay, float]) -> dict[Overlay, float]:
    """Scale so the weights sum to at most 1 (docs/15 §4).

    Proportional, so the *balance* of an expression survives: a face that is twice as
    interested as it is warm stays that way when both are scaled down.
    """
    total = sum(overlay.values())
    scale = 1.0 / total if total > 1.0 else 1.0
    # Rounded *down*: rounding each weight to nearest let five of them sum to 1.0001, and
    # "at most 1" is a guarantee renderers rely on, not an approximation.
    return {key: math.floor(value * scale * 10_000) / 10_000 for key, value in overlay.items()}


def _unit(value: float) -> float:
    return max(0.0, min(1.0, value))
