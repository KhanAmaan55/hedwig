"""The expression resolver (docs/15 §5, docs/28 §4).

Pure, so the avatar's entire expressive behaviour is tested here without a renderer — which is
the point of resolving in the backend rather than in each renderer.
"""

from __future__ import annotations

import itertools

import pytest

from hedwig.core.ports.emotion import Dimension, EmotionState
from hedwig.core.ports.expression import ExpressionIntent, GazeTarget, Overlay
from hedwig.expression import normalise, resolve

GRID = (0.0, 0.25, 0.5, 0.75, 1.0)


def _states() -> list[EmotionState]:
    """A coarse sweep of the whole state space (trust above its floor)."""
    states = []
    for happiness, curiosity, confidence, energy, stress in itertools.product(GRID, repeat=5):
        states.append(
            EmotionState(
                happiness=happiness,
                trust=0.6,
                curiosity=curiosity,
                confidence=confidence,
                energy=energy,
                stress=stress,
            )
        )
    return states


def test_every_channel_stays_in_range_across_the_state_space() -> None:
    for state in _states():
        intent = resolve(state)
        assert -1.0 <= intent.mood.valence <= 1.0
        assert 0.0 <= intent.mood.arousal <= 1.0
        assert 0.0 <= intent.energy <= 1.0
        for weight in intent.overlay.values():
            assert 0.0 <= weight <= 1.0


def test_overlays_never_sum_past_one() -> None:
    """docs/15 §4: a blend, not a pile."""
    for state in _states():
        assert sum(resolve(state).overlay.values()) <= 1.0 + 1e-6


def test_normalising_keeps_the_balance_of_an_expression() -> None:
    """Twice as interested as warm stays twice as interested as warm."""
    scaled = normalise({Overlay.INTEREST: 0.8, Overlay.WARMTH: 0.4, Overlay.FATIGUE: 0.4})

    assert scaled[Overlay.INTEREST] == pytest.approx(2 * scaled[Overlay.WARMTH])
    assert sum(scaled.values()) == pytest.approx(1.0)


def test_a_small_blend_is_left_alone() -> None:
    assert normalise({Overlay.INTEREST: 0.2, Overlay.WARMTH: 0.3}) == {
        Overlay.INTEREST: 0.2,
        Overlay.WARMTH: 0.3,
    }


@pytest.mark.parametrize(
    ("dimension", "overlay", "direction"),
    [
        (Dimension.CURIOSITY, Overlay.INTEREST, +1),
        (Dimension.TRUST, Overlay.WARMTH, +1),
        (Dimension.HAPPINESS, Overlay.AMUSEMENT, +1),
        (Dimension.CONFIDENCE, Overlay.UNCERTAINTY, -1),
        (Dimension.ENERGY, Overlay.FATIGUE, -1),
    ],
)
def test_each_overlay_follows_its_own_dimension(
    dimension: Dimension, overlay: Overlay, direction: int
) -> None:
    """The face reacts to emotion — each named overlay to the dimension that defines it."""
    low = resolve(EmotionState().with_dimension(dimension, 0.1 if direction > 0 else 0.9))
    high = resolve(EmotionState().with_dimension(dimension, 0.9 if direction > 0 else 0.1))

    assert high.overlay[overlay] > low.overlay[overlay]


def test_a_confident_character_at_rest_does_not_look_worried() -> None:
    """docs/15's `1 - confidence` would show 0.38 uncertainty at confidence 0.62: the face lying
    about a state the system is not in (docs/28 §4)."""
    assert resolve(EmotionState(confidence=0.62)).overlay[Overlay.UNCERTAINTY] == 0.0


def test_the_resting_face_is_warm_and_attentive_not_blank() -> None:
    """At rest the face is the character (docs/28 §4)."""
    rest = EmotionState(happiness=0.61, trust=0.645, curiosity=0.70, confidence=0.62, energy=0.6)
    overlay = resolve(rest).overlay

    assert overlay[Overlay.INTEREST] > 0.3
    assert overlay[Overlay.WARMTH] > 0.3
    assert overlay[Overlay.FATIGUE] == 0.0


def test_mood_carries_the_derived_core_affect() -> None:
    state = EmotionState(happiness=0.9, stress=0.05)
    intent = resolve(state)

    assert intent.mood.valence == pytest.approx(state.valence, abs=1e-4)
    assert intent.mood.arousal == pytest.approx(state.arousal, abs=1e-4)


def test_a_bad_day_reads_as_a_hard_time_not_as_distress() -> None:
    """The most negative available face is low valence, fatigue and uncertainty (docs/15
    §4.1) — and the vocabulary contains nothing darker to reach for."""
    bad = resolve(EmotionState(happiness=0.05, trust=0.3, confidence=0.1, energy=0.1, stress=0.95))

    assert bad.mood.valence < 0
    assert bad.overlay[Overlay.FATIGUE] > 0
    assert bad.overlay[Overlay.UNCERTAINTY] > 0
    assert {o.value for o in Overlay} == {
        "interest",
        "warmth",
        "amusement",
        "uncertainty",
        "fatigue",
    }


def test_without_emotion_the_face_is_neutral() -> None:
    assert resolve(None) == ExpressionIntent()


def test_gaze_is_on_the_user_and_the_action_idle() -> None:
    """No action input exists yet, so never 'thinking' or 'away' (docs/28 §3)."""
    intent = resolve(EmotionState())

    assert intent.gaze is GazeTarget.USER
    assert intent.action == "idle"


def test_resolution_is_deterministic() -> None:
    state = EmotionState(happiness=0.7, curiosity=0.8)

    assert resolve(state) == resolve(state)
