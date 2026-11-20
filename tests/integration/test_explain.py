"""Why HEDWIG said that (SEC-01, docs/16 §14) — every field from a real turn's records."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from hedwig.api.app import create_app
from hedwig.core.token import TokenFile
from hedwig.wiring import Container

TEA = "Please remember that my favourite tea is smoky lapsang souchong."


@pytest.fixture
async def api(running_container: Container) -> AsyncIterator[AsyncClient]:
    token = TokenFile(running_container.config.runtime.token_path).ensure()
    async with AsyncClient(
        transport=ASGITransport(app=create_app(running_container)),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        yield client


async def _say(api: AsyncClient, session: str, text: str) -> dict[str, Any]:
    response = await api.post(f"/v1/sessions/{session}/messages", json={"text": text})
    assert response.status_code == 200, response.text
    return dict(response.json())


async def _session(api: AsyncClient) -> str:
    return str((await api.post("/v1/sessions")).json()["id"])


async def test_a_reply_that_drew_on_memory_explains_itself(
    api: AsyncClient, running_container: Container
) -> None:
    await _say(api, await _session(api), TEA)
    await running_container.bus.drain()
    reply = await _say(api, await _session(api), "What tea do I like?")

    why = (await api.get(f"/v1/explain/{reply['message_id']}")).json()

    assert why["question"] == "What tea do I like?"
    assert why["reply"] == reply["reply"]
    assert why["intent"] == "answer" and why["rationale"]
    assert why["directives"], "the directives the reply was composed under were not recorded"
    assert set(why["mood"]) >= {"happiness", "curiosity", "stress"}
    [item] = [i for i in why["retrieval"]["items"] if "lapsang" in (i["text"] or "")]
    assert item["trust"] == "user" and item["score"] > 0 and not item["forgotten_since"]
    assert item["memory_id"] in reply["cited_memory_ids"]
    assert why["retrieval"]["queries"] == ["What tea do I like?"]
    assert [m["purpose"] for m in why["model"]] == ["compose.reply"]
    assert why["model"][0]["model"] and why["model"][0]["status"] == "ok"
    assert why["tools"] == []
    assert why["trace_id"] and why["turn"]["status"] == "completed"


async def test_a_memory_forgotten_since_is_still_shown_and_says_so(
    api: AsyncClient, running_container: Container
) -> None:
    await _say(api, await _session(api), TEA)
    await running_container.bus.drain()
    reply = await _say(api, await _session(api), "What tea do I like?")
    memory_id = reply["cited_memory_ids"][0]
    await api.post(f"/v1/memory/{memory_id}/forget")

    why = (await api.get(f"/v1/explain/{reply['message_id']}")).json()

    [item] = [i for i in why["retrieval"]["items"] if i["memory_id"] == memory_id]
    assert item["forgotten_since"] is True and "lapsang" in item["text"]


async def test_a_tool_turn_shows_the_tool(api: AsyncClient) -> None:
    reply = await _say(api, await _session(api), "What time is it?")
    why = (await api.get(f"/v1/explain/{reply['message_id']}")).json()
    assert [(t["name"], t["status"]) for t in why["tools"]] == [("get_datetime", "ok")]


async def test_only_hedwigs_replies_have_explanations(api: AsyncClient) -> None:
    session = await _session(api)
    reply = await _say(api, session, "hello there friend")
    assert (await api.get(f"/v1/explain/{reply['user_message_id']}")).status_code == 404
    assert (await api.get("/v1/explain/msg_nope")).status_code == 404


async def test_explanations_need_the_token(running_container: Container) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=create_app(running_container)), base_url="http://t"
    ) as anonymous:
        assert (await anonymous.get("/v1/explain/msg_x")).status_code == 401
