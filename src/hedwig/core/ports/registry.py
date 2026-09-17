"""The `ServiceRegistry` port.

`wiring.py` builds the object graph (docs/03 §6); the registry manages what happens to it
afterwards — start order, stop order, and health.

The distinction matters. Construction is static and belongs in one readable function.
Lifecycle is dynamic: services start in dependency order, stop in reverse, and a service
that fails to start must not leave half the system running. Putting that in the wiring
function would turn a list of constructors into a state machine.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class ServiceStatus(StrEnum):
    REGISTERED = "registered"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class HealthStatus(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"


@dataclass(frozen=True, slots=True)
class Health:
    """What a service reports about itself.

    `detail` is free-form and ends up in `/v1/health`, so it should be small, factual, and
    free of anything private (docs/19 §7).
    """

    status: HealthStatus
    detail: Mapping[str, Any] = field(default_factory=dict)
    message: str = ""


@dataclass(frozen=True, slots=True)
class ServiceInfo:
    name: str
    status: ServiceStatus
    depends_on: tuple[str, ...]
    started_at: float | None
    error: str | None


@runtime_checkable
class Lifecycle(Protocol):
    """Implemented by services that need to do something on start or stop.

    Optional: a service with no lifecycle is registered and simply never called here.
    """

    async def start(self) -> None: ...

    async def stop(self) -> None: ...


@runtime_checkable
class HealthReporting(Protocol):
    """Implemented by services that can describe their own condition."""

    async def health(self) -> Health: ...


@runtime_checkable
class ServiceRegistry(Protocol):
    """Lifecycle and lookup for long-lived services."""

    def register(
        self,
        name: str,
        service: object,
        *,
        depends_on: Sequence[str] = (),
        critical: bool = True,
    ) -> None:
        """Add a service.

        `depends_on` names services that must be running first; stop happens in reverse.
        `critical=False` means a start failure degrades the system instead of aborting it —
        the difference between "the database is gone" and "the avatar emitter is unhappy".

        Raises `ConflictError` on a duplicate name, or if the registry is already started.
        """
        ...

    def get(self, name: str) -> object:
        """Look up a service by name. Raises `NotFoundError` if absent."""
        ...

    def try_get(self, name: str) -> object | None: ...

    def names(self) -> Sequence[str]: ...

    def info(self) -> Sequence[ServiceInfo]: ...

    async def start_all(self) -> None:
        """Start every service in dependency order.

        A failing critical service stops everything already started, in reverse order, and
        re-raises. Partial startup is never left running.
        """
        ...

    async def stop_all(self) -> None:
        """Stop in reverse dependency order. Never raises: shutdown must finish."""
        ...

    async def health(self) -> Mapping[str, Health]:
        """Poll every health-reporting service. Never raises; a probe that fails is DOWN."""
        ...
