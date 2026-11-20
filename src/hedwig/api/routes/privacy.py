"""Export everything; erase for real (docs/16 §4.5, docs/21 §7.4, ADR-0024).

Translation only: `privacy.Exporter` builds the archive, `privacy.Purger` erases. Erasing is two
calls — preview, then purge with the preview's code — so nothing goes that the user has not seen.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from hedwig.api.auth import require_token
from hedwig.api.deps import ContainerDep

router = APIRouter(tags=["privacy"], dependencies=[Depends(require_token)])


class Scope(BaseModel):
    scope: Literal["memory", "session", "phrase"]
    value: Annotated[str, Field(min_length=1, max_length=500)]


class Erase(Scope):
    confirm: Annotated[str, Field(min_length=1, max_length=64)]


@router.post("/export", summary="Everything HEDWIG holds, as one zip", response_class=FileResponse)
async def export(container: ContainerDep) -> FileResponse:
    """Built in a temporary directory and deleted once sent: no copy is left behind."""
    workdir = Path(tempfile.mkdtemp(prefix="hedwig-export-"))
    try:
        archive = container.exporter.build(workdir)
    except Exception:
        shutil.rmtree(workdir, ignore_errors=True)
        raise
    return FileResponse(
        archive,
        media_type="application/zip",
        filename=archive.name,
        background=BackgroundTask(shutil.rmtree, workdir, ignore_errors=True),
    )


@router.post("/purge/preview", summary="What erasing this would remove; changes nothing")
async def preview(body: Scope, container: ContainerDep) -> dict[str, Any]:
    return await container.purger.preview(body.scope, body.value)


@router.post("/purge", summary="Erase permanently. Needs the preview's confirm code")
async def purge(body: Erase, container: ContainerDep) -> dict[str, Any]:
    return await container.purger.purge(body.scope, body.value, body.confirm)


@router.get("/purge/receipts", summary="Past erasures: what kind, how much, whether verified")
async def receipts(
    container: ContainerDep, limit: Annotated[int, Query(ge=1, le=200)] = 20
) -> dict[str, Any]:
    return {"receipts": container.purger.receipts(limit=limit)}
