"""Nightly reflection, on demand and on the record (docs/16 §4.4, docs/12 §15)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from hedwig.api.auth import require_token
from hedwig.api.deps import ContainerDep

router = APIRouter(prefix="/reflection", tags=["reflection"], dependencies=[Depends(require_token)])


@router.post("/run", summary="Run tonight's consolidation now")
async def run(container: ContainerDep) -> dict[str, Any]:
    """A manual run still yields to a conversation in progress; it resumes where it stopped."""
    return await container.nightly.run()


@router.get("/runs", summary="Past runs: what each stage did, and where an interrupted one stopped")
async def runs(
    container: ContainerDep, limit: Annotated[int, Query(ge=1, le=200)] = 20
) -> list[dict[str, Any]]:
    return container.nightly.runs(limit=limit)


class Resolution(BaseModel):
    choice: Literal["first", "second", "both", "same"]


@router.get("/disputes", summary="Beliefs HEDWIG thinks repeat or conflict, waiting for you")
async def disputes(container: ContainerDep) -> dict[str, Any]:
    """The utility model's proposals; nothing here has changed a memory yet (docs/12 §15)."""
    return {"disputes": await container.nightly.disputes()}


@router.post("/disputes/{pair}/resolve", summary="Say which is true")
async def resolve(pair: str, body: Resolution, container: ContainerDep) -> dict[str, Any]:
    return await container.nightly.resolve(pair, body.choice)


@router.post("/weekly/run", summary="Review last week now (once per week)")
async def weekly(container: ContainerDep) -> dict[str, Any]:
    return await container.periodic.weekly()


@router.post("/monthly/run", summary="Make last month's identity report now (once per month)")
async def monthly(container: ContainerDep) -> dict[str, Any]:
    return await container.periodic.monthly()


@router.get("/reports", summary="Monthly identity reports, newest first")
async def reports(container: ContainerDep) -> dict[str, Any]:
    return {"reports": container.periodic.reports()}


@router.get("/reports/{month}", summary="One month's identity report, as things stand now")
async def report(month: str, container: ContainerDep) -> dict[str, Any]:
    return await container.periodic.report(month)


@router.post("/reports/{month}/seen", summary="Mark a report as read")
async def seen(month: str, container: ContainerDep) -> dict[str, bool]:
    container.periodic.mark_seen(month)
    return {"seen": True}
