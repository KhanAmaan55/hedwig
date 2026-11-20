"""Why HEDWIG said that (docs/16 §6, §14). The question and the reply are user content."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from hedwig.api.auth import require_token
from hedwig.api.deps import ContainerDep
from hedwig.explain import Explainer

router = APIRouter(prefix="/explain", tags=["introspection"], dependencies=[Depends(require_token)])


@router.get("/{message_id}", summary="Everything that went into one reply")
async def explain(message_id: str, container: ContainerDep) -> dict[str, Any]:
    return await Explainer(container.database, container.memory).explain(message_id)
