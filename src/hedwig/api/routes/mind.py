"""Cognitive state, read-only (docs/16 §4).

`GET /v1/mind/emotion` and its history. Read-only on purpose: writes go through the owning
module's own commands (docs/08 §3), and a `PATCH /mind/emotion` would be a way to make
HEDWIG claim a mood it does not have.

**On the auth token.** docs/16 §3 says no endpoint exposing anything personal ships without
the bearer token. These two do not: the payload is six floats describing HEDWIG's own state,
plus the name of the rule that moved them. No user text, no memory content, no message
bodies — the store deliberately keeps none of those in these tables. The token still gates
the first endpoint that exposes user content, which is the conversation and memory surface,
and it is not this.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from hedwig.api.deps import ContainerDep

router = APIRouter(prefix="/mind", tags=["mind"])


class Behaviour(BaseModel):
    """What the state currently does. The answer to "so what?" (docs/09 §6)."""

    diversity: float = Field(description="Retrieval MMR lambda for the next turn.")
    token_budget_multiplier: float
    max_tokens_multiplier: float
    style: list[str] = Field(description="Behavioural directives handed to composition.")
    admits_background_work: bool


class EmotionRead(BaseModel):
    happiness: float
    trust: float
    curiosity: float
    confidence: float
    energy: float
    stress: float
    valence: float = Field(description="Derived, not stored (docs/09 §3.2).")
    arousal: float = Field(description="Derived, not stored (docs/09 §3.2).")
    baselines: dict[str, float] = Field(description="Where each dimension is idling toward.")
    behaviour: Behaviour
    updated_at: str | None


class EmotionHistory(BaseModel):
    entries: list[dict[str, Any]]
    count: int


@router.get(
    "/emotion",
    response_model=EmotionRead,
    summary="Current emotional state and what it is doing to behaviour",
)
async def emotion(container: ContainerDep) -> EmotionRead:
    engine = _engine(container)
    snapshot = await engine.snapshot()
    state = snapshot.state

    return EmotionRead(
        **state.as_dict(),
        valence=snapshot.valence,
        arousal=snapshot.arousal,
        baselines=dict(snapshot.baselines),
        behaviour=Behaviour(
            diversity=snapshot.behaviour.diversity,
            token_budget_multiplier=snapshot.behaviour.token_budget_multiplier,
            max_tokens_multiplier=snapshot.behaviour.max_tokens_multiplier,
            style=list(snapshot.behaviour.style),
            admits_background_work=snapshot.behaviour.admits_background_work,
        ),
        updated_at=state.updated_at.isoformat(timespec="milliseconds")
        if state.updated_at
        else None,
    )


@router.get(
    "/emotion/history",
    response_model=EmotionHistory,
    summary="The mood timeline",
)
async def emotion_history(
    container: ContainerDep,
    since: Annotated[datetime | None, Query(description="ISO-8601; inclusive.")] = None,
    limit: Annotated[int, Query(ge=1, le=2000)] = 200,
) -> EmotionHistory:
    engine = _engine(container)
    entries = await engine.history(since=since, limit=limit)
    return EmotionHistory(entries=[dict(entry) for entry in entries], count=len(entries))


def _engine(container: ContainerDep) -> Any:
    """503 rather than a 500 when emotion is switched off.

    It is a legitimate configuration — a user who wants a flat companion — so asking for the
    mood of a system that has none is unavailable, not broken.
    """
    if container.emotion is None:
        raise HTTPException(status_code=503, detail="the emotion engine is disabled")
    return container.emotion
