"""The speech ports (docs/15 §13). Text in, WAV out — and audio in, text out (VOI-02)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class SpeechStatus:
    available: bool
    engine: str
    voice: str
    reason: str = ""


@runtime_checkable
class Speaker(Protocol):
    async def status(self) -> SpeechStatus: ...

    async def synthesize(self, text: str, *, length_scale: float = 1.0) -> bytes:
        """WAV bytes for `text`. Raises `CapabilityUnavailableError` when there is no voice."""
        ...


@dataclass(frozen=True, slots=True)
class Transcript:
    text: str
    seconds: float
    """Length of the audio that was heard."""


@runtime_checkable
class Listener(Protocol):
    """Speech recognition, on this machine (VOI-02). Audio is transcribed and discarded."""

    async def status(self) -> SpeechStatus: ...

    async def transcribe(self, audio: bytes) -> Transcript:
        """Text for `audio` (any common container: webm/opus, wav, m4a). Raises
        `CapabilityUnavailableError` when no model is installed."""
        ...
