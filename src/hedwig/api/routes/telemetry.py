"""What happened, and is HEDWIG healthy? (docs/16 §4.5, docs/19 §13; OBS-01)"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse

from hedwig.api.auth import require_token
from hedwig.api.deps import ContainerDep
from hedwig.telemetry import render_metrics

router = APIRouter(tags=["system"], dependencies=[Depends(require_token)])

PROMETHEUS = "text/plain; version=0.0.4; charset=utf-8"


@router.get("/traces", summary="Recent turns and scheduled runs, to trace")
async def recent(
    container: ContainerDep, limit: Annotated[int, Query(ge=1, le=200)] = 20
) -> dict[str, Any]:
    return {"traces": container.traces.recent(limit=limit)}


@router.get("/traces/{correlation_id}", summary="Where the time went in one turn or job")
async def trace(correlation_id: str, container: ContainerDep) -> dict[str, Any]:
    """Stages, the handlers the turn set off, model and tool calls, events: one timeline."""
    return container.traces.trace(correlation_id)


@router.get(
    "/metrics",
    summary="Prometheus text: health and cognitive metrics (docs/19 §6)",
    response_class=PlainTextResponse,
)
async def metrics(container: ContainerDep) -> PlainTextResponse:
    gateway = getattr(container.llm, "metrics", None)
    stats = getattr(container.bus, "stats", None)
    text = render_metrics(
        container.database,
        now=container.clock.now(),
        uptime_seconds=container.uptime_seconds(),
        gateway=gateway.prometheus() if gateway is not None else "",
        bus=stats() if callable(stats) else None,
    )
    return PlainTextResponse(text, media_type=PROMETHEUS)
