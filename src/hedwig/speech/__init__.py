"""Speech output (docs/15 §13): Piper behind the `Speaker` port."""

from hedwig.speech.piper import PiperSpeaker
from hedwig.speech.text import length_scale, speakable

__all__ = ["PiperSpeaker", "length_scale", "speakable"]
