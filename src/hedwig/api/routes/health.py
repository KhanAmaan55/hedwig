"""Health and readiness (docs/19 §7).

The response shape is the one the documentation specifies, with only the subsystems that
exist today. Later milestones add entries to `subsystems` and `notices`; nothing about the
contract changes.

`notices` is the user-facing channel for anything that changes behaviour in a way someone
could notice. Silent degradation is the failure this endpoint exists to prevent.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from hedwig.api.deps import ContainerDep
from hedwig.core.ports import PluginStatus

router = APIRouter(tags=["system"])


class Status(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"


_SEVERITY = {Status.OK: 0, Status.DEGRADED: 1, Status.DOWN: 2}


class Subsystem(BaseModel):
    status: Status
    detail: dict[str, Any] = Field(default_factory=dict)


class Notice(BaseModel):
    level: Literal["info", "warn", "error"]
    code: str
    message: str


class Health(BaseModel):
    status: Status
    version: str
    schema_version: int | None = Field(
        default=None, description="Database schema version; null until the store lands."
    )
    environment: str
    started_at: str
    uptime_s: float
    subsystems: dict[str, Subsystem]
    notices: list[Notice]


@router.get(
    "/health",
    response_model=Health,
    summary="Liveness and per-subsystem status",
    responses={503: {"description": "One or more subsystems are unavailable"}},
)
async def health(container: ContainerDep, response: Response) -> Health:
    subsystems = _collect_subsystems(container)
    subsystems.update(await _collect_services(container))
    notices = _collect_notices(container)

    overall = max(
        (s.status for s in subsystems.values()),
        key=lambda status: _SEVERITY[status],
        default=Status.OK,
    )
    # A degraded HEDWIG still answers; only a down one is unavailable.
    if overall is Status.DOWN:
        response.status_code = 503

    return Health(
        status=overall,
        version=container.version,
        environment=container.config.runtime.environment,
        started_at=container.started_at.isoformat(timespec="milliseconds"),
        uptime_s=round(container.uptime_seconds(), 3),
        subsystems=subsystems,
        notices=notices,
    )


def _collect_subsystems(container: ContainerDep) -> dict[str, Subsystem]:
    """Report the things that are not registered services.

    Everything registered reports itself through `_collect_services`. This covers the edge
    and the database, which exist before any service does.
    """
    data_dir = container.config.runtime.data_dir
    database = container.database
    return {
        "api": Subsystem(status=Status.OK),
        "data_dir": Subsystem(
            status=Status.OK if data_dir.is_dir() else Status.DEGRADED,
            detail={"path": str(data_dir), "present": data_dir.is_dir()},
        ),
        "database": Subsystem(
            status=Status.OK,
            detail={"path": str(database.path), "size_bytes": database.size_bytes()},
        ),
    }


async def _collect_services(container: ContainerDep) -> dict[str, Subsystem]:
    """Ask every registered service how it is.

    The registry never raises here: a probe that fails or hangs is reported as DOWN, because
    a health endpoint that can fail is not a health endpoint.
    """
    reported = await container.registry.health()
    return {
        name: Subsystem(status=Status(health.status.value), detail=dict(health.detail))
        for name, health in reported.items()
    }


def _collect_notices(container: ContainerDep) -> list[Notice]:
    """Anything that changes behaviour in a way the user could notice.

    Silent degradation is the failure this exists to prevent (docs/19 §7).
    """
    notices: list[Notice] = []

    if not container.config.runtime.data_dir.is_dir():
        notices.append(
            Notice(
                level="warn",
                code="data_dir_missing",
                message=(
                    f"Data directory {container.config.runtime.data_dir} does not exist; "
                    "file logging and persistence are unavailable."
                ),
            )
        )

    for service in container.registry.info():
        if service.error is not None:
            notices.append(
                Notice(
                    level="error",
                    code="service_failed",
                    message=f"Service {service.name!r} reported: {service.error}",
                )
            )

    for record in container.plugins.records():
        if record.status is PluginStatus.FAILED:
            notices.append(
                Notice(
                    level="error" if record.manifest.required else "warn",
                    code="plugin_failed",
                    message=f"Plugin {record.manifest.name!r} failed: {record.error}",
                )
            )

    return notices
