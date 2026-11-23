"""Speech recognition through the API (VOI-02, docs/15 §13.2)."""

from __future__ import annotations

from dataclasses import replace

from httpx import ASGITransport, AsyncClient

from hedwig.api.app import create_app
from hedwig.core.ports.speech import SpeechStatus, Transcript
from hedwig.core.token import TokenFile
from hedwig.wiring import Container


class _Ears:
    def __init__(self) -> None:
        self.heard: list[bytes] = []

    async def status(self) -> SpeechStatus:
        return SpeechStatus(True, "fake", "base.en")

    async def transcribe(self, audio: bytes) -> Transcript:
        self.heard.append(audio)
        return Transcript(text="Remind me to water the tomatoes.", seconds=2.4)


def _client(container: Container) -> AsyncClient:
    token = TokenFile(container.config.runtime.token_path).ensure()
    return AsyncClient(
        transport=ASGITransport(app=create_app(container)),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


async def test_without_a_model_hedwig_says_it_cannot_listen(container: Container) -> None:
    async with _client(container) as api:
        status = (await api.get("/v1/speech")).json()
        assert status["listening"] is False and "no model" in status["listening_reason"]
        refused = await api.post("/v1/speech/transcribe", content=b"RIFF....WAVE")
        assert refused.status_code == 503


async def test_a_recording_comes_back_as_text_and_is_not_kept(container: Container) -> None:
    ears = _Ears()
    listening = replace(container, listener=ears)
    async with _client(listening) as api:
        heard = await api.post(
            "/v1/speech/transcribe", content=b"webm-bytes", headers={"Content-Type": "audio/webm"}
        )
        assert heard.json() == {"text": "Remind me to water the tomatoes.", "seconds": 2.4}
        assert (await api.get("/v1/speech")).json()["listening"] is True
        assert (await api.post("/v1/speech/transcribe", content=b"")).status_code in (400, 422)
    assert ears.heard == [b"webm-bytes"], "empty audio is refused before any model sees it"
    rows = container.database.query("SELECT COUNT(*) AS n FROM message")
    assert rows[0]["n"] == 0, "a transcript becomes a message only when the user sends it"


async def test_the_transcript_endpoint_needs_the_token(container: Container) -> None:
    async with _client(container) as api:
        anonymous = await api.post(
            "/v1/speech/transcribe", content=b"x", headers={"Authorization": ""}
        )
        assert anonymous.status_code == 401
