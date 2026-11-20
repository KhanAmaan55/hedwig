"""Personality and the relationship, for the inspector (PER-01, docs/16 §4.3).

Behind the token (ADR-0022 §2): drift is evidence about the user, and the relationship is
about them. Emotion stays open in `mind.py`; this does not.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from hedwig.api.auth import require_token
from hedwig.api.deps import ContainerDep
from hedwig.core.errors import DegradedError, InvalidRequestError, NotFoundError
from hedwig.core.ports.personality import Trait

router = APIRouter(prefix="/mind", tags=["mind"], dependencies=[Depends(require_token)])


class TraitRead(BaseModel):
    trait: str
    value: float
    anchor: float
    floor: float
    ceiling: float
    lifetime_drift: float


class PersonalityRead(BaseModel):
    traits: list[TraitRead]
    identity: dict[str, Any]
    version: int


class RelationshipRead(BaseModel):
    familiarity: float
    affinity: float
    trust: float = Field(description="Per-person and evidence-based; not the mood (ADR-0020).")
    interaction_count: int
    last_interaction_at: str | None


@router.get("/personality", response_model=PersonalityRead, summary="Traits, anchors, drift")
async def personality(container: ContainerDep) -> PersonalityRead:
    if container.personality is None:
        raise DegradedError("The personality engine is switched off.")
    profile = await container.personality.profile()
    identity = profile.identity
    return PersonalityRead(
        traits=[
            TraitRead(
                trait=state.trait.value,
                value=round(state.value, 4),
                anchor=state.anchor,
                floor=state.floor,
                ceiling=state.ceiling,
                lifetime_drift=round(state.lifetime_drift, 4),
            )
            for state in profile.traits.values()
        ],
        identity=asdict(identity),
        version=profile.version,
    )


@router.get("/personality/history", summary="Every change, with the evidence")
async def history(
    container: ContainerDep, limit: Annotated[int, Query(ge=1, le=500)] = 50
) -> list[dict[str, object]]:
    if container.persona_control is None:
        raise DegradedError("The personality engine is switched off.")
    return await container.persona_control.history(limit=limit)


@router.post("/personality/revert/{history_id}", summary="Undo one change")
async def revert(history_id: str, container: ContainerDep) -> dict[str, bool]:
    if container.persona_control is None:
        raise DegradedError("The personality engine is switched off.")
    if not await container.persona_control.revert(history_id):
        raise NotFoundError("Nothing to undo there.", history_id=history_id)
    return {"reverted": True}


@router.post("/personality/reset", summary="Put one trait back at its anchor")
async def reset(container: ContainerDep, trait: str) -> dict[str, bool]:
    if container.persona_control is None:
        raise DegradedError("The personality engine is switched off.")
    try:
        chosen = Trait(trait)
    except ValueError as error:
        raise InvalidRequestError(f"No trait called {trait!r}.") from error
    await container.persona_control.reset(chosen)
    return {"reset": True}


@router.get("/relationship", response_model=RelationshipRead, summary="How well HEDWIG knows you")
async def relationship(container: ContainerDep) -> RelationshipRead:
    state = await container.relationship.with_user()
    return RelationshipRead(
        familiarity=round(state.familiarity, 4),
        affinity=round(state.affinity, 4),
        trust=round(state.trust, 4),
        interaction_count=state.interaction_count,
        last_interaction_at=state.last_interaction_at.isoformat(timespec="seconds")
        if state.last_interaction_at
        else None,
    )
