"""What left the machine (docs/13 §13). Behind the token: the queries came from the user."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from hedwig.api.auth import require_token
from hedwig.api.deps import ContainerDep

router = APIRouter(prefix="/egress", tags=["knowledge"], dependencies=[Depends(require_token)])


class EgressLog(BaseModel):
    requests: list[dict[str, Any]]


@router.get("", response_model=EgressLog, summary="Every network request HEDWIG made, newest first")
async def egress(
    container: ContainerDep, limit: Annotated[int, Query(ge=1, le=1000)] = 100
) -> EgressLog:
    rows = container.database.query(
        "SELECT created_at, purpose, method, host, url, query, status, bytes, outcome, detail "
        "FROM egress_log ORDER BY created_at DESC, rowid DESC LIMIT ?",
        (limit,),
    )
    return EgressLog(requests=[dict(row) for row in rows])
