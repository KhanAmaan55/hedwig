"""Voice (VOI-01, docs/15 §13): what is said, how fast, and the Piper adapter."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from hedwig.core.errors import CapabilityUnavailableError
from hedwig.core.ports.emotion import EmotionState
from hedwig.speech import PiperSpeaker, length_scale, speakable

FAKE = [sys.executable, str(Path(__file__).parents[1] / "fixtures" / "fake_piper.py")]


@pytest.fixture
def voices(tmp_path: Path) -> Path:
    (tmp_path / "en_US-lessac-medium.onnx").write_bytes(b"model")
    return tmp_path


def test_citations_links_and_markup_are_not_read_aloud() -> None:
    said = speakable("Python 3.15 is out [1][2]. See **https://python.org/downloads** for `it`.")
    assert said == "Python 3.15 is out. See a link for it."


def test_very_long_replies_are_cut_at_a_sentence() -> None:
    said = speakable("A sentence here. " * 200)
    assert len(said) <= 1500 and said.endswith(".")


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (None, 1.0),
        (EmotionState(happiness=0.9, curiosity=0.9, stress=0.0, energy=0.9), "faster"),
        (EmotionState(energy=0.05, happiness=0.3, curiosity=0.2), "slower"),
    ],
)
def test_the_pace_follows_the_mood_within_bounds(
    state: EmotionState | None, expected: object
) -> None:
    scale = length_scale(state)
    assert 0.85 <= scale <= 1.2
    if expected == "faster":
        assert scale < 1.0
    elif expected == "slower":
        assert scale > 1.0
    else:
        assert scale == expected


async def test_without_piper_installed_hedwig_says_why(voices: Path) -> None:
    speaker = PiperSpeaker(
        command=["no-such-piper"], voice="en_US-lessac-medium", voices_dir=voices
    )
    status = await speaker.status()
    assert not status.available and "uv tool install piper-tts" in status.reason
    with pytest.raises(CapabilityUnavailableError):
        await speaker.synthesize("hello")


async def test_without_the_voice_hedwig_says_which(tmp_path: Path) -> None:
    speaker = PiperSpeaker(command=FAKE, voice="en_GB-alan-low", voices_dir=tmp_path)
    status = await speaker.status()
    assert not status.available and "en_GB-alan-low" in status.reason


async def test_speech_is_a_wav(voices: Path) -> None:
    speaker = PiperSpeaker(command=FAKE, voice="en_US-lessac-medium", voices_dir=voices)
    audio = await speaker.synthesize("Hello there, this is HEDWIG.", length_scale=0.9)
    assert audio[:4] == b"RIFF" and audio[8:12] == b"WAVE"


async def test_a_failing_voice_is_a_typed_error_not_a_crash(voices: Path) -> None:
    speaker = PiperSpeaker(command=FAKE, voice="en_US-lessac-medium", voices_dir=voices)
    with pytest.raises(CapabilityUnavailableError, match="could not be produced"):
        await speaker.synthesize("please FAIL now")
