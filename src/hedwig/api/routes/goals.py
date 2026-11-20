"""Goals (docs/16 §4.3, docs/08 §6.1). About the user's projects, so behind the token."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field

from hedwig.api.auth import require_token
from hedwig.api.deps import ContainerDep
from hedwig.core.errors import NotFoundError
from hedwig.core.ports.goals import Goal, GoalStatus

router = APIRouter(prefix="/goals", tags=["goals"], dependencies=[Depends(require_token)])

Status = Literal["proposed", "active", "blocked", "done", "abandoned"]


class GoalRead(BaseModel):
    id: str
    description: str
    kind: str
    status: Status
    priority: float
    progress: float
    context: str
    completion: str
    origin: str = Field(description="user, reflection or system.")
    created_at: str
    last_touched_at: str


class GoalList(BaseModel):
    goals: list[GoalRead]


class GoalCreate(BaseModel):
    description: str = Field(min_length=1, max_length=500)
    context: str = Field(default="", max_length=2000)
    completion: str = Field(default="", max_length=500)
    priority: float = Field(default=0.5, ge=0, le=1)


class GoalPatch(BaseModel):
    status: Status | None = None
    progress: float | None = Field(default=None, ge=0, le=1)
    priority: float | None = Field(default=None, ge=0, le=1)
    description: str | None = Field(default=None, min_length=1, max_length=500)
    context: str | None = Field(default=None, max_length=2000)
    completion: str | None = Field(default=None, max_length=500)


@router.get("", response_model=GoalList, summary="Goals, highest priority first")
async def list_goals(
    container: ContainerDep,
    status_filter: Annotated[list[Status] | None, Query(alias="status")] = None,
) -> GoalList:
    statuses = [GoalStatus(value) for value in status_filter] if status_filter else None
    return GoalList(goals=[_read(g) for g in await container.goals.list(statuses=statuses)])


@router.post("", response_model=GoalRead, status_code=status.HTTP_201_CREATED)
async def create_goal(body: GoalCreate, container: ContainerDep) -> GoalRead:
    """A goal the user sets is active at once; it is theirs (docs/08 §6.1)."""
    goal = await container.goals.create(
        body.description,
        context=body.context,
        completion=body.completion,
        priority=body.priority,
    )
    return _read(goal)


@router.get("/{goal_id}", response_model=GoalRead)
async def get_goal(goal_id: str, container: ContainerDep) -> GoalRead:
    goal = await container.goals.get(goal_id)
    if goal is None:
        raise NotFoundError("No such goal.", goal_id=goal_id)
    return _read(goal)


@router.patch("/{goal_id}", response_model=GoalRead, summary="Activate, progress, close")
async def update_goal(goal_id: str, body: GoalPatch, container: ContainerDep) -> GoalRead:
    goal = await container.goals.update(
        goal_id,
        status=GoalStatus(body.status) if body.status else None,
        progress=body.progress,
        priority=body.priority,
        description=body.description,
        context=body.context,
        completion=body.completion,
    )
    return _read(goal)


def _read(goal: Goal) -> GoalRead:
    return GoalRead(
        id=goal.id,
        description=goal.description,
        kind=goal.kind.value,
        status=goal.status.value,
        priority=goal.priority,
        progress=goal.progress,
        context=goal.context,
        completion=goal.completion,
        origin=goal.origin,
        created_at=goal.created_at.isoformat(timespec="milliseconds"),
        last_touched_at=goal.last_touched_at.isoformat(timespec="milliseconds"),
    )
