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
    await running_container.brain.run_turn("Hello.", session_id="s")
    clock.advance(days=7)  # a new week; last week's review is still to do
    await running_container.brain.run_turn("Hello again.", session_id="s")
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
    llm_provider.script(
        "reflect.month",
        json_response({"title": "January", "summary": "The user worked on the compiler."}),
    )
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


async def test_a_month_left_waiting_is_caught_up_after_the_calendar_moves_on(
    container: Container, llm_provider: RecordedProvider, clock: FakeClock
) -> None:
    """January waits for the model into March: January is still reported, then February."""
    await _conversation(container, "the compiler")
    await _conversation(container, "Porto")
    clock.advance(days=32)  # 2 February
    llm_provider.available = False
    assert (await container.periodic.monthly(force=True))["status"] == "waiting_for_model"

    clock.advance(days=28)  # 2 March: last month is February now
    llm_provider.available = True
    llm_provider.script(
        "reflect.month",
        json_response({"title": "January", "summary": "The user worked on the compiler."}),
    )
    january = await container.periodic.monthly(force=True)
    february = await container.periodic.monthly(force=True)

    assert (january["period"], january["status"]) == ("month:2026-01", "completed")
    assert (february["period"], february["status"]) == ("month:2026-02", "completed")
    assert [r["month"] for r in container.periodic.reports()] == ["2026-02", "2026-01"]
    assert (await container.periodic.monthly(force=True))["status"] == "already_done"


async def test_a_summary_stored_without_its_links_is_linked_when_the_run_is_retried(
    container: Container, llm_provider: RecordedProvider, clock: FakeClock
) -> None:
    first = await _conversation(container, "the Rust compiler")
    second = await _conversation(container, "learning Portuguese")
    clock.advance(days=4)
    llm_provider.script(
        "reflect.week", json_response({"title": "A week", "summary": "Rust and Portuguese."})
    )
    review = await container.periodic.weekly(force=True)
    summary = await container.memory.summary_for(review["period"])
    assert summary is not None
    # The process stopped after the summary was stored, before its links and its run record.
    container.database.execute("DELETE FROM memory_derivation WHERE derived_id = ?", (summary.id,))
    container.database.execute("DELETE FROM reflection_run WHERE tier = 'T3'")

    retried = await container.periodic.weekly(force=True)

    assert retried["summary"] == "already_there" and retried["status"] == "completed"
    assert set(await container.memory.sources_of(summary.id)) == {first, second}


async def test_two_weeks_summarised_in_the_same_words_each_keep_their_summary(
    container: Container, llm_provider: RecordedProvider, clock: FakeClock
) -> None:
    same = {"title": "A quiet week", "summary": "The user practised Portuguese with HEDWIG."}
    await _conversation(container, "Portuguese verbs")
    await _conversation(container, "Portuguese nouns")
    clock.advance(days=4)
    llm_provider.script("reflect.week", json_response(same))
    one = await container.periodic.weekly(force=True)
    await _conversation(container, "Portuguese verbs again")
    await _conversation(container, "Portuguese nouns again")
    clock.advance(days=7)
    llm_provider.script("reflect.week", json_response(same))
    two = await container.periodic.weekly(force=True)

    assert one["summary"] == two["summary"] == "written"
    assert one["period"] != two["period"] and one["summary_id"] != two["summary_id"]


async def test_a_conversation_erased_during_the_model_call_stops_the_summary(
    container: Container,
    llm_provider: RecordedProvider,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A summary must not keep words from a conversation erased while it was being written."""
    from hedwig.reflection import periodic

    erased = await _conversation(container, "the Rust compiler")
    await _conversation(container, "learning Portuguese")
    clock.advance(days=4)
    from hedwig.reflection.common import ask_structured as real

    async def erased_meanwhile(*args: object, **kwargs: object) -> object:
        container.database.execute("DELETE FROM episode WHERE id = ?", (erased,))  # a purge
        return {"title": "A week", "summary": "The user worked on Rust and Portuguese."}

    monkeypatch.setattr(periodic, "ask_structured", erased_meanwhile)
    review = await container.periodic.weekly(force=True)

    assert review["summary"] == "sources_erased" and review["status"] != "completed"
    assert await container.memory.summary_for(review["period"]) is None
    monkeypatch.setattr(periodic, "ask_structured", real)
    llm_provider.script(
        "reflect.week",
        json_response({"title": "A week", "summary": "The user learned Portuguese."}),
    )
    retried = await container.periodic.weekly(force=True)
    assert retried["period"] == review["period"] and retried["summary"] == "written"


async def test_each_months_report_counts_only_its_own_month_in_the_users_time_zone(
    container: Container, clock: FakeClock
) -> None:
    """Midnight on 1 February in Kolkata is 18:30 UTC on 31 January."""
    from hedwig.reflection.periodic import PeriodicReflection

    reflection = container.periodic
    kolkata = PeriodicReflection(
        database=container.database,
        store=container.memory,
        capture=reflection._capture,
        goals=container.goals,
        gateway=reflection._gateway,
        history=reflection._history,
        clock=clock,
        timezone="Asia/Kolkata",
    )
    clock.advance_to("2026-01-31T18:29:59+00:00")  # 23:59:59 on 31 January in Kolkata
    await _conversation(container, "the last evening of January")
    clock.advance(seconds=2)  # 00:00:01 on 1 February in Kolkata
    await _conversation(container, "the first minute of February")
    for month in ("2026-01", "2026-02"):
        container.database.execute(
            "INSERT INTO identity_report (month, summary_id, created_at) VALUES (?, NULL, ?)",
            (month, clock.now().isoformat()),
        )

    january = (await kolkata.report("2026-01"))["memory"]
    february = (await kolkata.report("2026-02"))["memory"]

    assert january["episodes_formed"] == 2  # the exchange and its summary
    assert february["episodes_formed"] == 2


def test_report_boundaries_compare_as_utc() -> None:
    """Stored timestamps are UTC; a local midnight must be compared as the same instant."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from hedwig.reflection.periodic import PeriodicReflection

    local_midnight = datetime(2026, 2, 1, tzinfo=ZoneInfo("Asia/Kolkata"))
    assert PeriodicReflection._iso(local_midnight) == "2026-01-31T18:30:00.000+00:00"


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
