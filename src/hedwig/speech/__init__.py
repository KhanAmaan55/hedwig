"""Speech (docs/15 §13): Piper speaks behind `Speaker`; faster-whisper listens behind `Listener`."""

from hedwig.speech.piper import PiperSpeaker
from hedwig.speech.text import length_scale, speakable
from hedwig.speech.whisper import WhisperListener

__all__ = ["PiperSpeaker", "WhisperListener", "length_scale", "speakable"]
