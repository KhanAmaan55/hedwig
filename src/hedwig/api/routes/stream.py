"""The live channel (docs/16 §5, §13; ADR-0023).

One WebSocket per client. A `message.send` runs a turn with a live sink bound, so the reply
arrives token by token and the turn's stages as they begin; `message.complete` then carries the
authoritative reply, identical to what `POST …/messages` returns.

Translation only, like every route: the brain runs turns, the sink is ADR-0023's, the reply is
built by `conversation.turn_reply`.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from hedwig.api.errors import error_body
from hedwig.api.routes.conversation import MAX_MESSAGE_CHARS, turn_reply
from hedwig.api.routes.mind import current_expression
from hedwig.core.context import correlation_scope
from hedwig.core.errors import HedwigError
from hedwig.core.ids import new_id
from hedwig.core.live import LiveHandle, live_scope
from hedwig.core.logging import fields, get_logger
from hedwig.core.token import TokenFile

logger = get_logger(__name__)

router = APIRouter(tags=["stream"])

PROTOCOL = "hedwig.v1"
TOKEN_PREFIX = "hedwig.token."
UNAUTHORISED = 4401
"""Application close code: the 4xxx range is ours (RFC 6455 §7.4.2); 4401 mirrors HTTP 401."""
MIND_CHECK_SECONDS = 0.4
"""How often a subscribed socket's expression is recomputed. Frames go out only on change, so
this is the latency of "thinking…" appearing, not a message rate (EMO-02)."""


def _presented_token(websocket: WebSocket) -> str | None:
    """`Authorization: Bearer …` (the Vite proxy), else the `hedwig.token.…` subprotocol."""
    scheme, _, value = websocket.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()
    for protocol in websocket.scope.get("subprotocols") or []:
        if protocol.startswith(TOKEN_PREFIX):
            return str(protocol)[len(TOKEN_PREFIX) :]
    return None


class Connection:
    """One client's socket: envelopes out, one at a time, never interleaved."""

    def __init__(self, websocket: WebSocket, clock: Any) -> None:
        self._ws = websocket
        self._clock = clock
        self._lock = asyncio.Lock()
        self.open = True

    async def send(self, kind: str, payload: Mapping[str, Any], *, corr: str | None = None) -> None:
        if not self.open:
            raise ConnectionError("closed")
        envelope = {
            "v": 1,
            "id": new_id("ev"),
            "type": kind,
            "ts": self._clock.now().timestamp(),
            "corr": corr,
            "payload": dict(payload),
        }
        async with self._lock:
            try:
                await self._ws.send_json(envelope)
            except Exception as error:
                self.open = False
                raise ConnectionError("closed") from error


@router.websocket("/stream")
async def stream(websocket: WebSocket) -> None:
    container = websocket.app.state.container
    token_file: TokenFile = websocket.app.state.token_file

    presented = _presented_token(websocket)
    if presented is None or not token_file.matches(presented):
        await websocket.close(code=UNAUTHORISED, reason="the API token was not accepted")
        return
    offered = websocket.scope.get("subprotocols") or []
    await websocket.accept(subprotocol=PROTOCOL if PROTOCOL in offered else None)

    connection = Connection(websocket, container.clock)
    presence = container.presence
    present: str | None = None  # this socket's place in presence, once it subscribes
    turns: set[asyncio.Task[None]] = set()
    watcher: asyncio.Task[None] | None = None
    running: dict[str, LiveHandle] = {}
    try:
        while True:
            message = await websocket.receive_json()
            kind = str(message.get("type", "")) if isinstance(message, dict) else ""
            payload = message.get("payload") or {} if isinstance(message, dict) else {}
            if kind == "ping":
                await connection.send("pong", {})
            elif kind == "hello":
                await connection.send("ready", {"server_version": container.version})
            elif kind == "turn.interrupt":
                # Stop the reply where it is (VOI-03); the turn still completes and is saved.
                handle = running.get(str(payload.get("turn_id", "")))
                if handle is not None:
                    handle.stop()
            elif kind == "mind.subscribe":
                # Push the avatar's expression as it changes, instead of the client polling.
                if watcher is None or watcher.done():
                    watcher = asyncio.create_task(_watch_mind(container, connection))
            elif kind == "initiative.subscribe":
                # The app is open and showing this conversation: HEDWIG may speak first into
                # it (ADR-0026). Sent again whenever the conversation changes.
                session = payload.get("session_id")
                session = str(session) if session else None
                if present is None:
                    present = presence.join(connection.send, session_id=session)
                else:
                    presence.showing(present, session)
            elif kind == "message.send":
                # A task, so the socket keeps answering pings while HEDWIG thinks. The turn is
                # not cancelled if the client leaves: it completes and persists (docs/16 §9).
                task = asyncio.create_task(_turn(container, connection, payload, running, present))
                turns.add(task)
                task.add_done_callback(turns.discard)
            else:
                await connection.send(
                    "error",
                    error_body("invalid_request", f"Unknown message type {kind!r}.")["error"],
                )
    except WebSocketDisconnect:
        pass
    except Exception:  # a malformed frame, a dropped connection: end this socket only
        logger.exception("stream closed unexpectedly")
    finally:
        connection.open = False
        if present is not None:
            presence.leave(present)
        if watcher is not None:
            watcher.cancel()


async def _watch_mind(container: Any, connection: Connection) -> None:
    """Send `mind.update` whenever the expression changes, starting with the current one."""
    last = None
    while connection.open:
        current = (await current_expression(container)).model_dump()
        if current != last:
            last = current
            try:
                await connection.send("mind.update", current)
            except ConnectionError:
                return
        # Wakes the moment a turn starts or ends; otherwise rechecks for mood changes.
        await container.brain.wait_for_activity(MIND_CHECK_SECONDS)


async def _turn(
    container: Any,
    connection: Connection,
    payload: Mapping[str, Any],
    running: dict[str, LiveHandle],
    present: str | None = None,
) -> None:
    client_id = payload.get("client_message_id")
    text = str(payload.get("text", ""))
    if not text.strip() or len(text) > MAX_MESSAGE_CHARS:
        await _quietly(
            connection.send(
                "error",
                {
                    **error_body("invalid_request", "A message needs 1 to 32768 characters.")[
                        "error"
                    ],
                    "client_message_id": client_id,
                },
            )
        )
        return

    session_id = payload.get("session_id")
    if session_id:
        if await container.history.get_session(str(session_id)) is None:
            await _quietly(
                connection.send(
                    "error",
                    {
                        **error_body("not_found", "No such session.")["error"],
                        "client_message_id": client_id,
                    },
                )
            )
            return
    else:
        session_id = await container.brain.open_session(channel="web")
    if present is not None:
        container.presence.showing(present, str(session_id))

    turn_id = new_id("turn")
    with correlation_scope(turn_id):
        await _quietly(
            connection.send(
                "turn.started",
                {"turn_id": turn_id, "session_id": session_id, "client_message_id": client_id},
                corr=turn_id,
            )
        )

        async def sink(kind: str, data: Mapping[str, Any]) -> None:
            await connection.send("turn.stage" if kind == "stage" else kind, data, corr=turn_id)

        with live_scope(sink) as handle:
            running[turn_id] = handle
            try:
                final = await container.brain.run_turn(
                    text,
                    session_id=str(session_id),
                    turn_id=turn_id,
                    correlation_id=turn_id,
                    client_message_id=str(client_id) if client_id else None,
                )
            finally:
                running.pop(turn_id, None)

        try:
            reply = turn_reply(final, session_id=str(session_id))
            await _quietly(
                connection.send(
                    "message.complete",
                    {**reply.model_dump(), "client_message_id": client_id},
                    corr=turn_id,
                )
            )
        except HedwigError as error:
            await _quietly(
                connection.send(
                    "error",
                    {
                        **error_body(
                            error.code,
                            error.message,
                            detail=error.detail,
                            retryable=error.retryable,
                        )["error"],
                        "client_message_id": client_id,
                    },
                    corr=turn_id,
                )
            )
        await _quietly(
            connection.send(
                "turn.completed",
                {"turn_id": turn_id, "status": final.get("status")},
                corr=turn_id,
            )
        )
        logger.info("streamed turn", extra=fields(turn=turn_id, status=final.get("status")))


async def _quietly(sending: Any) -> None:
    """The client may have gone; the turn has already been persisted either way."""
    with contextlib.suppress(ConnectionError):
        await sending
