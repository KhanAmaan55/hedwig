"""The speech port (docs/15 §13). Text in, WAV out; how is the adapter's business."""

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
