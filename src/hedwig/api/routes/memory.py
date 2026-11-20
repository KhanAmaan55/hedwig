"""What HEDWIG remembers, and the user's hand on it (docs/16 §4.2, docs/06 §16).

User content, so behind the token (ADR-0022). Translation only: the store and retrieval do the
work. Inspection never reinforces — search goes to retrieval directly and logs no access.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from hedwig.api.auth import require_token
from hedwig.api.deps import ContainerDep
from hedwig.core.errors import ConflictError, InvalidRequestError, NotFoundError
from hedwig.core.ports.memory import Belief, Episode, ForgetReason, RetrievalPolicy
from hedwig.core.types import MemoryId

router = APIRouter(prefix="/memory", tags=["memory"], dependencies=[Depends(require_token)])


class MemoryRead(BaseModel):
    id: str
    kind: Literal["episode", "belief"]
    text: str
    occurred_at: str
    salience: float
    confidence: float
    pinned: bool
    forgotten: bool
    trust: str = Field(description="Whose word this rests on (docs/13 §3).")
    source: str | None
    status: str | None = Field(default=None, description="Beliefs only: tentative, active, …")
    replaced_by: str | None = Field(
        default=None, description="Beliefs only: the belief that superseded this one."
    )


class Correction(BaseModel):
    text: Annotated[str | None, Field(min_length=1, max_length=2000)] = None
    confidence: Annotated[float | None, Field(ge=0, le=1)] = None


class MemoryList(BaseModel):
    memories: list[MemoryRead]


class SearchHit(BaseModel):
    memory: MemoryRead
    score: float
    breakdown: dict[str, float] = Field(description="Why it ranked where it did.")


class SearchResult(BaseModel):
    query: str
    hits: list[SearchHit]


@router.get("", response_model=MemoryList, summary="Recent memories, newest first")
async def browse(
    container: ContainerDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    include_forgotten: bool = False,
) -> MemoryList:
    memories = await container.memory.browse(limit=limit, include_forgotten=include_forgotten)
    return MemoryList(memories=[_read(memory) for memory in memories])


@router.get("/search", response_model=SearchResult, summary="Search as HEDWIG recalls")
async def search(
    container: ContainerDep,
    q: Annotated[str, Query(min_length=1, max_length=500)],
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> SearchResult:
    working_set = await container.retrieval.search(
        [q], policy=RetrievalPolicy(max_items=limit, token_budget=100_000)
    )
    hits: list[SearchHit] = []
    for item in working_set.items:
        memory = await container.memory.inspect(item.memory_id)
        if memory is not None:
            hits.append(
                SearchHit(
                    memory=_read(memory),
                    score=round(item.score, 6),
                    breakdown={k: round(v, 6) for k, v in item.score_breakdown.items()},
                )
            )
    return SearchResult(query=q, hits=hits)


@router.get("/{memory_id}", response_model=MemoryRead, summary="One memory, forgotten or not")
async def show(memory_id: str, container: ContainerDep) -> MemoryRead:
    return _read(await _require(container, memory_id))


@router.patch("/{memory_id}", response_model=MemoryRead, summary="Correct a memory")
async def correct(memory_id: str, body: Correction, container: ContainerDep) -> MemoryRead:
    """The correction replaces the memory, in the user's words and with their trust; the old
    version is kept as history (docs/06 §18). Returns the replacement, which has a new id."""
    if body.text is None and body.confidence is None:
        raise InvalidRequestError("Send the corrected text, a confidence, or both.")
    new = await container.memory.correct(
        MemoryId(memory_id), text=body.text, confidence=body.confidence
    )
    return _read(await _require(container, new))


@router.post("/{memory_id}/pin", response_model=MemoryRead, summary="Never forget this")
async def pin(memory_id: str, container: ContainerDep) -> MemoryRead:
    await _require(container, memory_id)
    await container.memory.set_pinned(MemoryId(memory_id), True)
    return _read(await _require(container, memory_id))


@router.delete("/{memory_id}/pin", response_model=MemoryRead, summary="Let it fade normally")
async def unpin(memory_id: str, container: ContainerDep) -> MemoryRead:
    await _require(container, memory_id)
    await container.memory.set_pinned(MemoryId(memory_id), False)
    return _read(await _require(container, memory_id))


@router.post("/{memory_id}/forget", response_model=MemoryRead, summary="Forget, reversibly")
async def forget(memory_id: str, container: ContainerDep) -> MemoryRead:
    memory = await _require(container, memory_id)
    if memory.pinned:
        raise ConflictError(
            "This memory is pinned. Unpin it first if you want it forgotten.",
            memory_id=memory_id,
        )
    if memory.tombstoned_at is None:
        await container.memory.tombstone(MemoryId(memory_id), ForgetReason.USER_REQUEST)
    return _read(await _require(container, memory_id))


@router.post("/{memory_id}/restore", response_model=MemoryRead, summary="Undo a forget")
async def restore(memory_id: str, container: ContainerDep) -> MemoryRead:
    await _require(container, memory_id)
    await container.memory.restore(MemoryId(memory_id))
    return _read(await _require(container, memory_id))


# -- helpers ---------------------------------------------------------------------


async def _require(container: ContainerDep, memory_id: str) -> Episode | Belief:
    memory = await container.memory.inspect(MemoryId(memory_id))
    if memory is None:
        raise NotFoundError("No such memory.", memory_id=memory_id)
    return memory


def _read(memory: Episode | Belief) -> MemoryRead:
    if isinstance(memory, Episode):
        return MemoryRead(
            id=memory.id,
            kind="episode",
            text=memory.content,
            occurred_at=_iso(memory.occurred_at),
            salience=memory.salience,
            confidence=memory.confidence,
            pinned=memory.pinned,
            forgotten=memory.tombstoned_at is not None,
            trust=memory.provenance.tier.value,
            source=memory.provenance.source_ref,
        )
    return MemoryRead(
        id=memory.id,
        kind="belief",
        text=memory.statement,
        occurred_at=_iso(memory.valid_from),
        salience=memory.salience,
        confidence=memory.confidence,
        pinned=memory.pinned,
        forgotten=memory.tombstoned_at is not None,
        trust=memory.provenance.tier.value,
        source=memory.provenance.source_ref,
        status=memory.status.value,
        replaced_by=memory.superseded_by,
    )


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds")
