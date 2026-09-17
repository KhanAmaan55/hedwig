"""The event bus.

Focused on the properties docs/04 §3.1 promises, because each of them is a specific way an
event system fails in production: lost events, silent drops, storms, poison messages, and
handlers that quietly stopped running weeks ago.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest

from hedwig.core.bus import EventCatalogue, InProcessBus, UnknownEventTypeError
from hedwig.core.bus.in_process import EventStormError
from hedwig.core.clock import FakeClock
from hedwig.core.errors import InvalidRequestError
from hedwig.core.ports import Delivery, Event, HealthStatus
from hedwig.core.store import Database


@pytest.fixture
def catalogue() -> EventCatalogue:
    cat = EventCatalogue()
    cat.register("test.thing.happened", "A thing happened.")
    cat.register("test.other.happened", "Something else happened.")
    cat.register("test.strict.happened", "Needs a key.", payload_keys={"required"})
    return cat


@pytest.fixture
async def bus(
    database: Database, clock: FakeClock, catalogue: EventCatalogue
) -> AsyncIterator[InProcessBus]:
    instance = InProcessBus(database, clock=clock, catalogue=catalogue, queue_size=8)
    await instance.start()
    try:
        yield instance
    finally:
        await instance.stop()


# -- publishing ------------------------------------------------------------


async def test_emit_fills_in_identity_time_and_correlation(bus: InProcessBus) -> None:
    event = await bus.emit("test.thing.happened", {"n": 1}, source="tests")

    assert event.id
    assert event.occurred_at.tzinfo is not None
    assert event.correlation_id
    assert event.payload == {"n": 1}


async def test_events_are_durable_before_they_are_dispatched(
    bus: InProcessBus, database: Database
) -> None:
    """A crash between 'it happened' and 'someone handled it' must not lose the fact."""
    await bus.emit("test.thing.happened", {"n": 1}, source="tests")

    row = database.query_one("SELECT type, payload FROM event")
    assert row is not None
    assert row["type"] == "test.thing.happened"


async def test_unknown_event_types_are_rejected(bus: InProcessBus) -> None:
    """docs/04 §4 — a typo'd name that silently goes nowhere is the nastiest bug here."""
    with pytest.raises(UnknownEventTypeError):
        await bus.emit("test.typo.happenned", source="tests")


async def test_missing_required_payload_keys_are_rejected(bus: InProcessBus) -> None:
    with pytest.raises(InvalidRequestError):
        await bus.emit("test.strict.happened", {"wrong": 1}, source="tests")


async def test_non_json_payload_values_are_coerced_to_strings(
    bus: InProcessBus, database: Database
) -> None:
    """Deliberate leniency: datetimes and paths are common in payloads and useful stored
    as text. The cost is that an accidentally-passed object becomes its repr rather than an
    error, which is why payload *keys* are validated by the catalogue instead."""
    from pathlib import Path

    await bus.emit("test.thing.happened", {"path": Path("/tmp/x")}, source="tests")

    row = database.query_one("SELECT payload FROM event")
    assert row is not None
    assert "/tmp/x" in row["payload"]


# -- delivery --------------------------------------------------------------


async def test_handlers_receive_matching_events(bus: InProcessBus) -> None:
    seen: list[Event] = []

    bus.subscribe("test.thing.*", lambda e: _append(seen, e), name="collector")
    await bus.emit("test.thing.happened", {"n": 1}, source="tests")
    await bus.emit("test.other.happened", {"n": 2}, source="tests")
    await bus.drain()

    assert [event.type for event in seen] == ["test.thing.happened"]


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        ("*", True),
        ("test.thing.happened", True),
        ("test.*.happened", True),
        ("*.thing.*", True),
        ("test.thing.*", True),
        ("test.other.*", False),
        ("test.thing", False),
        ("other.thing.happened", False),
    ],
)
async def test_pattern_matching(bus: InProcessBus, pattern: str, expected: bool) -> None:
    seen: list[Event] = []
    bus.subscribe(pattern, lambda e: _append(seen, e), name=f"sub-{pattern}")

    await bus.emit("test.thing.happened", source="tests")
    await bus.drain()

    assert bool(seen) is expected


async def test_one_failing_handler_never_costs_another_its_event(bus: InProcessBus) -> None:
    """A crashing emotion handler must not lose a memory write (docs/04 §3.1)."""
    delivered: list[str] = []

    async def explodes(_: Event) -> None:
        raise RuntimeError("handler is broken")

    async def works(event: Event) -> None:
        delivered.append(event.id)

    bus.subscribe("test.*.*", explodes, name="broken")
    bus.subscribe("test.*.*", works, name="healthy")

    await bus.emit("test.thing.happened", source="tests")
    await bus.drain()

    assert len(delivered) == 1


async def test_an_already_handled_event_is_not_redelivered_after_a_restart(
    database: Database, clock: FakeClock, catalogue: EventCatalogue
) -> None:
    """At-least-once plus a dedupe record is what makes idempotency cheap (docs/04 §3.2).

    Without the `processed_event` check, every restart would replay the last 24 hours into
    handlers that have already run — which is the difference between at-least-once and
    every-time.
    """
    seen: list[Event] = []

    first = InProcessBus(database, clock=clock, catalogue=catalogue)
    first.subscribe("test.*.*", lambda e: _append(seen, e), name="stable-name")
    await first.start()
    await first.emit("test.thing.happened", source="tests")
    await first.drain()
    await first.stop()
    assert len(seen) == 1

    second = InProcessBus(database, clock=clock, catalogue=catalogue)
    second.subscribe("test.*.*", lambda e: _append(seen, e), name="stable-name")
    await second.start()
    await second.drain()
    await second.stop()

    assert len(seen) == 1


async def test_correlation_and_causation_flow_into_the_handler(bus: InProcessBus) -> None:
    from hedwig.core.context import current_causation_id, current_correlation_id

    captured: dict[str, str | None] = {}

    async def handler(event: Event) -> None:
        captured["correlation"] = current_correlation_id()
        captured["causation"] = current_causation_id()

    bus.subscribe("test.*.*", handler, name="tracer")
    event = await bus.emit("test.thing.happened", source="tests", correlation_id="turn_1")
    await bus.drain()

    assert captured["correlation"] == "turn_1"
    assert captured["causation"] == event.id


# -- failure handling ------------------------------------------------------


async def test_failures_retry_then_dead_letter(bus: InProcessBus, database: Database) -> None:
    attempts = 0

    async def always_fails(_: Event) -> None:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("nope")

    bus.subscribe("test.*.*", always_fails, name="doomed")
    await bus.emit("test.thing.happened", source="tests")
    await bus.drain()

    assert attempts == 4  # first try plus three retries
    row = database.query_one("SELECT COUNT(*) AS n FROM event_dead_letter")
    assert row is not None and row["n"] == 1


async def test_dead_letters_degrade_health(bus: InProcessBus) -> None:
    async def always_fails(_: Event) -> None:
        raise RuntimeError("nope")

    bus.subscribe("test.*.*", always_fails, name="doomed")
    await bus.emit("test.thing.happened", source="tests")
    await bus.drain()

    health = await bus.health()
    assert health.status is HealthStatus.DEGRADED
    assert health.detail["dead_letters"] == 1


async def test_a_handler_publishing_what_it_consumes_is_stopped(bus: InProcessBus) -> None:
    """Without the depth cap this spins a core until the machine is rebooted."""
    published = 0

    async def recursive(_: Event) -> None:
        nonlocal published
        published += 1
        await bus.emit("test.thing.happened", source="tests")

    bus.subscribe("test.thing.*", recursive, name="storm")
    await bus.emit("test.thing.happened", source="tests")
    await bus.drain()

    assert published < 20  # bounded by MAX_DEPTH, not by luck


async def test_publish_raises_when_the_depth_cap_is_reached_directly() -> None:
    from hedwig.core.bus.in_process import MAX_DEPTH, _depth

    token = _depth.set(MAX_DEPTH)
    try:
        database = Database(":memory:")
        from hedwig.core.store import MigrationRunner
        from hedwig.wiring import MIGRATIONS_DIR

        MigrationRunner(database, MIGRATIONS_DIR, clock=FakeClock()).run()
        catalogue = EventCatalogue()
        catalogue.register("test.thing.happened", "x")
        instance = InProcessBus(database, clock=FakeClock(), catalogue=catalogue)

        with pytest.raises(EventStormError):
            await instance.emit("test.thing.happened", source="tests")
        database.close()
    finally:
        _depth.reset(token)


# -- backpressure and lossiness -------------------------------------------


async def test_lossy_subscriptions_drop_and_count_rather_than_blocking(
    database: Database, clock: FakeClock, catalogue: EventCatalogue
) -> None:
    """A silent drop is how an event system starts lying to you (docs/04 §3.1)."""
    instance = InProcessBus(database, clock=clock, catalogue=catalogue, queue_size=2)
    gate = asyncio.Event()

    async def slow(_: Event) -> None:
        await gate.wait()

    instance.subscribe("test.*.*", slow, name="slow", delivery=Delivery.LOSSY)
    await instance.start()

    for _ in range(10):
        await instance.emit("test.thing.happened", source="tests")

    assert instance.stats()["subscriptions"]["slow"]["dropped"] > 0
    gate.set()
    await instance.stop()


# -- recovery --------------------------------------------------------------


async def test_events_missed_while_down_are_redelivered_on_start(
    database: Database, clock: FakeClock, catalogue: EventCatalogue
) -> None:
    """The whole point of the outbox: a restart mid-handler re-delivers (docs/04 §3.3)."""
    first = InProcessBus(database, clock=clock, catalogue=catalogue)
    await first.start()
    await first.emit("test.thing.happened", {"n": 1}, source="tests")
    await first.stop()  # nobody was subscribed, so nobody handled it

    seen: list[Event] = []
    second = InProcessBus(database, clock=clock, catalogue=catalogue)
    second.subscribe("test.*.*", lambda e: _append(seen, e), name="late-subscriber")
    await second.start()
    await second.drain()
    await second.stop()

    assert [event.payload["n"] for event in seen] == [1]


async def test_replay_is_bounded_and_filtered(bus: InProcessBus, clock: FakeClock) -> None:
    await bus.emit("test.thing.happened", {"n": 1}, source="tests")
    await bus.emit("test.other.happened", {"n": 2}, source="tests")

    since = clock.now() - timedelta(hours=1)
    replayed = [event.type async for event in bus.replay(since=since, pattern="test.thing.*")]

    assert replayed == ["test.thing.happened"]


# -- registration ----------------------------------------------------------


async def test_duplicate_subscription_names_are_refused(bus: InProcessBus) -> None:
    """Names key the cursor and the dedupe record, so they must be unique."""
    bus.subscribe("test.*.*", lambda e: _append([], e), name="dup")
    with pytest.raises(InvalidRequestError):
        bus.subscribe("test.*.*", lambda e: _append([], e), name="dup")


async def test_unsubscribe_stops_delivery(bus: InProcessBus) -> None:
    seen: list[Event] = []
    handle = bus.subscribe("test.*.*", lambda e: _append(seen, e), name="temporary")

    await bus.emit("test.thing.happened", source="tests")
    await bus.drain()
    await handle.unsubscribe()
    await bus.emit("test.thing.happened", source="tests")
    await bus.drain()

    assert len(seen) == 1


async def _append(sink: list[Event], event: Event) -> None:
    sink.append(event)
