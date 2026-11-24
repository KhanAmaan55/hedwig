"""What HEDWIG said unprompted, and why it stayed quiet (docs/11 §14, ADR-0026)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from hedwig.api.auth import require_token
from hedwig.api.deps import ContainerDep

router = APIRouter(prefix="/initiative", tags=["initiative"], dependencies=[Depends(require_token)])


@router.get("", summary="Recent unprompted messages, and the reason for the last silence")
async def status(container: ContainerDep) -> dict[str, Any]:
    return container.initiative.status()


@router.post("/check", summary="Check now whether HEDWIG has something worth saying")
async def check(container: ContainerDep) -> dict[str, Any]:
    """Every gate still applies: a manual check never speaks into a conversation in progress."""
    return await container.initiative.check()


@router.post("/{offer_id}/dismiss", summary="Not interested")
async def dismiss(offer_id: str, container: ContainerDep) -> dict[str, Any]:
    """The finding is dismissed, or the goal gets no more check-ins."""
    return await container.initiative.dismiss(offer_id)
