"""A Python client for HEDWIG's public API.

The CLI's only way to reach a running HEDWIG. It speaks HTTP and imports nothing else from
this package — an import-linter contract holds it to that — which is what makes the CLI a
proof that the API is complete rather than a second door into the internals (docs/16 §10,
docs/17 §9). The TypeScript twin is `frontend/src/transport/client.ts`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import quote

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

    def end_session(self, session_id: str) -> None:
        """End the conversation now, so its relationship update is not left to the idle
        timeout. Idempotent on the server."""
        self._request("POST", f"/v1/sessions/{session_id}/end")

    def send(self, session_id: str, text: str, *, client_message_id: str | None = None) -> Reply:
        """Say something. With `client_message_id`, sending again returns the same turn."""
        payload: dict[str, Any] = {"text": text}
        if client_message_id:
            payload["client_message_id"] = client_message_id
        body = self._request("POST", f"/v1/sessions/{session_id}/messages", json=payload)
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

    # -- memory (docs/06 §16) ------------------------------------------------

    def memories(self, *, limit: int = 50, include_forgotten: bool = False) -> list[dict[str, Any]]:
        body = self._request(
            "GET",
            "/v1/memory",
            params={"limit": limit, "include_forgotten": str(include_forgotten).lower()},
        )
        return list(body["memories"])

    def search_memory(self, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
        body = self._request("GET", "/v1/memory/search", params={"q": query, "limit": limit})
        return list(body["hits"])

    def memory(self, memory_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/memory/{memory_id}")

    def memory_action(self, memory_id: str, action: str) -> dict[str, Any]:
        """`pin`, `unpin`, `forget` or `restore`."""
        if action == "unpin":
            return self._request("DELETE", f"/v1/memory/{memory_id}/pin")
        if action not in {"pin", "forget", "restore"}:
            raise ValueError(f"unknown memory action {action!r}")
        return self._request("POST", f"/v1/memory/{memory_id}/{action}")

    def memory_correct(
        self, memory_id: str, *, text: str | None = None, confidence: float | None = None
    ) -> dict[str, Any]:
        """Replace a memory with the user's correction; returns the replacement."""
        body: dict[str, Any] = {}
        if text is not None:
            body["text"] = text
        if confidence is not None:
            body["confidence"] = confidence
        return self._request("PATCH", f"/v1/memory/{memory_id}", json=body)

    # -- goals (docs/08 §6.1) --------------------------------------------------

    def goal_notes(self, goal_id: str) -> list[dict[str, Any]]:
        body: Any = self._request("GET", f"/v1/goals/{goal_id}/notes")
        return list(body)

    def add_goal_note(self, goal_id: str, text: str, *, kind: str = "progress") -> dict[str, Any]:
        return self._request(
            "POST", f"/v1/goals/{goal_id}/notes", json={"kind": kind, "text": text}
        )

    def goals(self, *, statuses: tuple[str, ...] = ()) -> list[dict[str, Any]]:
        params = [("status", status) for status in statuses]
        return list(self._request("GET", "/v1/goals", params=params)["goals"])

    def create_goal(
        self, description: str, *, completion: str = "", priority: float = 0.5
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/goals",
            json={"description": description, "completion": completion, "priority": priority},
        )

    def update_goal(self, goal_id: str, **changes: Any) -> dict[str, Any]:
        return self._request("PATCH", f"/v1/goals/{goal_id}", json=changes)

    # -- curiosity (docs/11 §13) -----------------------------------------------

    def gaps(self) -> list[dict[str, Any]]:
        return self._list("/v1/curiosity/gaps")

    def ask_curiosity(self, question: str) -> dict[str, Any]:
        return self._request("POST", "/v1/curiosity/gaps", json={"question": question})

    def findings(self) -> list[dict[str, Any]]:
        return self._list("/v1/curiosity/findings")

    def finding_action(self, finding_id: str, action: str) -> dict[str, Any]:
        if action not in {"confirm", "dismiss"}:
            raise ValueError(f"unknown finding action {action!r}")
        return self._request("POST", f"/v1/curiosity/findings/{finding_id}/{action}")

    def explore(self) -> dict[str, Any]:
        return self._request("POST", "/v1/curiosity/explore")

    def reflect(self) -> dict[str, Any]:
        return self._request("POST", "/v1/reflection/run")

    def reports(self) -> list[dict[str, Any]]:
        return list(self._request("GET", "/v1/reflection/reports")["reports"])

    def report(self, month: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/reflection/reports/{month}")

    def traces(self, limit: int = 20) -> list[dict[str, Any]]:
        return list(self._request("GET", "/v1/traces", params={"limit": limit})["traces"])

    def trace(self, correlation_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/traces/{correlation_id}")

    def metrics(self) -> str:
        try:
            response = self._http.get(
                "/v1/metrics", headers={"Authorization": f"Bearer {self._token}"}
            )
        except httpx.TransportError as error:
            raise ClientError("unreachable", f"Could not reach HEDWIG: {error}") from error
        if response.is_success:
            return response.text
        self._fail(response)

    def corrections(self, status: str = "proposed") -> list[dict[str, Any]]:
        return list(self._request("GET", "/v1/corrections", params={"status": status})["proposals"])

    def settle_correction(self, proposal_id: str, accept: bool) -> dict[str, Any]:
        verb = "accept" if accept else "decline"
        return self._request("POST", f"/v1/corrections/{proposal_id}/{verb}")

    def initiative(self) -> dict[str, Any]:
        return self._request("GET", "/v1/initiative")

    def check_initiative(self) -> dict[str, Any]:
        return self._request("POST", "/v1/initiative/check")

    def run_periodic(self, which: str) -> dict[str, Any]:
        return self._request("POST", f"/v1/reflection/{which}/run")

    def disputes(self) -> list[dict[str, Any]]:
        return list(self._request("GET", "/v1/reflection/disputes")["disputes"])

    def resolve_dispute(self, pair: str, choice: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/v1/reflection/disputes/{quote(pair, safe='')}/resolve",
            json={"choice": choice},
        )

    def export(self, destination: Path) -> Path:
        """Download everything HEDWIG holds as a zip: into `destination`, or into it as a folder
        under the dated name the server gives."""
        try:
            with self._http.stream(
                "POST", "/v1/export", headers={"Authorization": f"Bearer {self._token}"}
            ) as response:
                if not response.is_success:
                    response.read()
                    self._fail(response)
                if destination.is_dir():
                    _, _, name = response.headers.get("content-disposition", "").partition(
                        "filename="
                    )
                    destination = destination / (name.strip('"') or "hedwig-export.zip")
                with destination.open("wb") as out:
                    for chunk in response.iter_bytes():
                        out.write(chunk)
        except httpx.TransportError as error:
            raise ClientError("unreachable", f"Could not reach HEDWIG: {error}") from error
        return destination

    def documents(self) -> dict[str, Any]:
        return self._request("GET", "/v1/documents")

    def add_documents(self, path: str) -> dict[str, Any]:
        return self._request("POST", "/v1/documents", json={"path": path})

    def reindex_documents(self) -> dict[str, Any]:
        return self._request("POST", "/v1/documents/reindex")

    def remove_document(self, document_id: str) -> dict[str, Any]:
        return self._request("DELETE", f"/v1/documents/{document_id}")

    def forget_folder(self, path: str) -> dict[str, Any]:
        return self._request("DELETE", "/v1/documents", params={"path": path})

    def purge_preview(self, scope: str, value: str) -> dict[str, Any]:
        return self._request("POST", "/v1/purge/preview", json={"scope": scope, "value": value})

    def purge(self, scope: str, value: str, confirm: str) -> dict[str, Any]:
        return self._request(
            "POST", "/v1/purge", json={"scope": scope, "value": value, "confirm": confirm}
        )

    def purge_receipts(self) -> list[dict[str, Any]]:
        return list(self._request("GET", "/v1/purge/receipts")["receipts"])

    def egress(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return list(self._request("GET", "/v1/egress", params={"limit": limit})["requests"])

    # -- transport ---------------------------------------------------------

    def _list(self, path: str) -> list[dict[str, Any]]:
        """For the endpoints whose body is a JSON array rather than an object."""
        body: Any = self._request("GET", path)
        return list(body)

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
        self._fail(response)

    @staticmethod
    def _fail(response: httpx.Response) -> NoReturn:
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
