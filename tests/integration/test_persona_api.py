"""Personality and the relationship for the inspector (PER-01, docs/16 §4.3)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from hedwig.api.app import create_app
from hedwig.core.token import TokenFile
from hedwig.wiring import Container


@pytest.fixture
async def api(running_container: Container) -> AsyncIterator[AsyncClient]:
    token = TokenFile(running_container.config.runtime.token_path).ensure()
    async with AsyncClient(
        transport=ASGITransport(app=create_app(running_container)),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        yield client


async def test_the_personality_is_visible_with_its_anchors(api: AsyncClient) -> None:
    body = (await api.get("/v1/mind/personality")).json()
    traits = {t["trait"]: t for t in body["traits"]}
    assert len(traits) == 10
    assert traits["warmth"]["floor"] <= traits["warmth"]["value"] <= traits["warmth"]["ceiling"]
    assert body["identity"]["name"] == "HEDWIG"


async def test_an_instruction_shows_in_the_history_and_can_be_undone(
    api: AsyncClient, running_container: Container
) -> None:
    before = {
        t["trait"]: t["value"] for t in (await api.get("/v1/mind/personality")).json()["traits"]
    }
    await running_container.brain.run_turn("Please be more concise from now on.", session_id="s")
    await running_container.bus.drain()

    history = (await api.get("/v1/mind/personality/history")).json()
    assert history, "the instruction left no record"
    assert (await api.post(f"/v1/mind/personality/revert/{history[0]['id']}")).json() == {
        "reverted": True
    }
    after = {
        t["trait"]: t["value"] for t in (await api.get("/v1/mind/personality")).json()["traits"]
    }
    assert after == before
    assert (await api.post(f"/v1/mind/personality/revert/{history[0]['id']}")).status_code == 404


async def test_the_relationship_and_personality_need_the_token(
    running_container: Container,
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=create_app(running_container)), base_url="http://t"
    ) as anonymous:
        for path in (
            "/v1/mind/personality",
            "/v1/mind/relationship",
            "/v1/mind/personality/history",
        ):
            assert (await anonymous.get(path)).status_code == 401, path
        # HEDWIG's own mood stays open (ADR-0022 §2).
        assert (await anonymous.get("/v1/mind/emotion")).status_code == 200


async def test_the_relationship_starts_as_strangers(api: AsyncClient) -> None:
    body = (await api.get("/v1/mind/relationship")).json()
    assert body["familiarity"] == 0 and body["interaction_count"] == 0
