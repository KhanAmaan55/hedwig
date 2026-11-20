"""The user's hand on memory (MEM-02, docs/06 §16), end to end through the API."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from typer.testing import CliRunner

from hedwig import cli
from hedwig.api.app import create_app
from hedwig.client import HedwigClient
from hedwig.core.token import TokenFile
from hedwig.wiring import Container

TEA = "Please remember that my favourite tea is smoky lapsang souchong."


@pytest.fixture
async def api(running_container: Container) -> AsyncIterator[AsyncClient]:
    app = create_app(running_container)
    token = TokenFile(running_container.config.runtime.token_path).read()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        yield client


async def _remember_tea(container: Container) -> str:
    await container.brain.run_turn(TEA, session_id="sess_tea")
    await container.bus.drain()
    memories = await container.memory.browse(limit=5)
    assert memories, "the turn was not captured"
    return memories[0].id


async def _cited(container: Container) -> tuple[str, ...]:
    final = await container.brain.run_turn("What tea do I like?", session_id="sess_ask")
    await container.bus.drain()
    return final["response_context"].cited_memory_ids


async def test_memory_needs_the_token(running_container: Container) -> None:
    app = create_app(running_container)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        assert (await client.get("/v1/memory")).status_code == 401


async def test_what_hedwig_remembers_is_listed_and_shown(
    api: AsyncClient, running_container: Container
) -> None:
    memory_id = await _remember_tea(running_container)

    listed = (await api.get("/v1/memory")).json()["memories"]
    assert listed[0]["id"] == memory_id
    assert "lapsang" in listed[0]["text"]
    assert listed[0]["trust"] == "user"

    shown = (await api.get(f"/v1/memory/{memory_id}")).json()
    assert shown["kind"] == "episode" and shown["forgotten"] is False
    assert (await api.get("/v1/memory/ep_nope")).status_code == 404


async def test_search_ranks_like_recall_and_does_not_reinforce(
    api: AsyncClient, running_container: Container
) -> None:
    memory_id = await _remember_tea(running_container)
    before = running_container.database.query_one(
        "SELECT access_count FROM episode WHERE id = ?", (memory_id,)
    )

    result = (await api.get("/v1/memory/search", params={"q": "lapsang tea"})).json()

    assert result["hits"][0]["memory"]["id"] == memory_id
    assert result["hits"][0]["breakdown"], "no explanation of the score"
    after = running_container.database.query_one(
        "SELECT access_count FROM episode WHERE id = ?", (memory_id,)
    )
    assert before is not None and after is not None
    assert after["access_count"] == before["access_count"], "looking reinforced the memory"


async def test_forget_takes_it_out_of_the_next_turn_and_restore_brings_it_back(
    api: AsyncClient, running_container: Container
) -> None:
    memory_id = await _remember_tea(running_container)
    assert memory_id in await _cited(running_container)

    forgotten = (await api.post(f"/v1/memory/{memory_id}/forget")).json()
    assert forgotten["forgotten"] is True
    assert memory_id not in await _cited(running_container)
    # Still visible to the person who forgot it, so it can come back.
    assert (await api.get(f"/v1/memory/{memory_id}")).json()["forgotten"] is True

    restored = (await api.post(f"/v1/memory/{memory_id}/restore")).json()
    assert restored["forgotten"] is False
    assert memory_id in await _cited(running_container)


async def test_a_pinned_memory_refuses_to_be_forgotten_loudly(
    api: AsyncClient, running_container: Container
) -> None:
    memory_id = await _remember_tea(running_container)

    assert (await api.post(f"/v1/memory/{memory_id}/pin")).json()["pinned"] is True
    refused = await api.post(f"/v1/memory/{memory_id}/forget")
    assert refused.status_code == 409
    assert "Unpin" in refused.json()["error"]["message"]

    assert (await api.delete(f"/v1/memory/{memory_id}/pin")).json()["pinned"] is False
    assert (await api.post(f"/v1/memory/{memory_id}/forget")).json()["forgotten"] is True


async def test_forgetting_twice_is_harmless(api: AsyncClient, running_container: Container) -> None:
    memory_id = await _remember_tea(running_container)
    first = await api.post(f"/v1/memory/{memory_id}/forget")
    second = await api.post(f"/v1/memory/{memory_id}/forget")
    assert first.status_code == second.status_code == 200
    tombstones = running_container.database.query(
        "SELECT id FROM tombstone WHERE memory_id = ?", (memory_id,)
    )
    assert len(tombstones) == 1


# -- the CLI, over the same API ----------------------------------------------------------


@pytest.fixture
def http(container: Container) -> Iterator[TestClient]:
    with TestClient(create_app(container)) as client:
        yield client


def test_hedwig_memory_commands_go_through_the_api(
    http: TestClient, container: Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = TokenFile(container.config.runtime.token_path).read()
    assert token
    client = HedwigClient("http://testserver", token, http=http)
    client.send(client.open_session(), TEA)
    monkeypatch.setattr(cli, "_api", lambda url=None: client)
    runner = CliRunner()

    listed = runner.invoke(cli.app, ["memory", "list"])
    assert listed.exit_code == 0, listed.output
    memory_id = listed.stdout.split()[0]
    assert "lapsang" in listed.stdout

    assert "lapsang" in runner.invoke(cli.app, ["memory", "search", "lapsang"]).stdout
    assert runner.invoke(cli.app, ["memory", "forget", memory_id]).exit_code == 0
    assert "Nothing remembered" in runner.invoke(cli.app, ["memory", "list"]).stdout
    assert runner.invoke(cli.app, ["memory", "restore", memory_id]).exit_code == 0

    missing = runner.invoke(cli.app, ["memory", "show", "ep_nope"])
    assert missing.exit_code == 1
