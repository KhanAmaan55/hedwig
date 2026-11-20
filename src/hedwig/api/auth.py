"""The bearer-token check (ADR-0022, docs/16 §3).

A router-level dependency: a router that handles user content declares it once, and every
route added to that router later is guarded without having to remember to be. Health and
HEDWIG's own mind state are deliberately not behind it (ADR-0022 §2).

Only `Authorization: Bearer <token>` is accepted. No cookie — a cookie would be sent by any
page the browser loads — and no query string, which ends up in logs.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from hedwig.core.errors import UnauthorizedError
from hedwig.core.token import TokenFile


def get_token_file(request: Request) -> TokenFile:
    token_file = getattr(request.app.state, "token_file", None)
    if token_file is None:  # pragma: no cover - impossible via create_app
        raise RuntimeError("application was created without a token file")
    return token_file  # type: ignore[no-any-return]


def require_token(
    request: Request, token_file: Annotated[TokenFile, Depends(get_token_file)]
) -> None:
    scheme, _, presented = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not presented.strip():
        raise UnauthorizedError(
            "This endpoint needs the API token: send 'Authorization: Bearer <token>' "
            "(`hedwig token show` prints it)."
        )
    if not token_file.matches(presented.strip()):
        # The same message whether the file is missing or the token is wrong: which one it
        # is, is not the caller's business.
        raise UnauthorizedError("The API token was not accepted.")
