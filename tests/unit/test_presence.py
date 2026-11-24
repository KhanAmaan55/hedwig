"""Presence (ADR-0026): who has HEDWIG open, and delivery that one stalled client cannot hold up."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from hedwig.core.clock import FakeClock
from hedwig.core.presence import Presence


async def test_a_listener_that_stops_reading_is_dropped_and_the_others_still_hear() -> None:
    presence = Presence(FakeClock("2026-01-01T09:00:00+00:00"), delivery_timeout=0.05)
    heard: list[str] = []

    async def reading(kind: str, payload: Mapping[str, Any]) -> None:
        heard.append(kind)

    async def stalled(kind: str, payload: Mapping[str, Any]) -> None:
        await asyncio.Event().wait()  # a socket whose buffer never drains

    presence.join(reading, session_id="a")
    presence.join(stalled, session_id="b")

    delivered = await asyncio.wait_for(presence.broadcast("initiative.message", {}), 1.0)

    assert delivered == 1 and heard == ["initiative.message"]
    assert presence.current_session() == "a", "the stalled listener left"


async def test_a_listener_that_fails_is_dropped() -> None:
    presence = Presence(FakeClock("2026-01-01T09:00:00+00:00"))

    async def broken(kind: str, payload: Mapping[str, Any]) -> None:
        raise ConnectionError("gone")

    presence.join(broken)
    assert await presence.broadcast("x", {}) == 0
    assert not presence.present


async def test_a_listener_dropped_for_stalling_is_closed_so_it_can_reconnect() -> None:
    presence = Presence(FakeClock("2026-01-01T09:00:00+00:00"), delivery_timeout=0.05)
    closed: list[str] = []

    async def stalled(kind: str, payload: Mapping[str, Any]) -> None:
        await asyncio.Event().wait()

    async def close() -> None:
        closed.append("closed")

    presence.join(stalled, session_id="a", drop=close)

    assert await presence.broadcast("initiative.message", {}) == 0
    assert closed == ["closed"] and not presence.present
