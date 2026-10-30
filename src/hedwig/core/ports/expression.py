"""Expression intent (docs/15 §4, docs/28).

The seam between cognition and every avatar renderer. **Semantic, not geometric**: this says
*what is being expressed* — mood, a named overlay blend, where to look, how much energy — and
never a blendshape weight, a bone angle or a sprite. Each renderer maps it to what it can do
through a character profile, which is what keeps a second renderer a second consumer rather
than a rewrite (ADR-0021).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum


class Overlay(StrEnum):
    """The nameable part of an expression (docs/15 §4.1).

    Fixed and small. Deliberately absent: anger, sadness, fear, disgust — HEDWIG's face does
    not perform distress at the user. `concentration` and `surprise` are in docs/15's vocabulary
    and not produced here: the first needs the turn's action, the second needs threshold
    transients, and this resolver reads emotion only (docs/28 §3).
    """

    INTEREST = "interest"
    WARMTH = "warmth"
    AMUSEMENT = "amusement"
    UNCERTAINTY = "uncertainty"
    FATIGUE = "fatigue"


class GazeTarget(StrEnum):
    USER = "user"
    AWAY = "away"
    THINKING = "thinking"


@dataclass(frozen=True, slots=True)
class Mood:
    """Core affect: the substrate every renderer can do something with (docs/15 §4)."""

    valence: float = 0.0
    """-1..1"""
    arousal: float = 0.3
    """0..1"""


@dataclass(frozen=True, slots=True)
class ExpressionIntent:
    """What the face should be expressing now."""

    mood: Mood = field(default_factory=Mood)
    overlay: Mapping[Overlay, float] = field(default_factory=dict)
    """Weights in 0..1 that sum to at most 1."""
    gaze: GazeTarget = GazeTarget.USER
    energy: float = 0.5
    """0..1. Scales idle motion: a tired face moves less."""
    action: str = "idle"
    """idle | listening | thinking | speaking. Always idle until the turn announces stages."""
