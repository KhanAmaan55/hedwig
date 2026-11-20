"""A Python client for HEDWIG's public API.

The CLI's only way to reach a running HEDWIG. It speaks HTTP and imports nothing else from
this package — an import-linter contract holds it to that — which is what makes the CLI a
proof that the API is complete rather than a second door into the internals (docs/16 §10,
docs/17 §9). The TypeScript twin is `frontend/src/transport/client.ts`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

DEFAULT_READ_TIMEOUT = 300.0
"""A reply from a CPU-bound local model can take minutes. The server enforces its own
per-request timeout (docs/14 §7); this one only stops a hung connection waiting forever."""


@dataclass(frozen=True, slots=True)
class Reply:
    turn_id: str
    session_id: str
    message_id: str
    text: str
    status: str
    intent: str | None = None
    cited_memory_ids: tuple[str, ...] = ()
    mood: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Message:
    id: str
    role: str
    text: str
    created_at: str


class ClientError(Exception):
    """An API error envelope (docs/16 §4.6), or a failure to reach the API at all."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.detail = detail or {}


class HedwigClient:
    """Synchronous on purpose: its first user is a terminal REPL, which is synchronous."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        http: httpx.Client | None = None,
        read_timeout: float = DEFAULT_READ_TIMEOUT,
    ) -> None:
        self._token = token
        self._owns_http = http is None
        self._http = http or httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(10.0, read=read_timeout),
        )

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> HedwigClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # -- conversation ------------------------------------------------------

    def open_session(self, *, channel: str = "cli") -> str:
        return str(self._request("POST", "/v1/sessions", json={"channel": channel})["id"])

    def session_exists(self, session_id: str) -> bool:
        try:
            self._request("GET", f"/v1/sessions/{session_id}")
        except ClientError as error:
            if error.code == "not_found":
                return False
            raise
        return True

    def send(self, session_id: str, text: str) -> Reply:
        body = self._request("POST", f"/v1/sessions/{session_id}/messages", json={"text": text})
        return Reply(
            turn_id=body["turn_id"],
            session_id=body["session_id"],
            message_id=body["message_id"],
            text=body["reply"],
            status=body["status"],
            intent=body.get("intent"),
            cited_memory_ids=tuple(body.get("cited_memory_ids") or ()),
            mood=dict(body.get("mood") or {}),
        )

    def history(self, session_id: str, *, limit: int = 50) -> list[Message]:
        body = self._request("GET", f"/v1/sessions/{session_id}/messages", params={"limit": limit})
        return [
            Message(id=m["id"], role=m["role"], text=m["text"], created_at=m["created_at"])
            for m in body["messages"]
        ]

    # -- transport ---------------------------------------------------------

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self._http.request(
                method,
                path,
                headers={"Authorization": f"Bearer {self._token}", "Accept": "application/json"},
                **kwargs,
            )
        except httpx.TransportError as error:
            raise ClientError("unreachable", f"Could not reach HEDWIG: {error}") from error

        if response.is_success:
            return response.json()  # type: ignore[no-any-return]

        try:
            envelope = dict(response.json()["error"])
        except (ValueError, KeyError, TypeError):
            # Not our envelope (a proxy, a crash page). Still an error; keep the status.
            raise ClientError(
                "internal", response.reason_phrase or "Request failed", status=response.status_code
            ) from None
        raise ClientError(
            str(envelope.get("code", "internal")),
            str(envelope.get("message", response.reason_phrase)),
            status=response.status_code,
            detail=dict(envelope.get("detail") or {}),
        )
