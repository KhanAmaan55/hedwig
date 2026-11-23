"""The user's documents (docs/16 §4.4, docs/13 §14; KNW-01). Translation only: the library reads."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from hedwig.api.auth import require_token
from hedwig.api.deps import ContainerDep
from hedwig.core.errors import DegradedError

router = APIRouter(prefix="/documents", tags=["knowledge"], dependencies=[Depends(require_token)])


class AddDocuments(BaseModel):
    path: Annotated[str, Field(min_length=1, max_length=4096)]


def _enabled(container: ContainerDep) -> None:
    if not container.config.knowledge.documents_enabled:
        raise DegradedError("Local documents are switched off (knowledge.documents_enabled).")


@router.get("", summary="The folders HEDWIG reads, and every file in them")
async def listing(container: ContainerDep) -> dict[str, Any]:
    return container.library.listing()


@router.post("", summary="Read a folder (or one file) of notes")
async def add(body: AddDocuments, container: ContainerDep) -> dict[str, Any]:
    """Only text formats, only under this path; secrets are skipped and listed."""
    _enabled(container)
    report = await container.library.add(body.path)
    await container.embeddings.backfill()
    return report


@router.post("/reindex", summary="Re-read every folder now")
async def reindex(container: ContainerDep) -> dict[str, Any]:
    _enabled(container)
    report = await container.library.reindex()
    await container.embeddings.backfill()
    return report


@router.delete("/{document_id}", summary="Set one file aside")
async def remove(document_id: str, container: ContainerDep) -> dict[str, bool]:
    await container.library.remove(document_id)
    return {"removed": True}


@router.delete("", summary="Stop reading a folder")
async def forget_root(
    container: ContainerDep, path: Annotated[str, Query(min_length=1, max_length=4096)]
) -> dict[str, int]:
    return {"removed": await container.library.forget_root(path)}
