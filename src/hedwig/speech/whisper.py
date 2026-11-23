"""Listening: faster-whisper on this machine (VOI-02, docs/15 §13.2).

Audio in, text out, nothing kept: the recording is decoded in memory, transcribed, and dropped;
only the text goes back, and it becomes a message only if the user sends it. The model loads on
first use (a second or two) and stays loaded. Transcription is CPU work, so it runs in a thread
and never blocks the event loop.
"""

from __future__ import annotations

import asyncio
import io
import threading
from pathlib import Path
from typing import Any

from hedwig.core.errors import CapabilityUnavailableError, InvalidRequestError
from hedwig.core.logging import fields, get_logger
from hedwig.core.ports.speech import SpeechStatus, Transcript

logger = get_logger(__name__)

INSTALL_HINT = "the model downloads into <data_dir>/voices/whisper on first setup (docs/15 §13.2)"


class WhisperListener:
    def __init__(self, *, model: str, models_dir: Path, max_seconds: float = 120.0) -> None:
        self._name = model
        self._dir = models_dir
        self._max_seconds = max_seconds
        self._model: Any = None
        self._lock = threading.Lock()

    def _installed(self) -> bool:
        return self._dir.exists() and any(self._dir.glob(f"**/*{self._name}*/**/model.bin"))

    async def status(self) -> SpeechStatus:
        try:
            import faster_whisper  # noqa: F401 — optional at runtime
        except ImportError:
            return SpeechStatus(
                False, "faster-whisper", self._name, "faster-whisper is not installed"
            )
        if not self._installed():
            return SpeechStatus(False, "faster-whisper", self._name, f"no model: {INSTALL_HINT}")
        return SpeechStatus(True, "faster-whisper", self._name)

    async def transcribe(self, audio: bytes) -> Transcript:
        if not audio:
            raise InvalidRequestError("No audio was sent.")
        current = await self.status()
        if not current.available:
            raise CapabilityUnavailableError(f"HEDWIG cannot listen: {current.reason}")
        return await asyncio.to_thread(self._transcribe, audio)

    def _transcribe(self, audio: bytes) -> Transcript:
        from faster_whisper import WhisperModel

        with self._lock:  # one model, loaded once, used by one transcription at a time
            if self._model is None:
                self._model = WhisperModel(
                    self._name,
                    device="cpu",
                    compute_type="int8",
                    download_root=str(self._dir),
                    local_files_only=True,  # never downloads at runtime: setup did, with consent
                )
            try:
                segments, info = self._model.transcribe(
                    io.BytesIO(audio),
                    language="en" if self._name.endswith(".en") else None,
                    vad_filter=True,
                )
                if info.duration > self._max_seconds:
                    raise InvalidRequestError(
                        f"That recording is longer than {self._max_seconds:g} seconds."
                    )
                text = " ".join(segment.text.strip() for segment in segments).strip()
            except (InvalidRequestError, CapabilityUnavailableError):
                raise
            except Exception as error:
                logger.warning("transcription failed", extra=fields(error=str(error)))
                raise InvalidRequestError(
                    "That audio could not be understood as a recording."
                ) from error
        return Transcript(text=text, seconds=round(float(info.duration), 2))
