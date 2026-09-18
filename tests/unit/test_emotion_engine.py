"""The engine: subscription, coalescing, persistence and the events it publishes (docs/09 §5).

Two tests here carry more weight than the rest.

`test_the_same_events_produce_the_same_state_twice` is the property docs/09 §4.2 gave up a
model to keep. If it ever fails, every scenario test below becomes advisory.

`test_a_week_of_failures_cannot_break_trust` is ADR-0019's claim, run rather than argued.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from hedwig.core.bus import InProcessBus
from hedwig.core.clock import FakeClock
from hedwig.core.ports.emotion import DIMENSIONS, Dimension, EmotionState
from hedwig.core.ports.event_bus import Event
from hedwig.core.store import Database
from hedwig.emotion import EmotionEngine, EmotionStore
from hedwig.emotion.dynamics import FLOOR, Baselines


def _event(type_: str, **payload: Any) -> Event:
    return Event(
        id=f"evt_{type_}_{len(payload)}",
        type=type_,
        occurred_at=datetime(2026, 6, 1, 12, tzinfo=UTC),
        source="test",
        correlation_id="corr_1",
        payload=payload,
    )


@pytest.fixture
def engine(database: Database, clock: FakeClock) -> EmotionEngine:
    return EmotionEngine(EmotionStore(database, clock=clock), clock=clock, timezone="UTC")


async def _feed(engine: EmotionEngine, type_: str, **payload: Any) -> None:
    await engine._on_event(_event(type_, **payload))


# =========================================================================
# Coalescing
# =========================================================================


async def test_appraising_an_event_does_not_change_the_state(engine: EmotionEngine) -> None:
    """Per-event updates would storm the guarded row and make the avatar twitch. The tick is
    what applies them (docs/09 §5.2)."""
    before = await engine.state()
    await _feed(engine, "conversation.turn.completed", status="failed")

    assert await engine.state() == before


async def test_the_tick_applies_everything_queued_since_the_last_one(
    engine: EmotionEngine, clock: FakeClock
) -> None:
    for _ in range(3):
        await _feed(engine, "conversation.turn.completed", status="failed")
    clock.advance(seconds=30)

    after = await engine.tick()

    assert after.stress > EmotionState().stress
    assert after.confidence < EmotionState().confidence


async def test_a_tick_with_nothing_queued_still_decays(
    engine: EmotionEngine, clock: FakeClock
) -> None:
    await _feed(engine, "conversation.turn.completed", status="failed")
    clock.advance(seconds=30)
    stressed = await engine.tick()

    clock.advance(hours=6)
    settled = await engine.tick()

    assert settled.stress < stressed.stress


async def test_ticking_twice_in_the_same_instant_applies_the_batch_once(
    engine: EmotionEngine, clock: FakeClock
) -> None:
    await _feed(engine, "conversation.turn.completed", status="completed")
    clock.advance(seconds=30)

    first = await engine.tick()
    second = await engine.tick()

    # The version bumps — a tick is a write either way — but nothing moved.
    assert first.as_dict() == second.as_dict()


# =========================================================================
# Determinism — the property the model path was traded for
# =========================================================================


async def test_the_same_events_produce_the_same_state_twice(clock: FakeClock) -> None:
    """docs/09 §4.2. Bit-for-bit, not approximately."""
    script: list[tuple[str, dict[str, Any]]] = [
        ("conversation.session.started", {"session_id": "s", "channel": "cli"}),
        ("conversation.message.received", {"text": "thanks, that helped a lot"}),
        ("conversation.turn.completed", {"status": "completed", "tool_calls": 1}),
        ("memory.entity.discovered", {"entity_id": "e", "kind": "place", "name": "Porto"}),
        ("conversation.turn.completed", {"status": "failed"}),
    ]

    async def run() -> EmotionState:
        database = Database(":memory:")
        from hedwig.core.store import MigrationRunner
        from hedwig.wiring import MIGRATIONS_DIR

        MigrationRunner(
            database, MIGRATIONS_DIR, clock=FakeClock("2026-01-01T09:00:00+00:00")
        ).run()
        local = FakeClock("2026-01-01T09:00:00+00:00")
        instance = EmotionEngine(EmotionStore(database, clock=local), clock=local, timezone="UTC")
        for type_, payload in script:
            await _feed(instance, type_, **payload)
            local.advance(seconds=30)
            await instance.tick()
        state = await instance.state()
        database.close()
        return state

    assert await run() == await run()


# =========================================================================
# Persistence
# =========================================================================


async def test_state_survives_a_restart(database: Database, clock: FakeClock) -> None:
    first = EmotionEngine(EmotionStore(database, clock=clock), clock=clock, timezone="UTC")
    await _feed(first, "conversation.turn.completed", status="failed")
    clock.advance(seconds=30)
    stressed = await first.tick()

    second = EmotionEngine(EmotionStore(database, clock=clock), clock=clock, timezone="UTC")

    assert (await second.state()).stress == pytest.approx(stressed.stress)


async def test_a_restart_after_a_long_gap_settles_rather_than_resuming_the_old_mood(
    database: Database, clock: FakeClock
) -> None:
    """A companion restarted after a week must not resume the mood it had a week ago."""
    first = EmotionEngine(EmotionStore(database, clock=clock), clock=clock, timezone="UTC")
    for _ in range(6):
        await _feed(first, "conversation.turn.completed", status="failed")
    clock.advance(seconds=30)
    stressed = (await first.tick()).stress

    clock.advance(days=7)
    second = EmotionEngine(EmotionStore(database, clock=clock), clock=clock, timezone="UTC")
    await second.start()

    assert (await second.state()).stress < stressed


async def test_the_history_records_why_not_just_what(
    engine: EmotionEngine, clock: FakeClock
) -> None:
    await _feed(engine, "conversation.turn.completed", status="failed")
    clock.advance(seconds=30)
    await engine.tick()

    history = await engine.history()
    assert history
    assert history[-1]["cause"] == "appraisal"
    assert set(history[-1]) >= {d.value for d in DIMENSIONS}


async def test_the_appraisal_log_never_contains_user_text(
    engine: EmotionEngine, clock: FakeClock, database: Database
) -> None:
    """This table is read by the inspector and shipped in an export. A conversation must not
    leak into it sideways."""
    secret = "my passport number is 123456789"
    await _feed(engine, "conversation.message.received", text=secret)
    clock.advance(seconds=30)
    await engine.tick()

    rows = database.query("SELECT rationale, dims FROM appraisal")
    assert rows
    for row in rows:
        assert secret not in str(row["rationale"])
        assert secret not in str(row["dims"])


# =========================================================================
# Events out
# =========================================================================


async def test_an_idle_tick_publishes_nothing(
    database: Database, clock: FakeClock, bus: InProcessBus
) -> None:
    """Below `min_publish_delta` nothing is said, which is what keeps the log and the avatar
    quiet during idle periods (docs/09 §5.2)."""
    engine = EmotionEngine(
        EmotionStore(database, clock=clock), clock=clock, bus=bus, timezone="UTC"
    )
    seen: list[Event] = []
    bus.subscribe("emotion.*.*", lambda event: _record(seen, event), name="spy")

    clock.advance(seconds=30)
    await engine.tick()
    await bus.drain()

    assert not [event for event in seen if event.type == "emotion.state.changed"]


async def test_a_real_change_publishes_a_full_snapshot(
    database: Database, clock: FakeClock, bus: InProcessBus
) -> None:
    """Snapshots, never deltas (docs/02 §4.2): a subscriber that misses one update must not
    be left with a state assembled from half the changes."""
    engine = EmotionEngine(
        EmotionStore(database, clock=clock), clock=clock, bus=bus, timezone="UTC"
    )
    seen: list[Event] = []
    bus.subscribe("emotion.state.changed", lambda event: _record(seen, event), name="spy")

    for _ in range(4):
        await _feed(engine, "conversation.turn.completed", status="failed")
    clock.advance(seconds=30)
    await engine.tick()
    await bus.drain()

    assert seen
    assert set(seen[-1].payload["state"]) == {d.value for d in DIMENSIONS}
    assert "valence" in seen[-1].payload


async def test_crossing_a_threshold_is_announced_once_not_every_tick(
    database: Database, clock: FakeClock, bus: InProcessBus
) -> None:
    """Hysteresis: a value sitting on a threshold must not publish a crossing every tick."""
    engine = EmotionEngine(
        EmotionStore(database, clock=clock), clock=clock, bus=bus, timezone="UTC"
    )
    seen: list[Event] = []
    bus.subscribe("emotion.threshold.crossed", lambda event: _record(seen, event), name="spy")

    for _ in range(30):
        await _feed(engine, "memory.entity.discovered", entity_id="e", kind="k", name="n")
        clock.advance(seconds=30)
        await engine.tick()
    await bus.drain()

    curiosity_events = [e for e in seen if e.payload["dimension"] == "curiosity"]
    assert len(curiosity_events) == 1
    assert curiosity_events[0].payload["direction"] == "high"


# =========================================================================
# A simulated week (docs/09 §10)
# =========================================================================


async def test_stress_rises_with_failure_and_recovers_overnight(
    engine: EmotionEngine, clock: FakeClock
) -> None:
    for _ in range(5):
        await _feed(engine, "conversation.turn.completed", status="failed")
        clock.advance(seconds=30)
        await engine.tick()
    bad_afternoon = (await engine.state()).stress

    clock.advance(hours=10)
    await engine.tick()

    assert (await engine.state()).stress < bad_afternoon
    assert bad_afternoon > EmotionState().stress


async def test_a_week_of_failures_cannot_break_trust(
    engine: EmotionEngine, clock: FakeClock
) -> None:
    """ADR-0019's central claim, run rather than argued.

    Seven simulated days of nothing but failing turns and refusals — far worse than any real
    week — leaves trust reduced but intact, and never below the floor.
    """
    for _ in range(7 * 24 * 4):
        await _feed(engine, "conversation.turn.completed", status="failed")
        clock.advance(minutes=15)
        await engine.tick()

    state = await engine.state()
    assert state.trust >= FLOOR[Dimension.TRUST]
    assert state.trust < EmotionState().trust, "a terrible week should still register"


async def test_curiosity_rises_as_the_world_turns_out_to_be_new(
    engine: EmotionEngine, clock: FakeClock
) -> None:
    before = (await engine.state()).curiosity
    for index in range(10):
        await _feed(engine, "memory.entity.discovered", entity_id=f"e{index}", kind="k", name="n")
        clock.advance(seconds=30)
        await engine.tick()

    assert (await engine.state()).curiosity > before


async def test_energy_tracks_the_clock_over_a_day(database: Database, clock: FakeClock) -> None:
    engine = EmotionEngine(EmotionStore(database, clock=clock), clock=clock, timezone="UTC")
    readings: dict[int, float] = {}

    for _ in range(24 * 4):
        clock.advance(minutes=15)
        state = await engine.tick()
        readings[clock.now().hour] = state.energy

    assert readings[15] > readings[3], "energy should be higher mid-afternoon than at 3 am"


async def test_a_good_week_and_a_bad_week_end_up_somewhere_different(
    database: Database, clock: FakeClock
) -> None:
    """The whole point of having state at all: history has to leave a mark."""

    async def week(status: str, text: str) -> EmotionState:
        local = FakeClock("2026-03-01T09:00:00+00:00")
        instance = EmotionEngine(EmotionStore(database, clock=local), clock=local, timezone="UTC")
        for _ in range(100):
            await _feed(instance, "conversation.message.received", text=text)
            await _feed(instance, "conversation.turn.completed", status=status, tool_calls=1)
            local.advance(minutes=30)
            await instance.tick()
        return await instance.state()

    good = await week("completed", "thanks, that was exactly what I needed")
    bad = await week("failed", "that is wrong again")

    assert good.happiness > bad.happiness
    assert good.confidence > bad.confidence
    assert good.stress < bad.stress
    assert good.trust > bad.trust


# =========================================================================
# Health
# =========================================================================


async def test_health_notices_a_pinned_dimension(database: Database, clock: FakeClock) -> None:
    """docs/09 §9 lists a runaway dimension first. The clamps stop it being unbounded; this
    stops it being unnoticed."""
    store = EmotionStore(database, clock=clock)
    store.save(EmotionState(stress=1.0), ticked_at=clock.now())
    engine = EmotionEngine(store, clock=clock, timezone="UTC")

    health = await engine.health()

    assert health.status.value == "degraded"
    assert "stress" in health.message


async def test_a_settled_engine_is_healthy(engine: EmotionEngine) -> None:
    assert (await engine.health()).status.value == "ok"


async def test_baselines_can_be_supplied_by_personality_later(
    database: Database, clock: FakeClock
) -> None:
    """The seam personality plugs into: the engine takes baselines rather than computing
    them, so a real trait profile is a constructor argument and no logic change."""
    from hedwig.emotion.dynamics import Traits

    engine = EmotionEngine(
        EmotionStore(database, clock=clock),
        clock=clock,
        baselines=Baselines.from_traits(Traits(curiosity=1.0)),
        timezone="UTC",
    )
    for _ in range(200):
        clock.advance(hours=6)
        await engine.tick()

    assert (await engine.state()).curiosity == pytest.approx(0.80, abs=0.02)


async def _record(seen: list[Event], event: Event) -> None:
    seen.append(event)
