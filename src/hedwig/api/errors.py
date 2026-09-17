"""One error envelope for the whole API (docs/16 §4.6).

    {"error": {"code", "message", "detail", "correlation_id", "retryable"}}

Clients branch on the stable `code`, not on the HTTP status or the message text.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from hedwig.core.context import current_correlation_id
from hedwig.core.errors import HedwigError
from hedwig.core.logging import get_logger

logger = get_logger(__name__)

_STATUS_CODES = {
    400: "invalid_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "invalid_request",
    409: "conflict",
    422: "invalid_request",
    429: "rate_limited",
    503: "capability_unavailable",
}


def error_body(
    code: str,
    message: str,
    *,
    detail: dict[str, Any] | None = None,
    retryable: bool = False,
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "detail": detail or {},
            "correlation_id": current_correlation_id(),
            "retryable": retryable,
        }
    }


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(HedwigError)
    async def _hedwig_error(_: Request, exc: HedwigError) -> JSONResponse:
        logger.warning(
            "request failed",
            extra={"fields": {"code": exc.code, "detail": exc.detail}},
        )
        return JSONResponse(
            status_code=exc.http_status,
            content=error_body(exc.code, exc.message, detail=exc.detail, retryable=exc.retryable),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_CODES.get(exc.status_code, "internal")
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(code, str(exc.detail), retryable=exc.status_code >= 500),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=error_body(
                "invalid_request",
                "Request validation failed.",
                detail={"errors": exc.errors()},
            ),
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        # Log the traceback; return nothing about it. Internal detail is not a client's
        # business, and the correlation id is enough to find this line in the logs.
        logger.exception("unhandled exception", extra={"fields": {"type": type(exc).__name__}})
        return JSONResponse(
            status_code=500,
            content=error_body("internal", "An unexpected error occurred.", retryable=True),
        )
