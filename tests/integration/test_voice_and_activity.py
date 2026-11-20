"""The avatar thinks while HEDWIG composes, and the speech endpoint (AVA-01, VOI-01)."""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from hedwig.api.app import create_app
from hedwig.core.clock import FakeClock
from hedwig.core.config import Config
from hedwig.core.store import Database
from hedwig.core.token import TokenFile
from hedwig.llm.recorded import RecordedProvider
from hedwig.wiring import Container, build_container

FAKE = (sys.executable, str(Path(__file__).parents[1] / "fixtures" / "fake_piper.py"))


async def _client(container: Container) -> AsyncClient:
    token = TokenFile(container.config.runtime.token_path).ensure()
    return AsyncClient(
        transport=ASGITransport(app=create_app(container)),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


async def test_the_avatar_thinks_while_a_reply_is_composed(running_container: Container) -> None:
    """`busy` is "a session's turn lock is held or awaited" (docs/07 §16) — hold one."""
    brain = running_container.brain
    async with await _client(running_container) as api:
        before = (await api.get("/v1/mind/expression")).json()
        async with brain._session_turn("sess_composing"):
            during = (await api.get("/v1/mind/expression")).json()
        after = (await api.get("/v1/mind/expression")).json()

    assert before["action"] == "idle"
    assert (during["action"], during["gaze"]) == ("thinking", "thinking")
    assert after["action"] == "idle"


@pytest.fixture
async def voiced(
    config: Config,
    clock: FakeClock,
    database: Database,
    llm_provider: RecordedProvider,
    tmp_path: Path,
) -> AsyncIterator[Container]:
    voices = tmp_path / "voices"
    voices.mkdir()
    (voices / "en_US-lessac-medium.onnx").write_bytes(b"model")
    speech = config.speech.model_copy(update={"command": FAKE, "voices_dir": voices})
    container = build_container(
        config.model_copy(update={"speech": speech}),
        clock=clock,
        database=database,
        migrate=False,
        llm_provider=llm_provider,
    )
    await container.start()
    try:
        yield container
    finally:
        await container.stop()


async def test_speech_comes_back_as_wav_behind_the_token(voiced: Container) -> None:
    async with await _client(voiced) as api:
        status = (await api.get("/v1/speech")).json()
        spoken = await api.post("/v1/speech", json={"text": "Hello [1]. See https://x.test"})
        api.headers.pop("Authorization")
        anonymous = await api.post("/v1/speech", json={"text": "hello"})

    assert status["available"] is True and status["voice"] == "en_US-lessac-medium"
    assert spoken.status_code == 200
    assert spoken.headers["content-type"] == "audio/wav"
    assert spoken.content[:4] == b"RIFF"
    assert anonymous.status_code == 401


async def test_no_voice_installed_is_a_503_that_says_how(running_container: Container) -> None:
    async with await _client(running_container) as api:
        status = (await api.get("/v1/speech")).json()
        if status["available"]:
            pytest.skip("a real voice is installed on this machine")
        refused = await api.post("/v1/speech", json={"text": "hello"})
    assert refused.status_code == 503
    assert refused.json()["error"]["code"] == "capability_unavailable"
