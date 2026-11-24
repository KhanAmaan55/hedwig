"""Traces and metrics (OBS-01, docs/19 §13): where the time went, and is HEDWIG healthy."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from hedwig.api.app import create_app
from hedwig.core.clock import FakeClock
from hedwig.core.errors import NotFoundError
from hedwig.core.token import TokenFile
from hedwig.wiring import Container

SECRET = "my bank PIN is 4471 and I live at 9 Elm Road"
STAGES = {"ingest", "guard", "snapshot", "recall", "compose", "finalize"}


async def _turn(container: Container, text: str = "Hello there.") -> str:
    final = await container.brain.run_turn(text, session_id="s")
    await container.bus.drain()
    return str(final["correlation_id"])


async def test_a_turn_is_traced_stage_by_stage_with_what_it_set_off(
    running_container: Container,
) -> None:
    correlation = await _turn(running_container)

    trace = running_container.traces.trace(correlation)

    assert trace["root"]["kind"] == "turn" and trace["root"]["status"] == "completed"
    stages = [e["name"] for e in trace["timeline"] if e["type"] == "stage"]
    assert set(stages) >= STAGES
    assert stages.index("ingest") < stages.index("recall") < stages.index("compose")
    handlers = {e["name"]: e for e in trace["timeline"] if e["type"] == "handler"}
    assert handlers, "the work a turn sets off is part of its trace"
    assert all(h["event"] and h["status"] == "ok" for h in handlers.values())
    (compose,) = [
        e for e in trace["timeline"] if e["type"] == "model" and e["name"] == "compose.reply"
    ]
    assert compose["tier"] == "conversational"
    assert any(e["name"] == "conversation.turn.completed" for e in trace["timeline"])
    offsets = [e["offset_ms"] for e in trace["timeline"]]
    assert offsets == sorted(offsets) and offsets[0] >= 0
    assert trace["summary"]["model_calls"] >= 1 and trace["summary"]["failures"] == 0


async def test_spans_never_hold_what_was_said(running_container: Container) -> None:
    await _turn(running_container, SECRET)
    rows = running_container.database.query("SELECT * FROM span")
    assert rows
    dumped = " ".join(str(value) for row in rows for value in tuple(row))
    assert "4471" not in dumped and "Elm Road" not in dumped


async def test_a_handler_that_fails_is_marked_in_the_trace(running_container: Container) -> None:
    async def broken(event: object) -> None:
        raise RuntimeError("boom")

    running_container.bus.subscribe("conversation.turn.completed", broken, name="test.broken")
    correlation = await _turn(running_container)

    trace = running_container.traces.trace(correlation)
    (failed, *_) = [e for e in trace["timeline"] if e["name"] == "test.broken"]
    assert failed["status"] == "failed"
    assert trace["summary"]["failures"] >= 1


async def test_a_scheduled_run_is_traced_too(running_container: Container) -> None:
    run = await running_container.scheduler.trigger("telemetry.prune_spans")
    for _ in range(200):  # the scheduler picks it up on its next tick
        listed = running_container.traces.recent()
        if any(t["correlation_id"] == run.correlation_id for t in listed):
            break
        await asyncio.sleep(0.01)
    (mine,) = [t for t in listed if t["correlation_id"] == run.correlation_id]
    assert mine["kind"] == "job" and mine["name"] == "telemetry.prune_spans"
    assert running_container.traces.trace(run.correlation_id)["root"]["kind"] == "job"


async def test_a_job_trace_never_carries_its_error_text(running_container: Container) -> None:
    running_container.database.execute(
        "INSERT INTO task_run (id, task_name, trigger, status, correlation_id, queued_at, "
        "started_at, finished_at, error) VALUES ('run_x', 'memory.reembed', 'manual', 'failed', "
        "'job_x', '2026-01-01T09:00:00+00:00', '2026-01-01T09:00:00+00:00', "
        "'2026-01-01T09:00:01+00:00', ?)",
        (f"ValueError: could not embed {SECRET!r}",),
    )

    root = running_container.traces.trace("job_x")["root"]

    assert root["status"] == "failed" and "error" not in root
    assert "4471" not in str(root)


async def test_a_tool_that_timed_out_counts_as_a_failure(running_container: Container) -> None:
    correlation = await _turn(running_container)
    running_container.database.execute(
        "INSERT INTO tool_call (id, tool_name, args, status, duration_ms, correlation_id, "
        "created_at) VALUES ('tc_x', 'web.search', '{}', 'timeout', 30000, ?, ?)",
        (correlation, running_container.clock.now().isoformat()),
    )

    assert running_container.traces.trace(correlation)["summary"]["failures"] == 1


async def test_the_latest_turn_is_found_however_many_jobs_ran_since(
    running_container: Container,
) -> None:
    correlation = await _turn(running_container)
    for index in range(20):
        running_container.database.execute(
            "INSERT INTO task_run (id, task_name, trigger, status, correlation_id, "
            "queued_at, started_at) VALUES (?, 'x', 'manual', 'completed', ?, '2099-01-01', "
            "'2099-01-01')",
            (f"run_{index}", f"job_{index}"),
        )

    (latest,) = running_container.traces.recent(limit=1, kind="turn")
    assert latest["correlation_id"] == correlation


async def test_an_unknown_id_is_not_found(running_container: Container) -> None:
    with pytest.raises(NotFoundError):
        running_container.traces.trace("turn_nope")


async def test_old_spans_are_pruned(running_container: Container, clock: FakeClock) -> None:
    await _turn(running_container)
    clock.advance(days=31)
    from hedwig.core.tracing import SpanStore

    assert SpanStore(running_container.database, clock=clock).prune() > 0
    assert (
        running_container.database.query(
            "SELECT * FROM span WHERE started_at < ?",
            ((clock.now() - timedelta(days=30)).isoformat(),),
        )
        == []
    )


# -- the API --------------------------------------------------------------------------------

DESIGNED = (
    # docs/19 §6, as built (§13 lists the ones that are not).
    "hedwig_uptime_seconds",
    "hedwig_turns_total",
    "hedwig_turn_duration_seconds",
    "hedwig_reply_first_token_seconds",
    "hedwig_llm_calls_recorded_total",
    "hedwig_llm_requests_total",
    "hedwig_memories_total",
    "hedwig_memories_forgotten_total",
    "hedwig_beliefs_tentative_ratio",
    "hedwig_retrieval_dropped_ratio",
    "hedwig_personality_drift_applied_total",
    "hedwig_personality_drift_rejected_total",
    "hedwig_emotion_dimension",
    "hedwig_reflection_runs_total",
    "hedwig_reflection_lag_hours",
    "hedwig_curiosity_findings_total",
    "hedwig_curiosity_promotion_ratio",
    "hedwig_curiosity_dismissal_ratio",
    "hedwig_injection_quarantined_total",
    "hedwig_initiative_offers_total",
    "hedwig_initiative_dismissed_total",
    "hedwig_corrections_total",
    "hedwig_corrections_pending",
    "hedwig_bus_dead_letters_total",
    "hedwig_bus_queue_depth",
)


@pytest.fixture
async def api(running_container: Container) -> AsyncIterator[AsyncClient]:
    token = TokenFile(running_container.config.runtime.token_path).ensure()
    async with AsyncClient(
        transport=ASGITransport(app=create_app(running_container)),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        yield client


async def test_every_designed_metric_is_exposed(
    running_container: Container, api: AsyncClient
) -> None:
    await _turn(running_container)

    response = await api.get("/v1/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain; version=0.0.4")
    text = response.text
    for name in DESIGNED:
        assert f"# TYPE {name} " in text, f"{name} was designed and is not exposed"
    assert 'hedwig_turns_total{status="completed"} 1' in text
    assert "hedwig_turn_duration_seconds_count 1" in text
    families = [line.split()[2] for line in text.splitlines() if line.startswith("# TYPE")]
    assert len(families) == len(set(families)), "each family once"
    assert (await api.get("/v1/metrics", headers={"Authorization": ""})).status_code == 401


async def test_the_traces_api(running_container: Container, api: AsyncClient) -> None:
    correlation = await _turn(running_container)

    listed = (await api.get("/v1/traces")).json()["traces"]
    assert any(t["correlation_id"] == correlation and t["kind"] == "turn" for t in listed)
    trace = (await api.get(f"/v1/traces/{correlation}")).json()
    assert trace["root"]["kind"] == "turn"
    assert (await api.get("/v1/traces/turn_nope")).status_code == 404
    assert (await api.get("/v1/traces", headers={"Authorization": ""})).status_code == 401


async def test_counters_never_go_down_when_something_waiting_is_settled(
    running_container: Container, api: AsyncClient
) -> None:
    """A settled correction leaves the pending gauge and joins the counter; nothing decreases."""
    now = running_container.clock.now().isoformat()
    for proposal in ("cor_a", "cor_b"):
        running_container.database.execute(
            "INSERT INTO correction_proposal (id, memory_id, proposed_text, created_at) "
            "VALUES (?, 'bel_x', 'The user is vegan.', ?)",
            (proposal, now),
        )
    before = (await api.get("/v1/metrics")).text
    running_container.database.execute(
        "UPDATE correction_proposal SET status = 'declined' WHERE id = 'cor_a'"
    )
    after = (await api.get("/v1/metrics")).text

    assert "hedwig_corrections_pending 2" in before and "hedwig_corrections_pending 1" in after
    assert 'hedwig_corrections_total{status="declined"} 1' in after
    assert 'status="proposed"' not in before + after
    # An accept under way (claimed, not yet applied) is not counted until it settles.
    running_container.database.execute(
        "UPDATE correction_proposal SET status = 'accepted' WHERE id = 'cor_b'"
    )
    claimed = (await api.get("/v1/metrics")).text
    assert 'hedwig_corrections_total{status="accepted"}' not in claimed
    assert 'hedwig_turns_total{status="running"}' not in before
