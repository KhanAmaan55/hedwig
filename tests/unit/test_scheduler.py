"""The task scheduler and its triggers.

Triggers are pure functions of time and history, which is the whole reason a simulated year
of nightly jobs costs milliseconds instead of a year (docs/20 §3.1).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from hedwig.core.clock import FakeClock
from hedwig.core.errors import ConflictError, NotFoundError
from hedwig.core.ports import (
    HealthStatus,
    Priority,
    RunStatus,
    TaskContext,
    TaskHandler,
    TaskResult,
    TaskSpec,
    TriggerKind,
)
from hedwig.core.scheduler import (
    AsyncTaskScheduler,
    DailyTrigger,
    IdleTrigger,
    IntervalTrigger,
    ManualTrigger,
    TaskAlreadyRunningError,
    WeeklyTrigger,
)
from hedwig.core.store import Database

NOON = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


# =========================================================================
# Triggers — pure, no scheduler needed
# =========================================================================


def test_interval_trigger_fires_immediately_when_there_is_no_history() -> None:
    trigger = IntervalTrigger(seconds=60)
    assert trigger.next_due(now=NOON, last_success=None) == NOON


def test_interval_trigger_measures_from_the_last_success() -> None:
    trigger = IntervalTrigger(seconds=60)
    due = trigger.next_due(now=NOON, last_success=NOON)
    assert due == NOON + timedelta(seconds=60)


def test_daily_trigger_moves_to_tomorrow_once_today_has_run() -> None:
    trigger = DailyTrigger(hour=3)
    first = trigger.next_due(now=NOON, last_success=None)
    assert first is not None

    after = trigger.next_due(now=NOON, last_success=NOON)
    assert after is not None
    assert after > NOON


def test_daily_trigger_rejects_an_impossible_time() -> None:
    with pytest.raises(ValueError, match="invalid time"):
        DailyTrigger(hour=25)


def test_weekly_trigger_lands_on_the_requested_weekday() -> None:
    trigger = WeeklyTrigger(weekday=6, hour=3)  # Sunday
    due = trigger.next_due(now=NOON, last_success=None)

    assert due is not None
    assert due.astimezone().weekday() == 6


def test_weekly_trigger_rejects_an_impossible_weekday() -> None:
    with pytest.raises(ValueError, match="weekday"):
        WeeklyTrigger(weekday=9)


def test_idle_trigger_asks_about_activity_not_the_calendar() -> None:
    trigger = IdleTrigger(after_seconds=600)

    assert trigger.is_idle_due(idle_seconds=599) is False
    assert trigger.is_idle_due(idle_seconds=600) is True


def test_idle_trigger_rate_limits_repeat_runs() -> None:
    trigger = IdleTrigger(after_seconds=60, min_interval_seconds=3600)
    due = trigger.next_due(now=NOON, last_success=NOON)
    assert due == NOON + timedelta(seconds=3600)


def test_manual_trigger_never_fires_on_its_own() -> None:
    assert ManualTrigger().next_due(now=NOON, last_success=None) is None
    assert ManualTrigger().kind is TriggerKind.MANUAL


def test_triggers_describe_themselves_for_the_ui() -> None:
    assert IntervalTrigger(seconds=30).describe() == "every 30s"
    assert DailyTrigger(hour=3, minute=5).describe().startswith("daily at 03:05")
    assert "Sun" in WeeklyTrigger(weekday=6).describe()
    assert IdleTrigger(after_seconds=600).describe() == "when idle for 600s"


# =========================================================================
# Scheduler
# =========================================================================


@pytest.fixture
def scheduler(database: Database, clock: FakeClock) -> AsyncTaskScheduler:
    return AsyncTaskScheduler(database, clock=clock, tick_seconds=0.01)


def _spec(name: str, handler: TaskHandler, **kwargs: Any) -> TaskSpec:
    kwargs.setdefault("trigger", ManualTrigger())
    return TaskSpec(name=name, handler=handler, **kwargs)


# -- registration ----------------------------------------------------------


def test_duplicate_registration_is_refused(scheduler: AsyncTaskScheduler) -> None:
    async def noop(_: TaskContext) -> None:
        return None

    scheduler.register(_spec("nightly", noop))
    with pytest.raises(ConflictError):
        scheduler.register(_spec("nightly", noop))


def test_status_of_an_unknown_task_raises(scheduler: AsyncTaskScheduler) -> None:
    with pytest.raises(NotFoundError):
        scheduler.status("never-registered")


# -- running ---------------------------------------------------------------


async def test_a_triggered_task_runs_and_records_its_outcome(
    scheduler: AsyncTaskScheduler,
) -> None:
    calls: list[TaskContext] = []

    async def handler(context: TaskContext) -> TaskResult:
        calls.append(context)
        return TaskResult(stats={"items": 3})

    scheduler.register(_spec("consolidate", handler))
    await scheduler.trigger("consolidate", reason="test")
    await _settle()

    history = await scheduler.history("consolidate")
    assert len(calls) == 1
    assert calls[0].trigger is TriggerKind.MANUAL
    assert history[0].status is RunStatus.COMPLETED
    assert history[0].stats == {"items": 3}


async def test_a_failing_task_is_recorded_not_raised(
    scheduler: AsyncTaskScheduler,
) -> None:
    async def explodes(_: TaskContext) -> None:
        raise RuntimeError("task is broken")

    scheduler.register(_spec("broken", explodes))
    await scheduler.trigger("broken")
    await _settle()

    history = await scheduler.history("broken")
    assert history[0].status is RunStatus.FAILED
    assert "task is broken" in (history[0].error or "")
    assert scheduler.status("broken").failure_count == 1


async def test_a_task_that_overruns_is_cancelled(database: Database, clock: FakeClock) -> None:
    """Bounded runtime: otherwise a stuck task runs until the machine is rebooted."""
    scheduler = AsyncTaskScheduler(database, clock=clock, tick_seconds=0.01)

    async def forever(_: TaskContext) -> None:
        await asyncio.sleep(30)

    scheduler.register(_spec("stuck", forever, max_runtime_seconds=0.01))
    await scheduler.trigger("stuck")
    await _settle(cycles=40)

    history = await scheduler.history("stuck")
    assert history[0].status is RunStatus.FAILED
    assert "max_runtime_seconds" in (history[0].error or "")


async def test_one_run_per_task(scheduler: AsyncTaskScheduler) -> None:
    """Enforced by a unique partial index, not by hoping."""
    release = asyncio.Event()

    async def slow(_: TaskContext) -> None:
        await release.wait()

    scheduler.register(_spec("exclusive", slow))
    await scheduler.trigger("exclusive")
    await asyncio.sleep(0)

    with pytest.raises(TaskAlreadyRunningError):
        await scheduler.trigger("exclusive")

    release.set()
    await _settle()


async def test_cancel_stops_a_running_task(scheduler: AsyncTaskScheduler) -> None:
    started = asyncio.Event()

    async def slow(_: TaskContext) -> None:
        started.set()
        await asyncio.sleep(30)

    scheduler.register(_spec("cancellable", slow))
    await scheduler.trigger("cancellable")
    await started.wait()

    assert await scheduler.cancel("cancellable") is True
    await _settle()
    assert await scheduler.cancel("cancellable") is False


# -- cursors ---------------------------------------------------------------


async def test_a_cursor_is_handed_back_to_the_next_run(
    scheduler: AsyncTaskScheduler,
) -> None:
    """Resumability: an interrupted run continues rather than restarting (docs/12 §5.1)."""
    seen: list[str | None] = []

    async def handler(context: TaskContext) -> TaskResult:
        seen.append(context.cursor)
        return TaskResult(cursor="page-2", complete=False)

    scheduler.register(_spec("paged", handler))
    await scheduler.trigger("paged")
    await _settle()
    await scheduler.trigger("paged")
    await _settle()

    assert seen == [None, "page-2"]


async def test_partial_progress_does_not_count_as_success(
    scheduler: AsyncTaskScheduler,
) -> None:
    """Leaving `last_success_at` alone is what makes the next tick pick the work back up."""

    async def partial(_: TaskContext) -> TaskResult:
        return TaskResult(cursor="more", complete=False)

    scheduler.register(_spec("partial", partial))
    await scheduler.trigger("partial")
    await _settle()

    assert scheduler.status("partial").last_success_at is None


# -- pause and resume ------------------------------------------------------


async def test_pause_survives_a_restart(database: Database, clock: FakeClock) -> None:
    async def noop(_: TaskContext) -> None:
        return None

    first = AsyncTaskScheduler(database, clock=clock)
    first.register(_spec("nightly", noop))
    first.pause("nightly")

    second = AsyncTaskScheduler(database, clock=clock)
    second.register(_spec("nightly", noop))

    assert second.status("nightly").paused is True
    second.resume("nightly")
    assert second.status("nightly").paused is False


# -- the loop --------------------------------------------------------------


async def test_a_due_task_runs_without_being_triggered(
    database: Database, clock: FakeClock
) -> None:
    scheduler = AsyncTaskScheduler(database, clock=clock, tick_seconds=0.001)
    ran = asyncio.Event()

    async def handler(_: TaskContext) -> None:
        ran.set()

    scheduler.register(_spec("interval", handler, trigger=IntervalTrigger(seconds=1)))
    await scheduler.start()
    try:
        await asyncio.wait_for(ran.wait(), timeout=2)
    finally:
        await scheduler.stop()


async def test_a_paused_task_does_not_run_on_a_tick(database: Database, clock: FakeClock) -> None:
    scheduler = AsyncTaskScheduler(database, clock=clock, tick_seconds=0.001)
    runs = 0

    async def handler(_: TaskContext) -> None:
        nonlocal runs
        runs += 1

    scheduler.register(_spec("interval", handler, trigger=IntervalTrigger(seconds=1)))
    scheduler.pause("interval")
    await scheduler.start()
    await asyncio.sleep(0.05)
    await scheduler.stop()

    assert runs == 0


async def test_idle_tasks_wait_for_the_user_to_be_away(
    database: Database, clock: FakeClock
) -> None:
    scheduler = AsyncTaskScheduler(database, clock=clock, tick_seconds=0.001)
    runs = 0

    async def handler(_: TaskContext) -> None:
        nonlocal runs
        runs += 1

    scheduler.register(_spec("explore", handler, trigger=IdleTrigger(after_seconds=600)))
    scheduler.notify_activity()
    await scheduler.start()
    await asyncio.sleep(0.03)
    await scheduler.stop()

    assert runs == 0
    assert scheduler.idle_seconds >= 0


async def test_orphaned_runs_are_reaped_on_start(database: Database, clock: FakeClock) -> None:
    """A process that died mid-run must not leave a task that can never start again."""
    database.execute(
        "INSERT INTO task_run (id, task_name, trigger, status, queued_at, correlation_id) "
        "VALUES ('run_orphan', 'nightly', 'schedule', 'running', ?, 'job_x')",
        (clock.now().isoformat(timespec="milliseconds"),),
    )

    scheduler = AsyncTaskScheduler(database, clock=clock, tick_seconds=10)
    await scheduler.start()
    await scheduler.stop()

    row = database.query_one("SELECT status FROM task_run WHERE id = 'run_orphan'")
    assert row is not None
    assert row["status"] == "preempted"


# -- introspection ---------------------------------------------------------


async def test_status_reports_the_schedule(scheduler: AsyncTaskScheduler) -> None:
    async def noop(_: TaskContext) -> None:
        return None

    scheduler.register(
        _spec(
            "nightly",
            noop,
            trigger=DailyTrigger(hour=3),
            description="nightly consolidation",
            priority=Priority.BACKGROUND,
        )
    )

    status = scheduler.status("nightly")
    assert status.description == "nightly consolidation"
    assert status.trigger.startswith("daily at 03:00")
    assert status.next_due_at is not None
    assert len(scheduler.all_status()) == 1


async def test_health_is_ok_with_no_tasks(scheduler: AsyncTaskScheduler) -> None:
    health = await scheduler.health()
    assert health.status is HealthStatus.OK
    assert health.detail["tasks"] == 0


async def _settle(cycles: int = 20) -> None:
    """Let scheduled tasks and their bookkeeping finish."""
    for _ in range(cycles):
        await asyncio.sleep(0.005)
