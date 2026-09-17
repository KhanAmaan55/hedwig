"""FastAPI dependencies.

The container is attached to `app.state` at construction and read from the request here,
so route handlers depend on ports rather than on module-level globals — and a test can
build an app around a container with a `FakeClock` and no patching.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from hedwig.core.config import Config
from hedwig.core.ports import Clock
from hedwig.wiring import Container


def get_container(request: Request) -> Container:
    container = getattr(request.app.state, "container", None)
    if container is None:  # pragma: no cover - impossible via create_app
        raise RuntimeError("application was created without a container")
    return container  # type: ignore[no-any-return]


def get_config(container: Annotated[Container, Depends(get_container)]) -> Config:
    return container.config


def get_clock(container: Annotated[Container, Depends(get_container)]) -> Clock:
    return container.clock


ContainerDep = Annotated[Container, Depends(get_container)]
ConfigDep = Annotated[Config, Depends(get_config)]
ClockDep = Annotated[Clock, Depends(get_clock)]
