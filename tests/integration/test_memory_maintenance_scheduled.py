"""Memory maintenance runs in the real process, not only in tests (MEM-01, docs/29 G2).

The decay-and-forget pass had its own thorough unit tests and was never scheduled. These
tests are about the wiring: that the composed system runs it, on its own, through the
scheduler, and that a run does the documented work.
"""

from __future__ import annotations

import asyncio

from hedwig.core.clock import FakeClock
from hedwig.core.ports.scheduler import RunStatus
from hedwig.wiring import Container


async def _settle(cycles: int = 20) -> None:
    for _ in range(cycles):
        await asyncio.sleep(0)


def test_the_nightly_pass_is_registered(container: Container) -> None:
    status = container.scheduler.status("memory.maintenance")
    assert status.trigger.startswith("daily at 03:00")
    assert status.enabled


async def test_a_run_decays_what_has_not_been_used(
    running_container: Container, clock: FakeClock
) -> None:
    await running_container.brain.run_turn(
        "Please remember that my favourite tea is a smoky lapsang souchong.",
        session_id="sess_tea",
    )
    await running_container.bus.drain()
    before = running_container.database.query_one(
        "SELECT salience FROM episode WHERE tombstoned_at IS NULL"
    )
    assert before is not None

    clock.advance(days=60)
    await running_container.scheduler.trigger("memory.maintenance", reason="test")
    for _ in range(50):
        await _settle()
        history = await running_container.scheduler.history("memory.maintenance")
        if history and history[0].status is not RunStatus.RUNNING and history[0].finished_at:
            break

    run = history[0]
    assert run.status is RunStatus.COMPLETED, run.error
    assert run.stats["decayed"] >= 1
    assert {"forgotten", "protected", "budget", "considered"} <= set(run.stats)

    after = running_container.database.query_one(
        "SELECT salience FROM episode WHERE tombstoned_at IS NULL"
    )
    assert after is not None
    assert float(after["salience"]) < float(before["salience"])
