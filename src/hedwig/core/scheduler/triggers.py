"""Triggers: when a task is due.

Explicit trigger types rather than a cron string parser. Cron is a compact language for
expressing schedules and a poor one for reading them, and HEDWIG needs four shapes —
"every N seconds", "daily at 03:00", "weekly on Sunday", "when idle for N minutes"
(docs/17 §5). Each is a pure function of the current time and the last success, which is
what makes the whole schedule testable against a `FakeClock` without waiting for anything.

A cron parser can be added later behind the same `Trigger` protocol; nothing else changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from hedwig.core.ports import TriggerKind


@dataclass(frozen=True, slots=True)
class IntervalTrigger:
    """Every `seconds`, measured from the last success."""

    seconds: float

    @property
    def kind(self) -> TriggerKind:
        return TriggerKind.SCHEDULE

    def next_due(self, *, now: datetime, last_success: datetime | None) -> datetime | None:
        if last_success is None:
            return now
        return last_success + timedelta(seconds=self.seconds)

    def describe(self) -> str:
        return f"every {self.seconds:g}s"


@dataclass(frozen=True, slots=True)
class DailyTrigger:
    """Once a day at a local wall-clock time.

    Local, not UTC: a nightly consolidation that runs at 03:00 UTC is at 04:00 in Lisbon and
    19:00 in California. The whole point of "nightly" is that the user is asleep.
    """

    hour: int
    minute: int = 0
    tz: str = "local"

    def __post_init__(self) -> None:
        if not 0 <= self.hour <= 23 or not 0 <= self.minute <= 59:
            raise ValueError(f"invalid time {self.hour:02d}:{self.minute:02d}")

    @property
    def kind(self) -> TriggerKind:
        return TriggerKind.SCHEDULE

    def next_due(self, *, now: datetime, last_success: datetime | None) -> datetime | None:
        local_now = now.astimezone() if self.tz == "local" else now.astimezone(UTC)
        candidate = local_now.replace(hour=self.hour, minute=self.minute, second=0, microsecond=0)
        if last_success is None:
            return candidate.astimezone(UTC)

        last_local = last_success.astimezone(local_now.tzinfo)
        if last_local >= candidate:
            candidate += timedelta(days=1)
        return candidate.astimezone(UTC)

    def describe(self) -> str:
        return f"daily at {self.hour:02d}:{self.minute:02d} {self.tz}"


@dataclass(frozen=True, slots=True)
class WeeklyTrigger:
    """Once a week on `weekday` (0 = Monday) at a local time."""

    weekday: int
    hour: int = 3
    minute: int = 0

    def __post_init__(self) -> None:
        if not 0 <= self.weekday <= 6:
            raise ValueError(f"weekday must be 0-6, got {self.weekday}")

    @property
    def kind(self) -> TriggerKind:
        return TriggerKind.SCHEDULE

    def next_due(self, *, now: datetime, last_success: datetime | None) -> datetime | None:
        local_now = now.astimezone()
        days_ahead = (self.weekday - local_now.weekday()) % 7
        candidate = (local_now + timedelta(days=days_ahead)).replace(
            hour=self.hour, minute=self.minute, second=0, microsecond=0
        )
        if last_success is not None and last_success.astimezone(local_now.tzinfo) >= candidate:
            candidate += timedelta(days=7)
        return candidate.astimezone(UTC)

    def describe(self) -> str:
        names = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
        return f"weekly on {names[self.weekday]} at {self.hour:02d}:{self.minute:02d}"


@dataclass(frozen=True, slots=True)
class IdleTrigger:
    """When there has been no user activity for `after_seconds`.

    Never competes with a conversation, which is the precondition every background job in
    docs/11 §4 and docs/17 §7 is written against. `min_interval_seconds` stops a long idle
    period from running the same task over and over.
    """

    after_seconds: float
    min_interval_seconds: float = 3600.0

    @property
    def kind(self) -> TriggerKind:
        return TriggerKind.IDLE

    def next_due(self, *, now: datetime, last_success: datetime | None) -> datetime | None:
        # Idle triggers are evaluated against activity, not the calendar; the scheduler
        # consults `is_idle_due` as well. This bound is the rate limit.
        if last_success is None:
            return now
        return last_success + timedelta(seconds=self.min_interval_seconds)

    def is_idle_due(self, *, idle_seconds: float) -> bool:
        return idle_seconds >= self.after_seconds

    def describe(self) -> str:
        return f"when idle for {self.after_seconds:g}s"


@dataclass(frozen=True, slots=True)
class ManualTrigger:
    """Never runs on its own. For tasks that exist to be triggered by hand or by an event."""

    @property
    def kind(self) -> TriggerKind:
        return TriggerKind.MANUAL

    def next_due(self, *, now: datetime, last_success: datetime | None) -> datetime | None:
        return None

    def describe(self) -> str:
        return "manual only"
