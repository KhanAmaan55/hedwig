"""Request middleware: correlation and access logging (docs/19 §3).

Written as a pure ASGI middleware rather than Starlette's `BaseHTTPMiddleware` because the
latter wraps every request in an extra task, which breaks `contextvars` propagation into
background work — the exact property correlation depends on.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from hedwig.core.context import correlation_scope
from hedwig.core.logging import get_logger

logger = get_logger("hedwig.api.access")

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]

CORRELATION_HEADER = b"x-correlation-id"

_QUIET_PATHS = frozenset({"/v1/health"})
"""Polled every few seconds by the desktop shell; logged at debug so the log stays
readable. The request is still traced — only its log level changes."""


class CorrelationMiddleware:
    """Bind a correlation id per request, echo it back, and log the outcome."""

    def __init__(self, app: Callable[[Scope, Receive, Send], Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = _header(scope, CORRELATION_HEADER)
        started = time.monotonic()

        with correlation_scope(incoming) as correlation_id:
            status_holder: dict[str, int] = {}

            async def send_wrapper(message: MutableMapping[str, Any]) -> None:
                if message["type"] == "http.response.start":
                    status_holder["status"] = message["status"]
                    headers = list(message.get("headers", []))
                    headers.append((CORRELATION_HEADER, correlation_id.encode()))
                    message = {**message, "headers": headers}
                await send(message)

            try:
                await self.app(scope, receive, send_wrapper)
            finally:
                path = scope.get("path", "")
                duration_ms = round((time.monotonic() - started) * 1000, 1)
                level = "debug" if path in _QUIET_PATHS else "info"
                getattr(logger, level)(
                    "%s %s",
                    scope.get("method", "?"),
                    path,
                    extra={
                        "duration_ms": duration_ms,
                        "fields": {"status": status_holder.get("status", 0)},
                    },
                )


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key == name:
            decoded = value.decode("latin-1").strip()
            # Never trust an unbounded client-supplied value into our logs.
            return decoded[:64] or None
    return None
