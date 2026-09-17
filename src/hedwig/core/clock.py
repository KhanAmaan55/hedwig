"""Clock implementations.

`SystemClock` is the only place in HEDWIG permitted to read the wall clock. `FakeClock`
is what makes the long-horizon tests in docs/20 §3.1 possible; it lives in the source tree
rather than in `tests/` so that any consumer — including a future scenario CLI — can drive
time deterministically.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta


class SystemClock:
    """Real time. The default in every non-test container."""

    __slots__ = ()

    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class FakeClock:
    """Controllable time.

    `sleep` advances the clock instead of waiting, so a test that simulates a year
    completes in milliseconds.
    """

    __slots__ = ("_monotonic", "_now")

    def __init__(self, start: datetime | str = "2026-01-01T09:00:00+00:00") -> None:
        if isinstance(start, str):
            start = datetime.fromisoformat(start)
        if start.tzinfo is None:
            raise ValueError("FakeClock requires a timezone-aware start time")
        self._now = start.astimezone(UTC)
        self._monotonic = 0.0

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._monotonic

    async def sleep(self, seconds: float) -> None:
        self.advance(seconds=seconds)
        await asyncio.sleep(0)  # yield, so other tasks still interleave realistically

    def advance(
        self,
        *,
        seconds: float = 0,
        minutes: float = 0,
        hours: float = 0,
        days: float = 0,
    ) -> None:
        delta = timedelta(seconds=seconds, minutes=minutes, hours=hours, days=days)
        if delta.total_seconds() < 0:
            raise ValueError("time does not move backwards")
        self._now += delta
        self._monotonic += delta.total_seconds()

    def advance_to(self, when: datetime | str) -> None:
        if isinstance(when, str):
            when = datetime.fromisoformat(when)
        if when.tzinfo is None:
            raise ValueError("advance_to requires a timezone-aware time")
        target = when.astimezone(UTC)
        self.advance(seconds=(target - self._now).total_seconds())
