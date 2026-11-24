"""Corrections in conversation (MEM-07, docs/06 §19): heard after the turn, decided by the user."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from hedwig.api.app import create_app
from hedwig.core.errors import ConflictError
from hedwig.core.ids import new_id
from hedwig.core.ports.memory import Belief, BeliefStatus
from hedwig.core.token import TokenFile
from hedwig.core.types import MemoryId, TrustTier
from hedwig.llm.recorded import RecordedProvider, json_response
from hedwig.wiring import Container

PORTO = "The user's sister Ana lives in Porto."
LISBON = "The user's sister Ana lives in Lisbon."


async def _believe(container: Container, statement: str) -> MemoryId:
    belief_id = MemoryId(new_id("bel"))
    await container.memory.add_belief(
        Belief(
            id=belief_id,
            statement=statement,
            valid_from=container.clock.now(),
            status=BeliefStatus.ACTIVE,
            confidence=0.7,
        )
    )
    await container.bus.drain()
    return belief_id


async def _say(container: Container, text: str, session: str = "s") -> None:
    await container.brain.run_turn(text, session_id=session)
    await container.bus.drain()


def _model_says(provider: RecordedProvider, belief: int, corrected: str) -> None:
    provider.script("extract.correction", json_response({"belief": belief, "corrected": corrected}))


def _asked(provider: RecordedProvider) -> int:
    return sum(call.purpose == "extract.correction" for call in provider.calls)


async def test_no_she_lives_in_lisbon_proposes_a_correction_the_user_accepts(
    running_container: Container, llm_provider: RecordedProvider
) -> None:
    pushed: list[tuple[str, dict[str, Any]]] = []

    async def screen(kind: str, payload: Mapping[str, Any]) -> None:
        pushed.append((kind, dict(payload)))

    running_container.presence.join(screen, session_id="s")
    porto = await _believe(running_container, PORTO)
    await _say(running_container, "Where does my sister Ana live?")
    _model_says(llm_provider, 1, LISBON)

    await _say(running_container, "No, she lives in Lisbon.")

    (waiting,) = running_container.corrections.proposals(session_id="s")
    assert (waiting["memory_id"], waiting["current_text"]) == (porto, PORTO)
    assert waiting["proposed_text"] == LISBON
    assert pushed == [("memory.correction.proposed", waiting)]
    belief = await running_container.memory.inspect(porto)
    assert isinstance(belief, Belief) and belief.status is BeliefStatus.ACTIVE, "nothing yet"

    accepted = await running_container.corrections.accept(waiting["id"])

    assert accepted["status"] == "accepted"
    new = await running_container.memory.inspect(MemoryId(accepted["replacement_id"]))
    assert isinstance(new, Belief) and new.statement == LISBON
    assert new.provenance.tier is TrustTier.USER
    old = await running_container.memory.inspect(porto)
    assert isinstance(old, Belief) and old.status is BeliefStatus.SUPERSEDED


async def test_an_ordinary_message_costs_no_model_call(
    running_container: Container, llm_provider: RecordedProvider
) -> None:
    await _believe(running_container, PORTO)
    await _say(running_container, "Where does my sister Ana live?")
    await _say(running_container, "No thanks, that's all.")
    await _say(running_container, "I love the train ride to Porto.")
    assert _asked(llm_provider) == 0
    assert running_container.corrections.proposals() == []


async def test_a_correction_with_nothing_recalled_costs_no_model_call(
    running_container: Container, llm_provider: RecordedProvider
) -> None:
    await _say(running_container, "No, she lives in Lisbon.")
    assert _asked(llm_provider) == 0


async def test_the_model_may_rephrase_but_never_add(
    running_container: Container, llm_provider: RecordedProvider
) -> None:
    await _believe(running_container, PORTO)
    await _say(running_container, "Where does my sister Ana live?")
    _model_says(llm_provider, 1, "The user's sister Ana lives in Madrid.")

    await _say(running_container, "No, she lives in Lisbon.")

    assert _asked(llm_provider) == 1
    assert running_container.corrections.proposals() == []


async def test_the_model_saying_none_or_a_wrong_number_proposes_nothing(
    running_container: Container, llm_provider: RecordedProvider
) -> None:
    await _believe(running_container, PORTO)
    await _say(running_container, "Where does my sister Ana live?")
    _model_says(llm_provider, 0, "")
    await _say(running_container, "No, she lives in Lisbon.")
    _model_says(llm_provider, 7, LISBON)
    await _say(running_container, "Actually she lives in Lisbon now.")
    assert running_container.corrections.proposals() == []


async def test_declined_changes_nothing_and_a_replaced_belief_goes_stale(
    running_container: Container, llm_provider: RecordedProvider
) -> None:
    porto = await _believe(running_container, PORTO)
    await _say(running_container, "Where does my sister Ana live?")
    _model_says(llm_provider, 1, LISBON)
    await _say(running_container, "No, she lives in Lisbon.")
    (first,) = running_container.corrections.proposals()

    declined = await running_container.corrections.decline(first["id"])
    assert declined["status"] == "declined"
    belief = await running_container.memory.inspect(porto)
    assert isinstance(belief, Belief) and belief.status is BeliefStatus.ACTIVE
    with pytest.raises(ConflictError):
        await running_container.corrections.accept(first["id"])

    _model_says(llm_provider, 1, LISBON)
    await _say(running_container, "No, really, she lives in Lisbon.")
    (second,) = running_container.corrections.proposals()
    await running_container.memory.correct(porto, text="The user's sister Ana lives in Faro.")
    with pytest.raises(ConflictError):
        await running_container.corrections.accept(second["id"])
    assert running_container.corrections.proposal(second["id"])["status"] == "stale"  # type: ignore[index]


async def test_the_same_correction_in_another_conversation_is_proposed_there_too(
    running_container: Container, llm_provider: RecordedProvider
) -> None:
    await _believe(running_container, PORTO)
    for session in ("a", "b"):
        await _say(running_container, "Where does my sister Ana live?", session)
        _model_says(llm_provider, 1, LISBON)
        await _say(running_container, "No, she lives in Lisbon.", session)
    _model_says(llm_provider, 1, LISBON)
    await _say(running_container, "No, she lives in Lisbon!", "b")  # same one, same place

    assert len(running_container.corrections.proposals(session_id="a")) == 1
    assert len(running_container.corrections.proposals(session_id="b")) == 1


async def test_a_proposal_settles_once_even_while_the_correction_is_under_way(
    running_container: Container, llm_provider: RecordedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    porto = await _believe(running_container, PORTO)
    await _say(running_container, "Where does my sister Ana live?")
    _model_says(llm_provider, 1, LISBON)
    await _say(running_container, "No, she lives in Lisbon.")
    (proposal,) = running_container.corrections.proposals()
    corrections = running_container.corrections
    gate = asyncio.Event()
    real = running_container.memory.correct
    applied: list[MemoryId] = []

    async def slow(memory_id: MemoryId, **kwargs: Any) -> MemoryId:
        await gate.wait()  # e.g. a full subscriber queue makes publishing wait
        applied.append(memory_id)
        return await real(memory_id, **kwargs)

    monkeypatch.setattr(running_container.memory, "correct", slow)
    first = asyncio.create_task(corrections.accept(proposal["id"]))
    await asyncio.sleep(0)

    with pytest.raises(ConflictError):
        await corrections.decline(proposal["id"])
    with pytest.raises(ConflictError):
        await corrections.accept(proposal["id"])
    gate.set()
    settled = await first

    assert settled["status"] == "accepted" and settled["replacement_id"]
    assert applied == [porto], "corrected once"


async def test_a_correction_that_fails_for_another_reason_can_be_answered_again(
    running_container: Container, llm_provider: RecordedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _believe(running_container, PORTO)
    await _say(running_container, "Where does my sister Ana live?")
    _model_says(llm_provider, 1, LISBON)
    await _say(running_container, "No, she lives in Lisbon.")
    (proposal,) = running_container.corrections.proposals()

    async def broken(memory_id: MemoryId, **kwargs: Any) -> MemoryId:
        raise RuntimeError("disk full")

    monkeypatch.setattr(running_container.memory, "correct", broken)
    with pytest.raises(RuntimeError):
        await running_container.corrections.accept(proposal["id"])

    assert running_container.corrections.proposal(proposal["id"])["status"] == "proposed"  # type: ignore[index]


@pytest.fixture
async def api(running_container: Container) -> AsyncIterator[AsyncClient]:
    token = TokenFile(running_container.config.runtime.token_path).ensure()
    async with AsyncClient(
        transport=ASGITransport(app=create_app(running_container)),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        yield client


async def test_the_corrections_api(
    running_container: Container, llm_provider: RecordedProvider, api: AsyncClient
) -> None:
    await _believe(running_container, PORTO)
    await _say(running_container, "Where does my sister Ana live?")
    _model_says(llm_provider, 1, LISBON)
    await _say(running_container, "No, she lives in Lisbon.")

    listed = (await api.get("/v1/corrections", params={"session_id": "s"})).json()["proposals"]
    assert [p["proposed_text"] for p in listed] == [LISBON]
    accepted = await api.post(f"/v1/corrections/{listed[0]['id']}/accept")
    assert accepted.status_code == 200 and accepted.json()["status"] == "accepted"
    assert (await api.post(f"/v1/corrections/{listed[0]['id']}/decline")).status_code == 409
    assert (await api.post("/v1/corrections/cor_nope/accept")).status_code == 404
    assert (await api.get("/v1/corrections")).json() == {"proposals": []}
    everything = (await api.get("/v1/corrections", params={"status": "all"})).json()
    assert [p["status"] for p in everything["proposals"]] == ["accepted"]
    assert (await api.get("/v1/corrections", headers={"Authorization": ""})).status_code == 401
