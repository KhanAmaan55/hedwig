"""The service registry.

Both behaviours worth testing here are about failure: start is transactional, and stop
never raises. A half-started system that keeps running is how you get corruption reports
nobody can reproduce.
"""

from __future__ import annotations

import pytest

from hedwig.core.errors import ConflictError, NotFoundError
from hedwig.core.ports import Health, HealthStatus, ServiceStatus
from hedwig.core.registry import InMemoryServiceRegistry


class Recorder:
    """A service that records its own lifecycle."""

    def __init__(self, log: list[str], name: str, *, fail_on: str | None = None) -> None:
        self._log = log
        self._name = name
        self._fail_on = fail_on

    async def start(self) -> None:
        if self._fail_on == "start":
            raise RuntimeError(f"{self._name} refuses to start")
        self._log.append(f"start:{self._name}")

    async def stop(self) -> None:
        if self._fail_on == "stop":
            raise RuntimeError(f"{self._name} refuses to stop")
        self._log.append(f"stop:{self._name}")

    async def health(self) -> Health:
        return Health(status=HealthStatus.OK, detail={"name": self._name})


class Inert:
    """A service with no lifecycle at all — the common case."""


# -- registration ----------------------------------------------------------


def test_duplicate_registration_is_refused(registry: InMemoryServiceRegistry) -> None:
    registry.register("a", Inert())
    with pytest.raises(ConflictError):
        registry.register("a", Inert())


def test_lookup(registry: InMemoryServiceRegistry) -> None:
    service = Inert()
    registry.register("a", service)

    assert registry.get("a") is service
    assert registry.try_get("a") is service
    assert registry.try_get("absent") is None
    with pytest.raises(NotFoundError):
        registry.get("absent")


async def test_registration_after_start_is_refused(registry: InMemoryServiceRegistry) -> None:
    """The graph is fixed at startup; this is a lifecycle manager, not a discovery service."""
    registry.register("a", Inert())
    await registry.start_all()
    with pytest.raises(ConflictError):
        registry.register("b", Inert())


# -- ordering --------------------------------------------------------------


async def test_services_start_in_dependency_order_and_stop_in_reverse(
    registry: InMemoryServiceRegistry,
) -> None:
    log: list[str] = []
    registry.register("database", Recorder(log, "database"))
    registry.register("bus", Recorder(log, "bus"), depends_on=("database",))
    registry.register("state", Recorder(log, "state"), depends_on=("bus",))

    await registry.start_all()
    await registry.stop_all()

    assert log == [
        "start:database",
        "start:bus",
        "start:state",
        "stop:state",
        "stop:bus",
        "stop:database",
    ]


def test_registration_order_is_the_tiebreak(registry: InMemoryServiceRegistry) -> None:
    """A start order that varies between runs makes startup bugs unreproducible."""
    for name in ("first", "second", "third"):
        registry.register(name, Inert())

    assert registry.resolve_order() == ["first", "second", "third"]


def test_missing_dependency_is_named(registry: InMemoryServiceRegistry) -> None:
    registry.register("bus", Inert(), depends_on=("database",))
    with pytest.raises(NotFoundError, match="database"):
        registry.resolve_order()


def test_circular_dependency_is_detected(registry: InMemoryServiceRegistry) -> None:
    registry.register("a", Inert(), depends_on=("b",))
    registry.register("b", Inert(), depends_on=("a",))
    with pytest.raises(ConflictError, match="circular"):
        registry.resolve_order()


# -- failure ---------------------------------------------------------------


async def test_a_failing_critical_service_unwinds_everything_started(
    registry: InMemoryServiceRegistry,
) -> None:
    """Partial startup is never left running."""
    log: list[str] = []
    registry.register("database", Recorder(log, "database"))
    registry.register("bus", Recorder(log, "bus", fail_on="start"), depends_on=("database",))
    registry.register("state", Recorder(log, "state"), depends_on=("bus",))

    with pytest.raises(RuntimeError):
        await registry.start_all()

    assert log == ["start:database", "stop:database"]
    assert "start:state" not in log


async def test_a_failing_optional_service_degrades_rather_than_aborts(
    registry: InMemoryServiceRegistry,
) -> None:
    log: list[str] = []
    registry.register("database", Recorder(log, "database"))
    registry.register("avatar", Recorder(log, "avatar", fail_on="start"), critical=False)
    registry.register("state", Recorder(log, "state"))

    await registry.start_all()

    assert "start:database" in log
    assert "start:state" in log
    statuses = {info.name: info.status for info in registry.info()}
    assert statuses["avatar"] is ServiceStatus.FAILED
    assert statuses["state"] is ServiceStatus.RUNNING


async def test_stop_never_raises(registry: InMemoryServiceRegistry) -> None:
    """A service that throws on stop must not strand the ones behind it."""
    log: list[str] = []
    registry.register("first", Recorder(log, "first"))
    registry.register("stubborn", Recorder(log, "stubborn", fail_on="stop"))
    registry.register("last", Recorder(log, "last"))

    await registry.start_all()
    await registry.stop_all()  # must not raise

    assert "stop:last" in log
    assert "stop:first" in log


# -- health ----------------------------------------------------------------


async def test_health_collects_from_reporting_services(
    registry: InMemoryServiceRegistry,
) -> None:
    registry.register("reports", Recorder([], "reports"))
    registry.register("silent", Inert())
    await registry.start_all()

    health = await registry.health()

    assert set(health) == {"reports"}
    assert health["reports"].status is HealthStatus.OK


async def test_a_health_probe_that_raises_reports_down(
    registry: InMemoryServiceRegistry,
) -> None:
    """A health endpoint that can fail is not a health endpoint."""

    class Broken:
        async def health(self) -> Health:
            raise RuntimeError("probe exploded")

    registry.register("broken", Broken())
    await registry.start_all()

    health = await registry.health()
    assert health["broken"].status is HealthStatus.DOWN


async def test_a_failed_service_reports_down_without_being_probed(
    registry: InMemoryServiceRegistry,
) -> None:
    registry.register("avatar", Recorder([], "avatar", fail_on="start"), critical=False)
    await registry.start_all()

    health = await registry.health()
    assert health["avatar"].status is HealthStatus.DOWN


async def test_start_is_idempotent(registry: InMemoryServiceRegistry) -> None:
    log: list[str] = []
    registry.register("a", Recorder(log, "a"))

    await registry.start_all()
    await registry.start_all()

    assert log == ["start:a"]
