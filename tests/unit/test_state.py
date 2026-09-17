"""The state manager.

The rule under test is docs/08 §3: exactly one module writes each piece of state. Here it
is mechanical rather than aspirational — a write from the wrong owner is refused.
"""

from __future__ import annotations

import pytest

from hedwig.core.bus import InProcessBus
from hedwig.core.clock import FakeClock
from hedwig.core.errors import ConflictError, NotFoundError
from hedwig.core.ports import Event, NamespaceInfo, StateValue
from hedwig.core.state import OwnershipError, SqliteStateManager
from hedwig.core.store import Database


@pytest.fixture
def owned(state: SqliteStateManager) -> SqliteStateManager:
    state.register_namespace("emotion", NamespaceInfo(owner="emotion"))
    state.register_namespace(
        "scratch", NamespaceInfo(owner="tests", keep_history=False, description="no history")
    )
    return state


# -- ownership -------------------------------------------------------------


async def test_a_write_from_a_non_owner_is_refused(owned: SqliteStateManager) -> None:
    with pytest.raises(OwnershipError):
        await owned.put("emotion", "state", {"valence": 0.5}, owner="curiosity", reason="nope")


async def test_writing_an_unregistered_namespace_is_refused(
    owned: SqliteStateManager,
) -> None:
    with pytest.raises(NotFoundError):
        await owned.put("unclaimed", "k", {}, owner="anyone", reason="nope")


def test_a_second_owner_cannot_claim_a_namespace(state: SqliteStateManager) -> None:
    """Two writers for one piece of state is the bug this port exists to prevent."""
    state.register_namespace("emotion", NamespaceInfo(owner="emotion"))
    with pytest.raises(ConflictError):
        state.register_namespace("emotion", NamespaceInfo(owner="impostor"))


def test_re_registering_the_same_owner_is_fine(state: SqliteStateManager) -> None:
    state.register_namespace("emotion", NamespaceInfo(owner="emotion"))
    state.register_namespace("emotion", NamespaceInfo(owner="emotion", description="updated"))


# -- reads and writes ------------------------------------------------------


async def test_put_then_get_round_trips(owned: SqliteStateManager) -> None:
    written = await owned.put(
        "emotion", "state", {"valence": 0.5, "arousal": 0.2}, owner="emotion", reason="tick"
    )
    read = await owned.get("emotion", "state")

    assert read is not None
    assert read.value == {"valence": 0.5, "arousal": 0.2}
    assert read.version == written.version == 1


async def test_versions_increment_on_every_write(owned: SqliteStateManager) -> None:
    for expected in (1, 2, 3):
        document = await owned.put(
            "emotion", "state", {"n": expected}, owner="emotion", reason="tick"
        )
        assert document.version == expected


async def test_get_value_falls_back_to_a_default(owned: SqliteStateManager) -> None:
    value = await owned.get_value("emotion", "absent", {"valence": 0.0})
    assert value == {"valence": 0.0}


async def test_list_keys_is_sorted(owned: SqliteStateManager) -> None:
    for key in ("gamma", "alpha", "beta"):
        await owned.put("emotion", key, {}, owner="emotion", reason="seed")

    assert list(await owned.list_keys("emotion")) == ["alpha", "beta", "gamma"]


# -- optimistic concurrency ------------------------------------------------


async def test_a_stale_expected_version_is_refused(owned: SqliteStateManager) -> None:
    await owned.put("emotion", "state", {"n": 1}, owner="emotion", reason="first")

    with pytest.raises(ConflictError):
        await owned.put(
            "emotion", "state", {"n": 2}, owner="emotion", reason="stale", expected_version=0
        )


async def test_expected_version_zero_requires_a_new_document(
    owned: SqliteStateManager,
) -> None:
    await owned.put(
        "emotion", "fresh", {"n": 1}, owner="emotion", reason="create", expected_version=0
    )
    with pytest.raises(ConflictError):
        await owned.put(
            "emotion", "fresh", {"n": 2}, owner="emotion", reason="again", expected_version=0
        )


async def test_mutate_applies_a_delta(owned: SqliteStateManager) -> None:
    def increment(current: StateValue) -> StateValue:
        return {**current, "count": current.get("count", 0) + 1}

    for _ in range(3):
        await owned.mutate(
            "emotion", "counter", increment, owner="emotion", reason="tick", default={}
        )

    document = await owned.get("emotion", "counter")
    assert document is not None
    assert document.value == {"count": 3}


async def test_mutate_retries_after_a_concurrent_write(owned: SqliteStateManager) -> None:
    """The reason mutators must be pure: a conflict re-reads and re-applies the delta."""
    await owned.put("emotion", "counter", {"count": 0}, owner="emotion", reason="seed")
    interfered = False

    def increment(current: StateValue) -> StateValue:
        nonlocal interfered
        if not interfered:
            interfered = True
            # Simulate another writer landing between our read and our write.
            owned._database.execute(
                "UPDATE state_document SET value = ?, version = version + 1 "
                "WHERE namespace = 'emotion' AND key = 'counter'",
                ('{"count":10}',),
            )
        return {**current, "count": current.get("count", 0) + 1}

    document = await owned.mutate(
        "emotion", "counter", increment, owner="emotion", reason="increment"
    )

    # The delta landed on top of the interfering write rather than clobbering it.
    assert document.value == {"count": 11}


async def test_mutate_without_a_default_on_a_missing_document_raises(
    owned: SqliteStateManager,
) -> None:
    with pytest.raises(NotFoundError):
        await owned.mutate("emotion", "absent", lambda v: v, owner="emotion", reason="x")


# -- history ---------------------------------------------------------------


async def test_history_records_every_version_with_its_reason(
    owned: SqliteStateManager,
) -> None:
    await owned.put("emotion", "state", {"n": 1}, owner="emotion", reason="first")
    await owned.put("emotion", "state", {"n": 2}, owner="emotion", reason="second")

    history = await owned.history("emotion", "state")

    assert [revision.version for revision in history] == [2, 1]
    assert [revision.reason for revision in history] == ["second", "first"]


async def test_history_can_be_disabled_per_namespace(owned: SqliteStateManager) -> None:
    await owned.put("scratch", "k", {"n": 1}, owner="tests", reason="write")
    assert await owned.history("scratch", "k") == ()


# -- snapshots -------------------------------------------------------------


async def test_snapshot_and_restore_round_trip(owned: SqliteStateManager) -> None:
    """Selective rollback: restoring drifted personality must not touch memory."""
    await owned.put("emotion", "state", {"valence": 0.9}, owner="emotion", reason="good day")
    snapshot = await owned.snapshot("nightly", ["emotion"])

    await owned.put("emotion", "state", {"valence": -0.9}, owner="emotion", reason="bad week")

    restored = await owned.restore(snapshot.id, owner="emotion")
    document = await owned.get("emotion", "state")

    assert restored == 1
    assert document is not None
    assert document.value == {"valence": 0.9}


async def test_restore_is_itself_a_versioned_write(owned: SqliteStateManager) -> None:
    """The rollback appears in history rather than erasing it."""
    await owned.put("emotion", "state", {"n": 1}, owner="emotion", reason="first")
    snapshot = await owned.snapshot("before", ["emotion"])
    await owned.put("emotion", "state", {"n": 2}, owner="emotion", reason="second")
    await owned.restore(snapshot.id, owner="emotion")

    history = await owned.history("emotion", "state")
    assert [revision.value["n"] for revision in history] == [1, 2, 1]
    assert history[0].reason.startswith("restored from snapshot")


async def test_restore_requires_ownership_of_every_namespace(
    owned: SqliteStateManager,
) -> None:
    await owned.put("emotion", "state", {"n": 1}, owner="emotion", reason="seed")
    snapshot = await owned.snapshot("nightly", ["emotion"])

    with pytest.raises(OwnershipError):
        await owned.restore(snapshot.id, owner="impostor")


async def test_restoring_an_unknown_snapshot_raises(owned: SqliteStateManager) -> None:
    with pytest.raises(NotFoundError):
        await owned.restore("snap_missing", owner="emotion")


async def test_snapshots_are_listed_newest_first(
    owned: SqliteStateManager, clock: FakeClock
) -> None:
    await owned.put("emotion", "state", {"n": 1}, owner="emotion", reason="seed")
    await owned.snapshot("first", ["emotion"])
    clock.advance(hours=1)
    await owned.snapshot("second", ["emotion"])

    listed = await owned.list_snapshots()
    assert [snapshot.label for snapshot in listed] == ["second", "first"]
    assert listed[0].document_count == 1


# -- events and health -----------------------------------------------------


async def test_writes_announce_themselves_on_the_bus(
    database: Database, clock: FakeClock, bus: InProcessBus
) -> None:
    seen: list[Event] = []
    bus.subscribe("state.*.*", lambda event: _collect(seen, event), name="state-watch")

    manager = SqliteStateManager(database, clock=clock, bus=bus)
    manager.register_namespace("emotion", NamespaceInfo(owner="emotion"))
    await manager.put("emotion", "state", {"n": 1}, owner="emotion", reason="tick")
    await bus.drain()

    assert [event.type for event in seen] == ["state.document.changed"]
    assert seen[0].payload["namespace"] == "emotion"


async def test_health_counts_documents(owned: SqliteStateManager) -> None:
    await owned.put("emotion", "state", {"n": 1}, owner="emotion", reason="seed")
    health = await owned.health()

    assert health.detail["documents"] == 1
    assert health.detail["namespaces"] == 2


async def _collect(sink: list[Event], event: Event) -> None:
    sink.append(event)
