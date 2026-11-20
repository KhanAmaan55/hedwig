"""Curiosity (docs/16 §4.4, docs/11 §13): the gap register and the findings feed."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field

from hedwig.api.auth import require_token
from hedwig.api.deps import ContainerDep
from hedwig.core.errors import InvalidRequestError
from hedwig.curiosity import Finding, Gap, feed

router = APIRouter(prefix="/curiosity", tags=["curiosity"], dependencies=[Depends(require_token)])


class GapRead(BaseModel):
    id: str
    question: str
    origin: str
    priority: float
    status: str
    attempts: int
    created_at: str


class FindingRead(BaseModel):
    id: str
    question: str
    summary: str
    sources: list[dict[str, str]]
    status: str
    trust: str = Field(default="untrusted", description="Always: read on the web.")
    created_at: str


class GapCreate(BaseModel):
    question: str = Field(min_length=8, max_length=300)


class ExploreResult(BaseModel):
    status: str
    gap_id: str | None
    finding_id: str | None


@router.get("/gaps", response_model=list[GapRead], summary="What HEDWIG knows it does not know")
async def gaps(
    container: ContainerDep,
    status_filter: Annotated[list[str] | None, Query(alias="status")] = None,
) -> list[GapRead]:
    found = await container.curiosity.gaps(statuses=tuple(status_filter or ("open", "exploring")))
    return [_gap(gap) for gap in found]


@router.post("/gaps", response_model=GapRead, status_code=status.HTTP_201_CREATED)
async def ask(body: GapCreate, container: ContainerDep) -> GapRead:
    gap = await container.curiosity.record_gap(body.question, origin="user")
    if gap is None:
        raise InvalidRequestError(
            "That question mentions you, so HEDWIG will not send it to a search engine. "
            "Ask it in general terms."
        )
    return _gap(gap)


@router.get("/findings", response_model=list[FindingRead], summary="The findings feed")
async def findings(
    container: ContainerDep,
    status_filter: Annotated[list[str] | None, Query(alias="status")] = None,
) -> list[FindingRead]:
    statuses = tuple(status_filter) if status_filter else ("new", "surfaced", "confirmed")
    return [_finding(f) for f in await container.curiosity.findings(statuses=statuses)]


@router.post("/findings/{finding_id}/confirm", response_model=FindingRead)
async def confirm(finding_id: str, container: ContainerDep) -> FindingRead:
    return _finding(await feed.confirm(container.curiosity, container.capture, finding_id))


@router.post("/findings/{finding_id}/dismiss", response_model=FindingRead)
async def dismiss(finding_id: str, container: ContainerDep) -> FindingRead:
    return _finding(await feed.dismiss(container.curiosity, finding_id))


@router.post("/explore", response_model=ExploreResult, summary="Explore one gap now")
async def explore(container: ContainerDep) -> ExploreResult:
    """Skips idleness and quiet hours, never the opt-ins or the daily budget."""
    result = await container.explorer.explore(force=True)
    return ExploreResult(status=result.status, gap_id=result.gap_id, finding_id=result.finding_id)


def _gap(gap: Gap) -> GapRead:
    return GapRead(
        id=gap.id,
        question=gap.question,
        origin=gap.origin,
        priority=gap.priority,
        status=gap.status,
        attempts=gap.attempt_count,
        created_at=gap.created_at.isoformat(timespec="milliseconds"),
    )


def _finding(finding: Finding) -> FindingRead:
    return FindingRead(
        id=finding.id,
        question=finding.question,
        summary=finding.summary,
        sources=list(finding.sources),
        status=finding.status,
        created_at=finding.created_at.isoformat(timespec="milliseconds"),
    )
