"""Correcting a memory (MEM-03, docs/06 §18): replaced, never overwritten, in the user's words."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from hedwig.api.app import create_app
from hedwig.core.errors import ConflictError, InvalidRequestError, NotFoundError
from hedwig.core.ids import new_id
from hedwig.core.ports.memory import Belief, BeliefStatus, Episode, ForgetReason
from hedwig.core.token import TokenFile
from hedwig.core.types import MemoryId, Provenance, TrustTier
from hedwig.wiring import Container


async def _belief(container: Container, statement: str, *, pinned: bool = False) -> MemoryId:
    belief_id = MemoryId(new_id("bel"))
    await container.memory.add_belief(
        Belief(
            id=belief_id,
            statement=statement,
            valid_from=container.clock.now(),
            confidence=0.5,
            pinned=pinned,
            provenance=Provenance(tier=TrustTier.SELF, source_ref="session:a"),
        )
    )
    return belief_id


async def _episode(container: Container, content: str, *, pinned: bool = False) -> MemoryId:
    episode_id = MemoryId(new_id("ep"))
    await container.memory.add_episode(
        Episode(
            id=episode_id,
            occurred_at=container.clock.now(),
            title="Trip",
            content=content,
            pinned=pinned,
        )
    )
    return episode_id


async def test_a_corrected_belief_is_replaced_in_the_users_words(
    running_container: Container,
) -> None:
    porto = await _belief(running_container, "The user's sister Ana lives in Porto.")

    lisbon = await running_container.memory.correct(
        porto, text="The user's sister Ana lives in Lisbon."
    )

    new = await running_container.memory.inspect(lisbon)
    assert isinstance(new, Belief)
    assert new.statement == "The user's sister Ana lives in Lisbon."
    assert new.provenance.tier is TrustTier.USER
    assert new.provenance.source_ref == f"correction:{porto}"
    assert new.status is BeliefStatus.ACTIVE and new.confidence == 0.9
    old = await running_container.memory.inspect(porto)
    assert isinstance(old, Belief)
    assert old.status is BeliefStatus.SUPERSEDED and old.superseded_by == lisbon
    assert old.statement.endswith("Porto."), "the wrong version is kept as history"

    working = await running_container.retrieval.search(["Where does Ana live?"])
    assert [item.text for item in working.items] == ["The user's sister Ana lives in Lisbon."]


async def test_a_confidence_alone_can_be_corrected(running_container: Container) -> None:
    tea = await _belief(running_container, "The user likes green tea.")
    replacement = await running_container.memory.correct(tea, confidence=0.3)
    new = await running_container.memory.inspect(replacement)
    assert isinstance(new, Belief)
    assert (new.statement, new.confidence) == ("The user likes green tea.", 0.3)


async def test_a_corrected_episode_is_tombstoned_restorably_and_the_pin_follows(
    running_container: Container,
) -> None:
    wrong = await _episode(running_container, "User: we fly to Lisbon in June.", pinned=True)

    right = await running_container.memory.correct(wrong, text="User: we fly to Lisbon in July.")

    new = await running_container.memory.inspect(right)
    old = await running_container.memory.inspect(wrong)
    assert isinstance(new, Episode) and isinstance(old, Episode)
    assert new.content.endswith("July.") and new.pinned
    assert new.occurred_at == old.occurred_at, "a correction does not move what happened"
    assert old.tombstoned_at is not None and not old.pinned
    reason = running_container.database.query_one(
        "SELECT reason, reversible FROM tombstone WHERE memory_id = ?", (wrong,)
    )
    assert reason is not None and (reason["reason"], reason["reversible"]) == (
        ForgetReason.CONTRADICTED.value,
        1,
    )
    assert list(await running_container.memory.sources_of(right)) == [wrong]


async def test_what_cannot_be_corrected(running_container: Container) -> None:
    memory = running_container.memory
    with pytest.raises(NotFoundError):
        await memory.correct(MemoryId("bel_nope"), text="anything")

    same = await _belief(running_container, "The user plays the guitar.")
    with pytest.raises(InvalidRequestError):
        await memory.correct(same, text="The user plays the guitar.")

    replaced = await _belief(running_container, "The user runs on Sundays.")
    newer = await memory.correct(replaced, text="The user runs on Saturdays.")
    with pytest.raises(ConflictError) as refused:
        await memory.correct(replaced, text="The user runs on Mondays.")
    assert refused.value.detail["replaced_by"] == newer

    forgotten = await _belief(running_container, "The user dislikes coriander.")
    await memory.tombstone(forgotten, ForgetReason.USER_REQUEST)
    with pytest.raises(ConflictError):
        await memory.correct(forgotten, text="The user loves coriander.")


async def test_a_correction_settles_what_the_nightly_run_asked_about(
    running_container: Container,
) -> None:
    porto = await _belief(running_container, "The user's sister Ana lives in Porto.")
    lisbon = await _belief(running_container, "The user's sister Ana lives in Lisbon.")
    running_container.database.execute(
        "INSERT INTO belief_judgement (pair, verdict, status, created_at) VALUES (?,?,?,?)",
        (f"{porto}|{lisbon}", "contradicts", "proposed", running_container.clock.now().isoformat()),
    )
    assert len(await running_container.nightly.disputes()) == 1

    await running_container.memory.correct(porto, text="The user's sister Ana lives in Faro.")

    assert await running_container.nightly.disputes() == []


async def test_erasing_the_wrong_version_keeps_the_correction(running_container: Container) -> None:
    porto = await _belief(running_container, "The user's sister Ana lives in Porto.")
    lisbon = await running_container.memory.correct(
        porto, text="The user's sister Ana lives in Lisbon."
    )
    preview = await running_container.purger.preview("memory", porto)
    assert preview["counts"] == {"beliefs": 1}
    await running_container.purger.purge("memory", porto, preview["confirm"])
    assert await running_container.memory.inspect(lisbon) is not None


@pytest.fixture
async def api(running_container: Container) -> AsyncIterator[AsyncClient]:
    token = TokenFile(running_container.config.runtime.token_path).ensure()
    async with AsyncClient(
        transport=ASGITransport(app=create_app(running_container)),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        yield client


async def test_the_correction_api(running_container: Container, api: AsyncClient) -> None:
    porto = await _belief(running_container, "The user's sister Ana lives in Porto.")

    corrected = await api.patch(
        f"/v1/memory/{porto}", json={"text": "The user's sister Ana lives in Lisbon."}
    )
    assert corrected.status_code == 200
    body = corrected.json()
    assert body["trust"] == "user" and body["status"] == "active" and body["id"] != porto
    old = (await api.get(f"/v1/memory/{porto}")).json()
    assert old["status"] == "superseded" and old["replaced_by"] == body["id"]

    again = await api.patch(f"/v1/memory/{porto}", json={"text": "Faro"})
    assert again.status_code == 409
    assert (await api.patch(f"/v1/memory/{body['id']}", json={})).status_code == 400
    assert (await api.patch("/v1/memory/bel_nope", json={"text": "x"})).status_code == 404
    anonymous = await api.patch(
        f"/v1/memory/{body['id']}", json={"text": "x"}, headers={"Authorization": ""}
    )
    assert anonymous.status_code == 401
