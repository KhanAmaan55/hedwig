"""Task scheduler (docs/17 §5-6).

A tick loop over registered tasks. Everything interesting is about *not* misbehaving:

* **Coalesced catch-up.** A laptop asleep for three nights produces one run over the
  combined window, not three. This is the common case, not the edge case.
* **One run per task.** Enforced by a unique partial index in SQLite, so two triggers firing
  at once cannot both start.
* **Resumable cursors.** A run persists where it stopped; the next one continues there.
* **Bounded runtime.** Every task has a deadline, after which it is cancelled and recorded
  as failed rather than running until the machine is rebooted.
* **Idle awareness.** Idle-triggered tasks wait for the user to actually be away.

Priority and preemption against interactive work belong to the resource governor, which is
a later milestone; the scheduler already carries the `Priority` on each spec so that
governor has something to read.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from hedwig.core.context import correlation_scope
from hedwig.core.errors import ConflictError, NotFoundError
from hedwig.core.ids import new_id
from hedwig.core.logging import fields, get_logger
from hedwig.core.ports import (
    Clock,
    EventBus,
    Health,
    HealthStatus,
    RunStatus,
    TaskContext,
    TaskResult,
    TaskRun,
    TaskSpec,
    TaskStatus,
    TriggerKind,
)
from hedwig.core.scheduler.triggers import IdleTrigger
from hedwig.core.store import Database

logger = get_logger(__name__)

DEFAULT_TICK_SECONDS = 5.0


class TaskAlreadyRunningError(ConflictError):
    code = "task_already_running"


class AsyncTaskScheduler:
    """The `TaskScheduler` implementation."""

    def __init__(
        self,
        database: Database,
        *,
        clock: Clock,
        bus: EventBus | None = None,
        tick_seconds: float = DEFAULT_TICK_SECONDS,
        max_concurrent: int = 1,
    ) -> None:
        self._database = database
        self._clock = clock
        self._bus = bus
        self._tick_seconds = tick_seconds
        self._semaphore = asyncio.Semaphore(max_concurrent)

        self._specs: dict[str, TaskSpec] = {}
        self._running: dict[str, asyncio.Task[None]] = {}
        self._paused: set[str] = set()
        self._loop_task: asyncio.Task[None] | None = None
        self._stopping = False
        self._last_activity = clock.monotonic()

    # -- registration ------------------------------------------------------

    def register(self, spec: TaskSpec) -> None:
        if spec.name in self._specs:
            raise ConflictError(f"task {spec.name!r} is already registered", task=spec.name)
        self._specs[spec.name] = spec
        self._database.execute(
            "INSERT INTO task_state (task_name, paused, updated_at) VALUES (?, 0, ?) "
            "ON CONFLICT (task_name) DO NOTHING",
            (spec.name, self._now()),
        )
        if self._is_paused(spec.name):
            self._paused.add(spec.name)
        logger.debug(
            "task registered",
            extra=fields(task=spec.name, trigger=spec.trigger.describe(), enabled=spec.enabled),
        )

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        if self._loop_task is not None:
            return
        self._stopping = False
        await self._reap_orphans()
        self._loop_task = asyncio.create_task(self._tick_loop(), name="scheduler")
        logger.info("scheduler started", extra=fields(tasks=len(self._specs)))

    async def stop(self) -> None:
        self._stopping = True
        if self._loop_task is not None:
            self._loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._loop_task
            self._loop_task = None

        for name, task in list(self._running.items()):
            task.cancel()
            # Shutdown has to finish. A task that fails on its way out is logged by its own
            # handler; it must not be allowed to strand the tasks queued behind it.
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
            logger.info("task cancelled during shutdown", extra=fields(task=name))
        self._running.clear()
        logger.info("scheduler stopped")

    async def _reap_orphans(self) -> None:
        """Close runs left `running` by a process that died (docs/17 §3 step 7)."""
        orphans = self._database.execute(
            "UPDATE task_run SET status = 'preempted', finished_at = ?, "
            "error = 'process exited while running' "
            "WHERE status IN ('queued', 'running')",
            (self._now(),),
        )
        if orphans:
            logger.warning("reaped orphaned task runs", extra=fields(count=orphans))

    # -- the loop ----------------------------------------------------------

    async def _tick_loop(self) -> None:
        while not self._stopping:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # a scheduler that dies silently is the failure to avoid
                logger.exception("scheduler tick failed")
            await self._clock.sleep(self._tick_seconds)

    async def _tick(self) -> None:
        now = self._clock.now()
        for name, spec in self._specs.items():
            if not spec.enabled or name in self._paused or name in self._running:
                continue
            if self._is_due(spec, now):
                await self._launch(spec, spec.trigger.kind)

    def _is_due(self, spec: TaskSpec, now: datetime) -> bool:
        last_success = self._last_success(spec.name)

        if isinstance(spec.trigger, IdleTrigger) and not spec.trigger.is_idle_due(
            idle_seconds=self.idle_seconds
        ):
            return False

        due = spec.trigger.next_due(now=now, last_success=last_success)
        if due is None:
            return False
        if due > now:
            return False
        # No history and catch-up disabled: wait for the next genuine occurrence rather
        # than firing immediately on first boot.
        return not (last_success is None and not spec.catch_up)

    # -- running -----------------------------------------------------------

    async def trigger(self, name: str, *, reason: str = "manual") -> TaskRun:
        spec = self._spec(name)
        run = await self._launch(spec, TriggerKind.MANUAL, reason=reason)
        if run is None:
            raise TaskAlreadyRunningError(f"task {name!r} is already running", task=name)
        return run

    async def _launch(
        self, spec: TaskSpec, trigger: TriggerKind, *, reason: str = ""
    ) -> TaskRun | None:
        run_id = new_id("run")
        correlation_id = new_id("job")
        queued_at = self._clock.now()

        try:
            # The unique partial index is the guard: two triggers racing cannot both win.
            self._database.execute(
                "INSERT INTO task_run (id, task_name, trigger, status, queued_at, "
                "correlation_id) VALUES (?, ?, ?, 'queued', ?, ?)",
                (
                    run_id,
                    spec.name,
                    trigger.value,
                    queued_at.isoformat(timespec="milliseconds"),
                    correlation_id,
                ),
            )
        except Exception:
            logger.debug("task already active, skipping launch", extra=fields(task=spec.name))
            return None

        cursor = self._last_cursor(spec.name)
        run = TaskRun(
            id=run_id,
            task_name=spec.name,
            trigger=trigger,
            status=RunStatus.QUEUED,
            queued_at=queued_at,
            started_at=None,
            finished_at=None,
            cursor=cursor,
            stats={},
            error=None,
            correlation_id=correlation_id,
        )

        task = asyncio.create_task(
            self._execute(spec, run, reason=reason), name=f"task:{spec.name}"
        )
        self._running[spec.name] = task
        task.add_done_callback(lambda _: self._running.pop(spec.name, None))
        return run

    async def _execute(self, spec: TaskSpec, run: TaskRun, *, reason: str) -> None:
        async with self._semaphore:
            started_at = self._clock.now()
            self._database.execute(
                "UPDATE task_run SET status = 'running', started_at = ? WHERE id = ?",
                (started_at.isoformat(timespec="milliseconds"), run.id),
            )
            await self._announce(
                "scheduler.task.started", {"task": spec.name, "run_id": run.id, "reason": reason}
            )
            logger.info(
                "task started",
                extra=fields(task=spec.name, run=run.id, trigger=run.trigger.value, reason=reason),
            )

            context = TaskContext(
                run_id=run.id,
                task_name=spec.name,
                trigger=run.trigger,
                correlation_id=run.correlation_id,
                started_at=started_at,
                cursor=run.cursor,
            )

            status = RunStatus.COMPLETED
            error: str | None = None
            result: TaskResult | None = None

            try:
                with correlation_scope(run.correlation_id):
                    result = await asyncio.wait_for(
                        spec.handler(context), timeout=spec.max_runtime_seconds
                    )
            except TimeoutError:
                status, error = (
                    RunStatus.FAILED,
                    (f"exceeded max_runtime_seconds={spec.max_runtime_seconds:g}"),
                )
                logger.error("task timed out", extra=fields(task=spec.name, run=run.id))
            except asyncio.CancelledError:
                status, error = RunStatus.CANCELLED, "cancelled"
                self._finish(spec, run, status, None, error)
                raise
            except Exception as exc:
                status, error = RunStatus.FAILED, f"{type(exc).__name__}: {exc}"
                logger.exception("task failed", extra=fields(task=spec.name, run=run.id))

            self._finish(spec, run, status, result, error)

    def _finish(
        self,
        spec: TaskSpec,
        run: TaskRun,
        status: RunStatus,
        result: TaskResult | None,
        error: str | None,
    ) -> None:
        finished_at = self._now()
        cursor = result.cursor if result else None
        stats = dict(result.stats) if result else {}
        complete = result.complete if result else status is RunStatus.COMPLETED

        self._database.execute(
            "UPDATE task_run SET status = ?, finished_at = ?, cursor = ?, stats = ?, error = ? "
            "WHERE id = ?",
            (status.value, finished_at, cursor, json.dumps(stats, default=str), error, run.id),
        )

        succeeded = status is RunStatus.COMPLETED
        self._database.execute(
            """
            UPDATE task_state SET
                last_run_id = ?,
                run_count = run_count + 1,
                failure_count = failure_count + ?,
                last_success_at = CASE WHEN ? THEN ? ELSE last_success_at END,
                updated_at = ?
            WHERE task_name = ?
            """,
            (
                run.id,
                0 if succeeded else 1,
                # Partial progress does not count as success: leaving `last_success_at`
                # alone is what makes the next tick pick the work straight back up.
                1 if (succeeded and complete) else 0,
                finished_at,
                finished_at,
                spec.name,
            ),
        )

        logger.info(
            "task finished",
            extra=fields(
                task=spec.name, run=run.id, status=status.value, complete=complete, error=error
            ),
        )
        if self._bus is not None:
            asyncio.create_task(  # noqa: RUF006 - fire and forget; failure is logged by the bus
                self._announce(
                    "scheduler.task.completed",
                    {"task": spec.name, "run_id": run.id, "status": status.value, "stats": stats},
                )
            )

    async def cancel(self, name: str) -> bool:
        task = self._running.get(name)
        if task is None:
            return False
        task.cancel()
        return True

    # -- control -----------------------------------------------------------

    def pause(self, name: str) -> None:
        self._spec(name)
        self._paused.add(name)
        self._database.execute(
            "UPDATE task_state SET paused = 1, updated_at = ? WHERE task_name = ?",
            (self._now(), name),
        )
        logger.info("task paused", extra=fields(task=name))

    def resume(self, name: str) -> None:
        self._spec(name)
        self._paused.discard(name)
        self._database.execute(
            "UPDATE task_state SET paused = 0, updated_at = ? WHERE task_name = ?",
            (self._now(), name),
        )
        logger.info("task resumed", extra=fields(task=name))

    def notify_activity(self) -> None:
        self._last_activity = self._clock.monotonic()

    @property
    def idle_seconds(self) -> float:
        return max(0.0, self._clock.monotonic() - self._last_activity)

    # -- introspection -----------------------------------------------------

    def status(self, name: str) -> TaskStatus:
        spec = self._spec(name)
        last_success = self._last_success(name)
        row = self._database.query_one(
            "SELECT run_count, failure_count FROM task_state WHERE task_name = ?", (name,)
        )
        return TaskStatus(
            name=name,
            description=spec.description,
            trigger=spec.trigger.describe(),
            enabled=spec.enabled,
            paused=name in self._paused,
            running=name in self._running,
            last_success_at=last_success,
            next_due_at=spec.trigger.next_due(now=self._clock.now(), last_success=last_success),
            run_count=int(row["run_count"]) if row else 0,
            failure_count=int(row["failure_count"]) if row else 0,
        )

    def all_status(self) -> Sequence[TaskStatus]:
        return tuple(self.status(name) for name in self._specs)

    async def history(self, name: str, *, limit: int = 20) -> Sequence[TaskRun]:
        rows = self._database.query(
            "SELECT * FROM task_run WHERE task_name = ? ORDER BY queued_at DESC LIMIT ?",
            (name, limit),
        )
        return tuple(_to_run(row) for row in rows)

    async def health(self) -> Health:
        failing = [status for status in self.all_status() if status.failure_count > 0]
        stalled = [
            status
            for status in self.all_status()
            if status.next_due_at is not None
            and status.last_success_at is None
            and status.run_count > 2
        ]
        status = HealthStatus.DEGRADED if stalled else HealthStatus.OK
        return Health(
            status=status,
            message=f"{len(stalled)} tasks never succeeded" if stalled else "",
            detail={
                "tasks": len(self._specs),
                "running": list(self._running),
                "paused": sorted(self._paused),
                "with_failures": [status.name for status in failing],
                "idle_seconds": round(self.idle_seconds, 1),
            },
        )

    # -- helpers -----------------------------------------------------------

    def _spec(self, name: str) -> TaskSpec:
        spec = self._specs.get(name)
        if spec is None:
            raise NotFoundError(f"task {name!r} is not registered", task=name)
        return spec

    def _now(self) -> str:
        return self._clock.now().isoformat(timespec="milliseconds")

    def _is_paused(self, name: str) -> bool:
        row = self._database.query_one("SELECT paused FROM task_state WHERE task_name = ?", (name,))
        return bool(row["paused"]) if row else False

    def _last_success(self, name: str) -> datetime | None:
        row = self._database.query_one(
            "SELECT last_success_at FROM task_state WHERE task_name = ?", (name,)
        )
        if row is None or row["last_success_at"] is None:
            return None
        return datetime.fromisoformat(row["last_success_at"])

    def _last_cursor(self, name: str) -> str | None:
        row = self._database.query_one(
            "SELECT cursor FROM task_run WHERE task_name = ? AND cursor IS NOT NULL "
            "ORDER BY queued_at DESC LIMIT 1",
            (name,),
        )
        return str(row["cursor"]) if row and row["cursor"] else None

    async def _announce(self, type_: str, payload: Mapping[str, Any]) -> None:
        if self._bus is None:
            return
        try:
            await self._bus.emit(type_, payload, source="core.scheduler")
        except Exception:  # telemetry must never break the task it describes
            logger.exception("failed to publish scheduler event", extra=fields(type=type_))


def _to_run(row: Any) -> TaskRun:
    return TaskRun(
        id=str(row["id"]),
        task_name=str(row["task_name"]),
        trigger=TriggerKind(row["trigger"]),
        status=RunStatus(row["status"]),
        queued_at=datetime.fromisoformat(row["queued_at"]),
        started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
        finished_at=datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None,
        cursor=row["cursor"],
        stats=json.loads(row["stats"]) if row["stats"] else {},
        error=row["error"],
        correlation_id=str(row["correlation_id"]),
    )
