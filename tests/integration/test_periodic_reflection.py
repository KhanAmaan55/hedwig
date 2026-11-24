"""T3 weekly review and T4 monthly identity report (REF-03, docs/12 §16)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from hedwig.api.app import create_app
from hedwig.core.clock import FakeClock
from hedwig.core.ids import new_id
from hedwig.core.ports.goals import GoalStatus
from hedwig.core.ports.memory import Episode, EpisodeKind
from hedwig.core.ports.personality import StyleObservation, Trait
from hedwig.core.token import TokenFile
from hedwig.core.types import MemoryId
from hedwig.llm.recorded import RecordedProvider, json_response
from hedwig.personality import PersonalityEngine
from hedwig.wiring import Container


async def _conversation(container: Container, text: str) -> MemoryId:
    """One conversation's trace: its exchange and its session summary."""
    now = container.clock.now()
    leaf = MemoryId(new_id("ep"))
    await container.memory.add_episode(
        Episode(id=leaf, occurred_at=now, title="chat", content=f"User: {text}")
    )
    await container.memory.add_episode(
        Episode(
            id=MemoryId(new_id("ep")),
            occurred_at=now,
            title="summary",
            content=f"The user talked about {text}.",
            kind=EpisodeKind.SESSION_SUMMARY,
        )
    )
    return leaf


async def test_the_week_is_summarised_once_and_linked_to_its_conversations(
    running_container: Container, llm_provider: RecordedProvider, clock: FakeClock
) -> None:
    first = await _conversation(running_container, "the Rust compiler")
    second = await _conversation(running_container, "learning Portuguese")
    clock.advance(days=4)  # Monday 5 January: last week holds both
    llm_provider.script(
        "reflect.week",
        json_response(
            {"title": "A busy week", "summary": "The user worked on Rust and Portuguese."}
        ),
    )

    review = await running_container.periodic.weekly(force=True)
    again = await running_container.periodic.weekly(force=True)

    assert review["status"] == "completed" and review["summary"] == "written"
    assert again["status"] == "already_done"
    summary = await running_container.memory.summary_for(review["period"])
    assert summary is not None and "Rust" in summary.content
    assert set(await running_container.memory.sources_of(summary.id)) == {first, second}


async def test_a_goal_untouched_for_four_weeks_is_blocked_with_a_note(
    running_container: Container, clock: FakeClock
) -> None:
    stale = await running_container.goals.create("Help the user finish the compiler")
    clock.advance(days=30)
    fresh = await running_container.goals.create("Help the user plan the garden")

    review = await running_container.periodic.weekly(force=True)

    assert review["goals_blocked"] == 1
    assert (await running_container.goals.get(stale.id)).status is GoalStatus.BLOCKED  # type: ignore[union-attr]
    assert (await running_container.goals.get(fresh.id)).status is GoalStatus.ACTIVE  # type: ignore[union-attr]
    notes = [n.text for n in await running_container.goals.notes(stale.id)]
    assert any("four weeks" in text for text in notes)


async def test_the_review_steps_aside_while_the_user_is_talking(
    running_container: Container, clock: FakeClock
) -> None:
    clock.advance(days=7)  # a new week; last week's review is still to do
    await running_container.brain.run_turn("Hello.", session_id="s")
    assert (await running_container.periodic.weekly())["status"] == "yielded"


async def test_the_monthly_report_tells_how_hedwig_changed_and_why(
    running_container: Container, llm_provider: RecordedProvider, clock: FakeClock
) -> None:
    engine = running_container.personality
    assert isinstance(engine, PersonalityEngine)
    await _conversation(running_container, "the compiler")
    await _conversation(running_container, "a trip to Porto")
    await engine.instruct(Trait.VERBOSITY, -1, phrase="be more concise")
    await engine.observe(
        [
            StyleObservation(
                trait=Trait.HUMOUR,
                direction=1,
                source_id="msg_1",
                quote="you're hilarious",
                observed_at=clock.now(),
            )
        ]
    )
    goal = await running_container.goals.create("Help the user ship the compiler")
    await running_container.goals.update(goal.id, status=GoalStatus.DONE, progress=1.0)
    clock.advance(days=32)  # 2 February: January is last month
    llm_provider.script(
        "reflect.month",
        json_response({"title": "January", "summary": "A month of compilers and travel plans."}),
    )

    made = await running_container.periodic.monthly(force=True)
    report = await running_container.periodic.report("2026-01")

    assert made["status"] == "completed"
    assert report["summary"] == "A month of compilers and travel plans."
    (change,) = report["personality"]
    assert change["trait"] == "verbosity" and "be more concise" in change["rationale"]
    assert report["goals"]["finished"] == ["Help the user ship the compiler"]
    assert report["reactions"] == [{"trait": "humour", "wanted": "more", "times": 1}]
    assert report["memory"]["episodes_formed"] >= 4
    assert report["seen_at"] is None

    await running_container.persona_control.revert(change["id"])  # type: ignore[union-attr]
    later = await running_container.periodic.report("2026-01")
    assert later["personality"][0]["reverted_at"], "a report shows what is true now"


async def test_without_a_model_the_report_waits_and_comes_later(
    running_container: Container, llm_provider: RecordedProvider, clock: FakeClock
) -> None:
    await _conversation(running_container, "the compiler")
    await _conversation(running_container, "Porto")
    clock.advance(days=32)
    llm_provider.available = False
    assert (await running_container.periodic.monthly(force=True))["status"] == "waiting_for_model"
    assert running_container.periodic.reports() == []

    llm_provider.available = True
    llm_provider.script("reflect.month", json_response({"title": "t", "summary": "A month."}))
    assert (await running_container.periodic.monthly(force=True))["status"] == "completed"


async def test_a_month_whose_conversations_went_unsummarised_is_summarised_from_its_exchanges(
    running_container: Container, llm_provider: RecordedProvider, clock: FakeClock
) -> None:
    """Found live: three conversations, one session summary — the month went unsummarised."""
    for text in ("the compiler", "Porto", "Portuguese lessons"):
        await running_container.memory.add_episode(
            Episode(
                id=MemoryId(new_id("ep")),
                occurred_at=clock.now(),
                title="chat",
                content=f"User: {text}",
            )
        )
    clock.advance(days=32)
    summary = "The user worked on the compiler, planned Porto and began Portuguese lessons."
    llm_provider.script("reflect.month", json_response({"title": "January", "summary": summary}))

    assert (await running_container.periodic.monthly(force=True))["status"] == "completed"
    assert (await running_container.periodic.report("2026-01"))["summary"] == summary


async def test_a_quiet_month_still_gets_a_report(
    running_container: Container, clock: FakeClock
) -> None:
    await running_container.brain.run_turn("Hello.", session_id="s")  # HEDWIG exists now
    clock.advance(days=32)
    made = await running_container.periodic.monthly(force=True)
    assert made["status"] == "completed"
    assert (await running_container.periodic.report("2026-01"))["summary"] is None


@pytest.fixture
async def api(running_container: Container) -> AsyncIterator[AsyncClient]:
    token = TokenFile(running_container.config.runtime.token_path).ensure()
    async with AsyncClient(
        transport=ASGITransport(app=create_app(running_container)),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        yield client


async def test_the_reports_api(
    running_container: Container, api: AsyncClient, clock: FakeClock
) -> None:
    await running_container.brain.run_turn("Hello.", session_id="s")
    clock.advance(days=32)
    # The scheduler may already have made it once the clock moved; either way it exists, once.
    made = (await api.post("/v1/reflection/monthly/run")).json()
    assert made["status"] in ("completed", "already_done")
    assert [r["month"] for r in (await api.get("/v1/reflection/reports")).json()["reports"]] == [
        "2026-01"
    ]
    assert (await api.get("/v1/reflection/reports/2026-01")).status_code == 200
    assert (await api.post("/v1/reflection/reports/2026-01/seen")).json() == {"seen": True}
    assert (await api.get("/v1/reflection/reports/2026-01")).json()["seen_at"]
    assert (await api.get("/v1/reflection/reports/1999-01")).status_code == 404


async def test_no_report_for_a_month_before_hedwig_knew_the_user(
    running_container: Container,
) -> None:
    """A new install's first start must not produce an empty report for last month."""
    assert running_container.periodic.reports() == []


def test_both_are_scheduled_after_the_nightly_run(container: Container) -> None:
    for name in ("reflection.weekly", "reflection.monthly"):
        assert container.scheduler.status(name).trigger.startswith("daily at 04:00")
