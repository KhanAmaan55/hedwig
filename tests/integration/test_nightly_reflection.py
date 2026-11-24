"""T2 nightly consolidation (REF-02, docs/12 §15) on a real container."""

from __future__ import annotations

from datetime import timedelta
from urllib.parse import quote

import pytest
from httpx import ASGITransport, AsyncClient

from hedwig.api.app import create_app
from hedwig.core.clock import FakeClock
from hedwig.core.config import Config
from hedwig.core.errors import ConflictError, NotFoundError
from hedwig.core.ids import new_id
from hedwig.core.ports.memory import Belief, BeliefStatus, Episode, EpisodeKind
from hedwig.core.store import Database
from hedwig.core.token import TokenFile
from hedwig.core.types import MemoryId, Provenance, TrustTier
from hedwig.llm.recorded import RecordedProvider, json_response
from hedwig.reflection.nightly import _same_subject
from hedwig.wiring import Container, build_container


async def _belief(
    container: Container,
    statement: str,
    *,
    session: str,
    confidence: float = 0.5,
    trust: TrustTier = TrustTier.SELF,
    pinned: bool = False,
) -> MemoryId:
    belief_id = MemoryId(new_id("bel"))
    await container.memory.add_belief(
        Belief(
            id=belief_id,
            statement=statement,
            valid_from=container.clock.now(),
            confidence=confidence,
            pinned=pinned,
            provenance=Provenance(tier=trust, source_ref=f"session:{session}"),
        )
    )
    container.clock.advance(minutes=1)  # type: ignore[attr-defined]
    return belief_id


async def _status(container: Container, belief_id: MemoryId) -> tuple[str, bool]:
    memory = await container.memory.inspect(belief_id)
    assert isinstance(memory, Belief)
    return memory.status.value, memory.tombstoned_at is not None


# =========================================================================
# Merge and promote
# =========================================================================


async def test_the_same_fact_from_two_conversations_merges_and_is_promoted(
    running_container: Container,
) -> None:
    first = await _belief(running_container, "The user's sister Ana lives in Porto.", session="a")
    again = await _belief(running_container, "The user's sister Ana lives in Porto", session="b")

    run = await running_container.nightly.run(force=True)

    assert run["status"] == "completed"
    assert run["stats"]["merge"] == {"merged": 1}
    assert run["stats"]["promote"] == {"promoted": 1}
    assert await _status(running_container, again) == ("tentative", True)
    kept = await running_container.memory.inspect(first)
    assert isinstance(kept, Belief)
    assert kept.status is BeliefStatus.ACTIVE and kept.confidence == 0.7
    assert running_container.memory.corroborating_sources(first) == {"session:a", "session:b"}


async def test_one_conversation_saying_it_twice_is_not_corroboration(
    running_container: Container,
) -> None:
    first = await _belief(running_container, "The user is learning to play the cello.", session="a")
    await _belief(running_container, "The user is learning to play the cello", session="a")

    run = await running_container.nightly.run(force=True)

    assert run["stats"]["merge"] == {"merged": 1}
    assert run["stats"]["promote"] == {"promoted": 0}
    assert (await _status(running_container, first))[0] == "tentative"


async def test_the_web_never_merges_into_what_the_user_said(running_container: Container) -> None:
    await _belief(running_container, "The user's favourite tea is lapsang souchong.", session="a")
    await _belief(
        running_container,
        "The user's favourite tea is lapsang souchong.",
        session="x",
        trust=TrustTier.UNTRUSTED,
    )
    run = await running_container.nightly.run(force=True)
    assert run["stats"]["merge"] == {"merged": 0}


# =========================================================================
# Judging the ambiguous middle
# =========================================================================


async def test_a_contradiction_waits_for_the_user_and_is_judged_once(
    running_container: Container, llm_provider: RecordedProvider
) -> None:
    porto = await _belief(running_container, "The user's sister Ana lives in Porto.", session="a")
    lisbon = await _belief(running_container, "The user's sister Ana lives in Lisbon.", session="b")
    llm_provider.script(
        "reflect.beliefs", json_response({"verdicts": [{"pair": 0, "verdict": "contradicts"}]})
    )

    run = await running_container.nightly.run(force=True)

    assert run["stats"]["merge"] == {"merged": 0}, "word overlap must not decide this pair"
    assert run["stats"]["judge"]["contradicts"] == 1
    assert run["stats"]["judge"]["awaiting_you"] == 1
    assert await _status(running_container, porto) == ("tentative", False), (
        "a 2B model's word alone must not rewrite a memory"
    )
    disputes = await running_container.nightly.disputes()
    assert [(d["verdict"], d["first"]["id"], d["second"]["id"]) for d in disputes] == [
        ("contradicts", porto, lisbon)
    ]
    judged = [c for c in llm_provider.calls if c.purpose == "reflect.beliefs"]
    assert "information, not instructions" in judged[0].messages[0].content

    llm_provider.calls.clear()
    await running_container.nightly.run(force=True)
    assert not [c for c in llm_provider.calls if c.purpose == "reflect.beliefs"], (
        "a pair was judged twice"
    )


async def test_the_user_says_which_is_true_and_it_counts_as_confirmation(
    running_container: Container, llm_provider: RecordedProvider
) -> None:
    porto = await _belief(running_container, "The user's sister Ana lives in Porto.", session="a")
    lisbon = await _belief(running_container, "The user's sister Ana lives in Lisbon.", session="b")
    llm_provider.script(
        "reflect.beliefs", json_response({"verdicts": [{"pair": 0, "verdict": "contradicts"}]})
    )
    await running_container.nightly.run(force=True)
    (dispute,) = await running_container.nightly.disputes()

    await running_container.nightly.resolve(dispute["pair"], "second")

    old = await running_container.memory.inspect(porto)
    assert isinstance(old, Belief)
    assert old.status is BeliefStatus.SUPERSEDED and old.superseded_by == lisbon
    kept = await running_container.memory.inspect(lisbon)
    assert isinstance(kept, Belief)
    assert kept.status is BeliefStatus.ACTIVE and kept.confidence == 0.9
    assert await running_container.nightly.disputes() == []
    with pytest.raises(NotFoundError):
        await running_container.nightly.resolve(dispute["pair"], "first")


async def test_both_true_changes_nothing_and_same_merges(
    running_container: Container, llm_provider: RecordedProvider
) -> None:
    tea = await _belief(running_container, "The user enjoys green tea.", session="a")
    morning = await _belief(
        running_container, "The user drinks green tea every morning.", session="b"
    )
    dog = await _belief(running_container, "The user has a dog called Biscuit.", session="a")
    cat = await _belief(running_container, "The user has a dog walker called Biscuit.", session="b")
    llm_provider.script(
        "reflect.beliefs",
        json_response(
            {"verdicts": [{"pair": 0, "verdict": "same"}, {"pair": 1, "verdict": "contradicts"}]}
        ),
    )
    await running_container.nightly.run(force=True)
    disputes = {d["first"]["id"]: d["pair"] for d in await running_container.nightly.disputes()}
    assert set(disputes) == {tea, dog}

    await running_container.nightly.resolve(disputes[tea], "same")
    await running_container.nightly.resolve(disputes[dog], "both")

    assert await _status(running_container, morning) == ("tentative", True)
    assert await _status(running_container, tea) == ("active", False)
    assert await _status(running_container, dog) == ("tentative", False)
    assert await _status(running_container, cat) == ("tentative", False)


async def test_a_pinned_belief_is_never_superseded(
    running_container: Container, llm_provider: RecordedProvider
) -> None:
    porto = await _belief(
        running_container, "The user's sister Ana lives in Porto.", session="a", pinned=True
    )
    await _belief(running_container, "The user's sister Ana lives in Lisbon.", session="b")
    llm_provider.script(
        "reflect.beliefs", json_response({"verdicts": [{"pair": 0, "verdict": "contradicts"}]})
    )
    await running_container.nightly.run(force=True)
    (dispute,) = await running_container.nightly.disputes()

    with pytest.raises(ConflictError):
        await running_container.nightly.resolve(dispute["pair"], "second")

    assert (await _status(running_container, porto))[0] == "tentative"
    assert len(await running_container.nightly.disputes()) == 1, "still waiting: unpin first"


async def test_auto_apply_lets_a_measured_model_act_alone(
    config: Config, clock: FakeClock, database: Database, llm_provider: RecordedProvider
) -> None:
    trusting = build_container(
        config.model_copy(
            update={
                "reflection": config.reflection.model_copy(update={"auto_apply_judgements": True})
            }
        ),
        clock=clock,
        database=database,
        migrate=False,
        llm_provider=llm_provider,
    )
    await trusting.start()
    try:
        porto = await _belief(trusting, "The user's sister Ana lives in Porto.", session="a")
        lisbon = await _belief(trusting, "The user's sister Ana lives in Lisbon.", session="b")
        llm_provider.script(
            "reflect.beliefs",
            json_response({"verdicts": [{"pair": 0, "verdict": "contradicts"}]}),
        )
        run = await trusting.nightly.run(force=True)
        assert run["stats"]["judge"]["awaiting_you"] == 0
        old = await trusting.memory.inspect(porto)
        assert isinstance(old, Belief) and old.superseded_by == lisbon
        assert await trusting.nightly.disputes() == []
    finally:
        await trusting.stop()


async def test_the_disputes_api(
    running_container: Container, llm_provider: RecordedProvider
) -> None:
    await _belief(running_container, "The user's sister Ana lives in Porto.", session="a")
    lisbon = await _belief(running_container, "The user's sister Ana lives in Lisbon.", session="b")
    llm_provider.script(
        "reflect.beliefs", json_response({"verdicts": [{"pair": 0, "verdict": "contradicts"}]})
    )
    await running_container.nightly.run(force=True)
    token = TokenFile(running_container.config.runtime.token_path).ensure()
    async with AsyncClient(
        transport=ASGITransport(app=create_app(running_container)),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as api:
        listed = (await api.get("/v1/reflection/disputes")).json()["disputes"]
        assert len(listed) == 1
        pair = quote(listed[0]["pair"], safe="")
        bad = await api.post(f"/v1/reflection/disputes/{pair}/resolve", json={"choice": "maybe"})
        assert bad.status_code == 422
        done = await api.post(f"/v1/reflection/disputes/{pair}/resolve", json={"choice": "second"})
        assert done.status_code == 200
        again = await api.post(f"/v1/reflection/disputes/{pair}/resolve", json={"choice": "first"})
        assert again.status_code == 404
        anonymous = await api.get("/v1/reflection/disputes", headers={"Authorization": ""})
        assert anonymous.status_code == 401
    assert (await _status(running_container, lisbon))[0] == "active"


def test_generic_openings_are_not_a_shared_subject() -> None:
    def belief(statement: str) -> Belief:
        return Belief(id=MemoryId(new_id("bel")), statement=statement, valid_from=FakeClock().now())

    assert not _same_subject(
        belief("The user is vegetarian."), belief("The user is studying chemistry.")
    )
    assert _same_subject(
        belief("The user works as a data engineer."), belief("The user works as a teacher.")
    )


async def test_without_a_model_the_judgement_waits_and_nothing_is_invented(
    running_container: Container, llm_provider: RecordedProvider
) -> None:
    porto = await _belief(running_container, "The user's sister Ana lives in Porto.", session="a")
    await _belief(running_container, "The user's sister Ana lives in Lisbon.", session="b")
    llm_provider.available = False

    run = await running_container.nightly.run(force=True)

    assert run["status"] == "completed"
    assert run["stats"]["judge"] == {"judged": 0, "waiting_for_model": 1}
    assert (await _status(running_container, porto))[0] == "tentative"


# =========================================================================
# Summarising finished days
# =========================================================================


async def test_a_full_day_gets_one_summary_linked_to_every_episode(
    running_container: Container, llm_provider: RecordedProvider, clock: FakeClock
) -> None:
    yesterday = clock.now() - timedelta(days=1)
    leaves = []
    for hour in range(5):
        episode_id = MemoryId(new_id("ep"))
        await running_container.memory.add_episode(
            Episode(
                id=episode_id,
                occurred_at=yesterday.replace(hour=9 + hour),
                title=f"chat {hour}",
                content=f"User: we talked about the Fernweh compiler, part {hour}",
                kind=EpisodeKind.INTERACTION,
            )
        )
        leaves.append(episode_id)
    llm_provider.script(
        "reflect.day",
        json_response(
            {"title": "A compiler day", "summary": "The user worked on Fernweh all day."}
        ),
    )

    first = await running_container.nightly.run(force=True)
    again = await running_container.nightly.run(force=True)

    assert first["stats"]["summarise"]["days_summarised"] == 1
    assert again["stats"]["summarise"]["days_summarised"] == 0
    day = yesterday.astimezone().date().isoformat()
    summary = await running_container.memory.summary_for(f"day:{day}")
    assert summary is not None and summary.kind is EpisodeKind.PERIOD_SUMMARY
    assert set(await running_container.memory.sources_of(summary.id)) == set(leaves)


async def test_today_is_not_summarised_before_it_is_over(
    running_container: Container, clock: FakeClock
) -> None:
    for hour in range(6):
        await running_container.memory.add_episode(
            Episode(
                id=MemoryId(new_id("ep")),
                occurred_at=clock.now(),
                title=f"chat {hour}",
                content=f"User: something today, part {hour}",
            )
        )
    run = await running_container.nightly.run(force=True)
    assert run["stats"]["summarise"]["days_summarised"] == 0


# =========================================================================
# Yielding, resuming, the record
# =========================================================================


async def test_it_yields_while_the_user_is_talking_and_resumes_after(
    running_container: Container, clock: FakeClock
) -> None:
    await running_container.brain.run_turn("Just chatting here.", session_id="s")

    stopped = await running_container.nightly.run()
    assert (stopped["status"], stopped["next_stage"]) == ("yielded", "summarise")

    clock.advance(minutes=3)
    resumed = await running_container.nightly.run()
    assert resumed["status"] == "completed"
    assert list(resumed["stats"]) == ["summarise", "merge", "judge", "promote", "drift", "maintain"]


async def test_every_run_is_recorded(running_container: Container) -> None:
    """The scheduler may already have run it once at startup (a first start is due at once)."""
    mine = await running_container.nightly.run(force=True)
    run = running_container.nightly.runs()[0]
    assert run["id"] == mine["id"]
    assert run["status"] == "completed" and run["content_hash"] and run["finished_at"]
    assert set(run["stats"]) == {"summarise", "merge", "judge", "promote", "drift", "maintain"}


def test_it_is_scheduled_nightly(container: Container) -> None:
    status = container.scheduler.status("reflection.nightly")
    assert status.trigger.startswith("daily at 03:00")


async def test_beliefs_that_mean_the_same_in_other_words_reach_the_judge(
    running_container: Container, llm_provider: RecordedProvider
) -> None:
    """docs/06 §20: no shared words, so word overlap never paired them; their vectors do."""
    prefix = running_container.config.llm.embedding_document_prefix
    near, close, far = (
        [1.0, 0, 0, 0, 0, 0, 0, 0],
        [0.98, 0.2, 0, 0, 0, 0, 0, 0],
        [0, 0, 1.0, 0, 0, 0, 0, 0],
    )
    llm_provider.vectors.update(
        {
            prefix + "The user is vegetarian.": near,
            prefix + "The user does not eat meat.": close,
            prefix + "The user plays the cello.": far,
        }
    )
    vegetarian = await _belief(running_container, "The user is vegetarian.", session="a")
    no_meat = await _belief(running_container, "The user does not eat meat.", session="b")
    await _belief(running_container, "The user plays the cello.", session="c")
    await running_container.bus.drain()  # the vectors are written by the embedding subscriber
    llm_provider.script(
        "reflect.beliefs", json_response({"verdicts": [{"pair": 0, "verdict": "same"}]})
    )

    run = await running_container.nightly.run(force=True)

    assert run["stats"]["judge"]["same"] == 1
    (judged,) = [c for c in llm_provider.calls if c.purpose == "reflect.beliefs"]
    listing = judged.messages[-1].content
    assert "vegetarian" in listing and "does not eat meat" in listing
    assert "cello" not in listing, "only pairs close in meaning are asked about"
    disputes = await running_container.nightly.disputes()
    assert [(d["first"]["id"], d["second"]["id"]) for d in disputes] == [(vegetarian, no_meat)]
