"""Port conformance for `Clock`.

Parameterised over every implementation, including the fake. The fake passes the same
suite as the real one or the tests built on it are lying (docs/20 §4).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from hedwig.core.clock import FakeClock, SystemClock
from hedwig.core.ports import Clock

IMPLEMENTATIONS: list[Callable[[], Clock]] = [SystemClock, FakeClock]


@pytest.fixture(params=IMPLEMENTATIONS, ids=lambda factory: factory.__name__)
def subject(request: pytest.FixtureRequest) -> Clock:
    factory: Callable[[], Clock] = request.param
    return factory()


def test_satisfies_the_protocol(subject: Clock) -> None:
    assert isinstance(subject, Clock)


def test_now_is_timezone_aware_utc(subject: Clock) -> None:
    moment = subject.now()
    assert moment.tzinfo is not None
    assert moment.utcoffset() == datetime.now(UTC).utcoffset()


def test_now_never_moves_backwards(subject: Clock) -> None:
    readings = [subject.now() for _ in range(20)]
    assert readings == sorted(readings)


def test_monotonic_never_moves_backwards(subject: Clock) -> None:
    readings = [subject.monotonic() for _ in range(20)]
    assert readings == sorted(readings)


async def test_sleep_advances_time(subject: Clock) -> None:
    before = subject.monotonic()
    await subject.sleep(0.01)
    assert subject.monotonic() >= before


# --- FakeClock-specific behaviour -------------------------------------------------


def test_fake_clock_travels() -> None:
    clock = FakeClock("2026-01-01T09:00:00+00:00")
    clock.advance(days=90)
    assert clock.now().isoformat() == "2026-04-01T09:00:00+00:00"


def test_fake_clock_sleep_does_not_wait() -> None:
    """A year of simulated time must cost no real time (docs/20 §3.1)."""
    import asyncio
    import time

    clock = FakeClock()
    started = time.monotonic()
    asyncio.run(clock.sleep(365 * 24 * 3600))
    assert time.monotonic() - started < 0.5
    assert clock.now().year == 2027


def test_fake_clock_refuses_to_go_backwards() -> None:
    clock = FakeClock()
    with pytest.raises(ValueError):
        clock.advance(seconds=-1)


def test_fake_clock_requires_an_aware_start() -> None:
    with pytest.raises(ValueError):
        FakeClock(datetime(2026, 1, 1))  # noqa: DTZ001 - deliberately naive
