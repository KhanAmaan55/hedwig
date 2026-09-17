"""Service registry: lifecycle and lookup.

`wiring.py` constructs the object graph; this manages what happens to it afterwards
(see `hedwig.core.ports.registry` for why those are separate concerns).

The two behaviours worth stating plainly, because both are about failure:

* **Start is transactional.** If a critical service fails to start, everything already
  started is stopped in reverse order and the error propagates. A half-started system that
  keeps running is how you get corruption reports nobody can reproduce.
* **Stop never raises.** Shutdown has to finish. A service that throws on stop is logged and
  skipped, not allowed to strand the ones behind it.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from hedwig.core.errors import ConflictError, NotFoundError
from hedwig.core.logging import fields, get_logger
from hedwig.core.ports import (
    Health,
    HealthReporting,
    HealthStatus,
    Lifecycle,
    ServiceInfo,
    ServiceStatus,
)

logger = get_logger(__name__)

HEALTH_TIMEOUT_SECONDS = 5.0


@dataclass(slots=True)
class _Entry:
    name: str
    service: object
    depends_on: tuple[str, ...]
    critical: bool
    status: ServiceStatus = ServiceStatus.REGISTERED
    started_at: float | None = None
    error: str | None = None
    order: int = field(default=0)


class InMemoryServiceRegistry:
    """The `ServiceRegistry` implementation.

    In-memory because the set of services is fixed at startup: this is a lifecycle manager,
    not a discovery mechanism. Nothing looks a service up by name at runtime except
    diagnostics and plugins.
    """

    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}
        self._started = False
        self._start_order: list[str] = []

    # -- registration ------------------------------------------------------

    def register(
        self,
        name: str,
        service: object,
        *,
        depends_on: Sequence[str] = (),
        critical: bool = True,
    ) -> None:
        if self._started:
            raise ConflictError(
                f"cannot register {name!r}: the registry is already started", service=name
            )
        if name in self._entries:
            raise ConflictError(f"service {name!r} is already registered", service=name)

        self._entries[name] = _Entry(
            name=name,
            service=service,
            depends_on=tuple(depends_on),
            critical=critical,
            order=len(self._entries),
        )
        logger.debug(
            "service registered",
            extra=fields(service=name, depends_on=list(depends_on), critical=critical),
        )

    def get(self, name: str) -> object:
        entry = self._entries.get(name)
        if entry is None:
            raise NotFoundError(f"service {name!r} is not registered", service=name)
        return entry.service

    def try_get(self, name: str) -> object | None:
        entry = self._entries.get(name)
        return entry.service if entry else None

    def names(self) -> Sequence[str]:
        return tuple(self._entries)

    def info(self) -> Sequence[ServiceInfo]:
        return tuple(
            ServiceInfo(
                name=entry.name,
                status=entry.status,
                depends_on=entry.depends_on,
                started_at=entry.started_at,
                error=entry.error,
            )
            for entry in self._entries.values()
        )

    # -- ordering ----------------------------------------------------------

    def resolve_order(self) -> list[str]:
        """Topological sort: dependencies first, registration order as the tiebreak.

        The stable tiebreak matters more than it looks — a start order that varies between
        runs makes intermittent startup bugs impossible to reproduce.
        """
        unresolved = {name: set(entry.depends_on) for name, entry in self._entries.items()}

        for name, dependencies in unresolved.items():
            missing = dependencies - self._entries.keys()
            if missing:
                raise NotFoundError(
                    f"service {name!r} depends on unregistered service(s): {sorted(missing)}",
                    service=name,
                    missing=sorted(missing),
                )

        ordered: list[str] = []
        while unresolved:
            ready = sorted(
                (name for name, deps in unresolved.items() if not deps),
                key=lambda name: self._entries[name].order,
            )
            if not ready:
                raise ConflictError(
                    f"circular service dependency among {sorted(unresolved)}",
                    services=sorted(unresolved),
                )
            for name in ready:
                ordered.append(name)
                del unresolved[name]
            for dependencies in unresolved.values():
                dependencies.difference_update(ready)
        return ordered

    # -- lifecycle ---------------------------------------------------------

    async def start_all(self) -> None:
        if self._started:
            return
        order = self.resolve_order()
        self._started = True

        for name in order:
            entry = self._entries[name]
            service = entry.service
            if not isinstance(service, Lifecycle):
                entry.status = ServiceStatus.RUNNING
                self._start_order.append(name)
                continue

            entry.status = ServiceStatus.STARTING
            started = time.monotonic()
            try:
                await service.start()
            except Exception as exc:
                entry.status = ServiceStatus.FAILED
                entry.error = f"{type(exc).__name__}: {exc}"
                logger.error(
                    "service failed to start", extra=fields(service=name, error=entry.error)
                )

                if entry.critical:
                    await self._stop_started()
                    self._started = False
                    raise
                continue

            entry.status = ServiceStatus.RUNNING
            entry.started_at = started
            self._start_order.append(name)
            logger.info(
                "service started",
                extra={
                    "duration_ms": round((time.monotonic() - started) * 1000, 1),
                    "fields": {"service": name},
                },
            )

        logger.info("all services started", extra=fields(count=len(self._start_order)))

    async def stop_all(self) -> None:
        if not self._started:
            return
        await self._stop_started()
        self._started = False
        logger.info("all services stopped")

    async def _stop_started(self) -> None:
        for name in reversed(self._start_order):
            entry = self._entries[name]
            service = entry.service
            if not isinstance(service, Lifecycle):
                entry.status = ServiceStatus.STOPPED
                continue

            entry.status = ServiceStatus.STOPPING
            try:
                await service.stop()
            except Exception as exc:  # never let one failure strand the rest
                entry.error = f"{type(exc).__name__}: {exc}"
                logger.error(
                    "service failed to stop", extra=fields(service=name, error=entry.error)
                )
            entry.status = ServiceStatus.STOPPED
        self._start_order.clear()

    # -- health ------------------------------------------------------------

    async def health(self) -> Mapping[str, Health]:
        """Poll every health-reporting service.

        A probe that raises or hangs reports DOWN rather than propagating: a health endpoint
        that can fail is not a health endpoint.
        """
        import asyncio

        results: dict[str, Health] = {}
        for name, entry in self._entries.items():
            service = entry.service
            if entry.status is ServiceStatus.FAILED:
                results[name] = Health(
                    status=HealthStatus.DOWN, message=entry.error or "failed to start"
                )
                continue
            if not isinstance(service, HealthReporting):
                continue
            try:
                results[name] = await asyncio.wait_for(
                    service.health(), timeout=HEALTH_TIMEOUT_SECONDS
                )
            except Exception as exc:
                results[name] = Health(
                    status=HealthStatus.DOWN, message=f"health probe failed: {exc}"
                )
        return results
