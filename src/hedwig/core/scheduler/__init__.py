"""Task scheduler: background work, on time, without starving the conversation."""

from __future__ import annotations

from hedwig.core.scheduler.scheduler import AsyncTaskScheduler, TaskAlreadyRunningError
from hedwig.core.scheduler.triggers import (
    DailyTrigger,
    IdleTrigger,
    IntervalTrigger,
    ManualTrigger,
    WeeklyTrigger,
)

__all__ = [
    "AsyncTaskScheduler",
    "DailyTrigger",
    "IdleTrigger",
    "IntervalTrigger",
    "ManualTrigger",
    "TaskAlreadyRunningError",
    "WeeklyTrigger",
]
