"""The `Clock` port.

The smallest port in the system and the one with the largest payoff. Because nothing in
HEDWIG calls `datetime.now()` directly, a test can simulate three months of memory decay,
reflection cycles and personality drift in milliseconds (docs/03 §5.6, docs/20 §3.1).

Enforced by `tests/architecture/test_conventions.py`, which fails the build if any module
outside `hedwig.core.clock` reads the wall clock.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Time, injected."""

    def now(self) -> datetime:
        """Current time as a timezone-aware UTC datetime.

        Never naive. Callers may assume `result.tzinfo is not None`.
        """
        ...

    def monotonic(self) -> float:
        """Seconds from an arbitrary origin, unaffected by wall-clock adjustments.

        Use for measuring durations. Never for timestamps.
        """
        ...

    async def sleep(self, seconds: float) -> None:
        """Suspend the calling task for `seconds` of this clock's time."""
        ...
