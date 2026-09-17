"""The `TaskScheduler` port (docs/17 §5-6).

Background work: reflection tiers, curiosity exploration, maintenance. This port is the
generic mechanism; the tasks themselves are defined by the layers that own them, so the
scheduler never learns what reflection is.

Three properties the design turns on:

* **Time is injected.** Every trigger asks the `Clock`, so a test can run a simulated year
  of nightly jobs in milliseconds (docs/20 §3.1).
* **Runs are resumable.** A run persists a cursor, so a task interrupted by a laptop lid or
  a preemption continues rather than restarting (docs/12 §5.1).
* **Missed runs coalesce.** A machine asleep at 03:00 for three nights produces one
  catch-up run over the combined window, not three sequential ones (docs/17 §5).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class TriggerKind(StrEnum):
    SCHEDULE = "schedule"
    IDLE = "idle"
    MANUAL = "manual"
    EVENT = "event"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PREEMPTED = "preempted"


class Priority(StrEnum):
    """Interactive work always wins (docs/17 §7)."""

    INTERACTIVE = "interactive"
    BACKGROUND = "background"
    MAINTENANCE = "maintenance"


@dataclass(frozen=True, slots=True)
class TaskContext:
    """Handed to a task when it runs."""

    run_id: str
    task_name: str
    trigger: TriggerKind
    correlation_id: str
    started_at: datetime
    cursor: str | None
    """Where the previous interrupted run stopped, or `None` for a fresh start."""


@dataclass(frozen=True, slots=True)
class TaskResult:
    """What a task reports back."""

    cursor: str | None = None
    """Persisted so an interrupted run resumes here. `None` means the work completed."""
    stats: Mapping[str, Any] = field(default_factory=dict)
    complete: bool = True
    """`False` marks partial progress: the run succeeded but there is more to do."""


TaskHandler = Callable[[TaskContext], Awaitable[TaskResult | None]]


@runtime_checkable
class Trigger(Protocol):
    """Decides when a task is due.

    Implementations are pure functions of time and history, which is what makes the
    schedule testable without waiting for it.
    """

    @property
    def kind(self) -> TriggerKind: ...

    def next_due(self, *, now: datetime, last_success: datetime | None) -> datetime | None:
        """When this task should next run, or `None` if it never runs on its own."""
        ...

    def describe(self) -> str: ...


@dataclass(frozen=True, slots=True)
class TaskSpec:
    """Everything the scheduler needs to know about a task."""

    name: str
    handler: TaskHandler
    trigger: Trigger
    priority: Priority = Priority.BACKGROUND
    max_runtime_seconds: float = 900.0
    description: str = ""
    catch_up: bool = True
    """Run once on start if the schedule was missed while the process was down."""
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class TaskRun:
    id: str
    task_name: str
    trigger: TriggerKind
    status: RunStatus
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    cursor: str | None
    stats: Mapping[str, Any]
    error: str | None
    correlation_id: str


@dataclass(frozen=True, slots=True)
class TaskStatus:
    name: str
    description: str
    trigger: str
    enabled: bool
    paused: bool
    running: bool
    last_success_at: datetime | None
    next_due_at: datetime | None
    run_count: int
    failure_count: int


@runtime_checkable
class TaskScheduler(Protocol):
    """Runs registered tasks when their triggers say so."""

    def register(self, spec: TaskSpec) -> None:
        """Add a task. Raises `ConflictError` on a duplicate name."""
        ...

    async def trigger(self, name: str, *, reason: str = "manual") -> TaskRun:
        """Run a task now, regardless of its schedule.

        Raises `ConflictError` if it is already running: one run per task, enforced by a
        unique index rather than by hoping.
        """
        ...

    async def cancel(self, name: str) -> bool:
        """Ask a running task to stop. Returns `False` if it was not running."""
        ...

    def pause(self, name: str) -> None: ...

    def resume(self, name: str) -> None: ...

    def status(self, name: str) -> TaskStatus: ...

    def all_status(self) -> Sequence[TaskStatus]: ...

    async def history(self, name: str, *, limit: int = 20) -> Sequence[TaskRun]: ...

    def notify_activity(self) -> None:
        """Record that the user is active, resetting idle triggers (docs/11 §4)."""
        ...
