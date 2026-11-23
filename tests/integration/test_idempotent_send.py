"""A retried message gets the original turn back, never a second answer (FND-06)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from hedwig.api.app import create_app
from hedwig.core.token import TokenFile
from hedwig.llm.recorded import RecordedProvider
from hedwig.wiring import Container


def _composed(provider: RecordedProvider) -> int:
    return sum(1 for call in provider.calls if call.purpose.startswith("compose"))


@pytest.fixture
async def api(running_container: Container) -> AsyncIterator[AsyncClient]:
    token = TokenFile(running_container.config.runtime.token_path).ensure()
    async with AsyncClient(
        transport=ASGITransport(app=create_app(running_container)),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        yield client


async def test_a_retried_post_returns_the_same_turn(
    running_container: Container, api: AsyncClient, llm_provider: RecordedProvider
) -> None:
    session = (await api.post("/v1/sessions", json={"channel": "api"})).json()["id"]
    body = {"text": "Hello there.", "client_message_id": "c-42"}

    first = (await api.post(f"/v1/sessions/{session}/messages", json=body)).json()
    again = (await api.post(f"/v1/sessions/{session}/messages", json=body)).json()

    assert again["turn_id"] == first["turn_id"] and again["reply"] == first["reply"]
    assert _composed(llm_provider) == 1, "HEDWIG answered twice"
    history = (await api.get(f"/v1/sessions/{session}/messages")).json()["messages"]
    assert [m["role"] for m in history] == ["user", "hedwig"]


async def test_a_retry_racing_the_original_waits_and_gets_it(
    running_container: Container, llm_provider: RecordedProvider
) -> None:
    brain = running_container.brain
    first, second = await asyncio.gather(
        brain.run_turn("Hi.", session_id="s", client_message_id="c-1"),
        brain.run_turn("Hi.", session_id="s", client_message_id="c-1"),
    )
    assert first["turn_id"] == second["turn_id"]
    assert _composed(llm_provider) == 1


async def test_a_retry_after_a_failure_is_answered(
    running_container: Container, llm_provider: RecordedProvider
) -> None:
    brain = running_container.brain
    llm_provider.available = False
    failed = await brain.run_turn("Hi.", session_id="s", client_message_id="c-7")
    assert failed["status"] != "completed"

    llm_provider.available = True
    retried = await brain.run_turn("Hi.", session_id="s", client_message_id="c-7")
    assert retried["status"] == "completed" and retried["turn_id"] != failed["turn_id"]


async def test_without_an_id_every_send_is_a_new_turn(
    running_container: Container, llm_provider: RecordedProvider
) -> None:
    await running_container.brain.run_turn("Hi.", session_id="s")
    await running_container.brain.run_turn("Hi.", session_id="s")
    assert _composed(llm_provider) == 2
