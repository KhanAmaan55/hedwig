"""Corrections proposed in conversation, waiting for the user (docs/06 §19, MEM-07)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query

from hedwig.api.auth import require_token
from hedwig.api.deps import ContainerDep

router = APIRouter(prefix="/corrections", tags=["memory"], dependencies=[Depends(require_token)])


@router.get("", summary="Corrections HEDWIG heard in conversation")
async def proposals(
    container: ContainerDep,
    status: Literal["proposed", "accepted", "declined", "stale", "all"] = "proposed",
    session_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> dict[str, Any]:
    return {
        "proposals": container.corrections.proposals(
            status=None if status == "all" else status, session_id=session_id, limit=limit
        )
    }


@router.post("/{proposal_id}/accept", summary="Yes, correct it")
async def accept(proposal_id: str, container: ContainerDep) -> dict[str, Any]:
    """The belief is replaced in the proposed words (docs/06 §18); the old is kept as history."""
    return await container.corrections.accept(proposal_id)


@router.post("/{proposal_id}/decline", summary="No: nothing changes")
async def decline(proposal_id: str, container: ContainerDep) -> dict[str, Any]:
    return await container.corrections.decline(proposal_id)
