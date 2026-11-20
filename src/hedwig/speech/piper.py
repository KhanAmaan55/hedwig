"""Piper, run as a separate process (docs/15 §13).

The maintained Piper is GPL-3.0; HEDWIG is MIT. Running it as its own installed tool, never
importing it, keeps the two apart. No shell: arguments are a list, text goes in on stdin.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from collections.abc import Sequence
from pathlib import Path

from hedwig.core.errors import CapabilityUnavailableError
from hedwig.core.logging import fields, get_logger
from hedwig.core.ports.speech import SpeechStatus

logger = get_logger(__name__)

INSTALL_HINT = "uv tool install piper-tts, then download a voice (docs/15 §13)"


class PiperSpeaker:
    def __init__(
        self,
        *,
        command: Sequence[str],
        voice: str,
        voices_dir: Path,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._command = list(command)
        self._voice = voice
        self._voices = voices_dir
        self._timeout = timeout_seconds

    async def status(self) -> SpeechStatus:
        if not self._command or shutil.which(self._command[0]) is None:
            return SpeechStatus(
                False, "piper", self._voice, f"piper is not installed: {INSTALL_HINT}"
            )
        if not (self._voices / f"{self._voice}.onnx").exists():
            return SpeechStatus(
                False, "piper", self._voice, f"voice {self._voice} is not in {self._voices}"
            )
        return SpeechStatus(True, "piper", self._voice)

    async def synthesize(self, text: str, *, length_scale: float = 1.0) -> bytes:
        status = await self.status()
        if not status.available:
            raise CapabilityUnavailableError(f"No voice: {status.reason}", cause="speech")
        if not text.strip():
            raise CapabilityUnavailableError("Nothing to say.", cause="speech")

        with tempfile.TemporaryDirectory(prefix="hedwig-speech-") as scratch:
            output = Path(scratch) / "speech.wav"
            process = await asyncio.create_subprocess_exec(
                *self._command,
                "-m",
                self._voice,
                "--data-dir",
                str(self._voices),
                "--length-scale",
                f"{length_scale:.3f}",
                "-f",
                str(output),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, error = await asyncio.wait_for(
                    process.communicate(text.encode("utf-8")), timeout=self._timeout
                )
            except TimeoutError as exc:
                process.kill()
                raise CapabilityUnavailableError("Speaking took too long.", cause="speech") from exc
            if process.returncode != 0 or not output.exists() or output.stat().st_size == 0:
                logger.warning(
                    "piper failed",
                    extra=fields(
                        code=process.returncode, error=error.decode(errors="replace")[-300:]
                    ),
                )
                raise CapabilityUnavailableError("The voice could not be produced.", cause="speech")
            return output.read_bytes()
