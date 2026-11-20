"""The live channel (FND-05, docs/16 §13, ADR-0023), end to end through a real container."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from hedwig.api.app import create_app
from hedwig.core.live import emit_live, live_scope
from hedwig.core.token import TokenFile
from hedwig.llm.recorded import RecordedProvider
from hedwig.wiring import Container

REPLY = "Hello there! It is lovely to hear from you today."


@pytest.fixture
def http(container: Container) -> Iterator[TestClient]:
    with TestClient(create_app(container)) as client:
        yield client


@pytest.fixture
def token(container: Container, http: TestClient) -> str:
    value = TokenFile(container.config.runtime.token_path).read()
    assert value
    return value


def _until(socket: Any, kind: str) -> list[dict[str, Any]]:
    """Every envelope up to and including the first of `kind`."""
    seen: list[dict[str, Any]] = []
    while True:
        envelope = socket.receive_json()
        seen.append(envelope)
        if envelope["type"] == kind:
            return seen


def _send(socket: Any, text: str, **payload: Any) -> None:
    socket.send_json({"type": "message.send", "payload": {"text": text, **payload}})


# =========================================================================
# Authentication
# =========================================================================


def test_without_the_token_the_socket_is_closed(http: TestClient) -> None:
    with (
        pytest.raises(WebSocketDisconnect) as closed,
        http.websocket_connect("/v1/stream") as socket,
    ):
        socket.receive_json()
    assert closed.value.code == 4401


def test_a_wrong_token_is_closed_too(http: TestClient) -> None:
    with (
        pytest.raises(WebSocketDisconnect),
        http.websocket_connect("/v1/stream", subprotocols=["hedwig.v1", "hedwig.token.nope"]) as s,
    ):
        s.receive_json()


def test_the_token_as_a_subprotocol_is_accepted_and_never_echoed(
    http: TestClient, token: str
) -> None:
    with http.websocket_connect(
        "/v1/stream", subprotocols=["hedwig.v1", f"hedwig.token.{token}"]
    ) as socket:
        accepted: object = socket.accepted_subprotocol  # typed narrowly by the test client
        assert accepted == "hedwig.v1"
        socket.send_json({"type": "ping"})
        assert socket.receive_json()["type"] == "pong"


def test_the_token_as_a_header_is_accepted(http: TestClient, token: str) -> None:
    """How the Vite proxy authenticates in browser development (docs/18 §13.2)."""
    with http.websocket_connect("/v1/stream", headers={"Authorization": f"Bearer {token}"}) as s:
        s.send_json({"type": "hello", "payload": {"client": "test"}})
        assert s.receive_json()["type"] == "ready"


# =========================================================================
# A streamed turn
# =========================================================================


@pytest.fixture
def socket(http: TestClient, token: str) -> Iterator[Any]:
    with http.websocket_connect(
        "/v1/stream", subprotocols=["hedwig.v1", f"hedwig.token.{token}"]
    ) as connection:
        yield connection


def test_a_turn_streams_stages_then_tokens_then_the_authoritative_reply(
    socket: Any, llm_provider: RecordedProvider
) -> None:
    llm_provider.script("compose.reply", REPLY)

    _send(socket, "Hi HEDWIG, how are you today?", client_message_id="c1")
    seen = _until(socket, "turn.completed")
    kinds = [envelope["type"] for envelope in seen]

    started = seen[0]
    assert started["type"] == "turn.started"
    assert started["payload"]["session_id"].startswith("sess_")
    assert started["payload"]["client_message_id"] == "c1"
    stages = [e["payload"]["stage"] for e in seen if e["type"] == "turn.stage"]
    assert stages == ["recalling", "deliberating", "composing"]
    assert kinds.index("token") > kinds.index("turn.stage")

    tokens = "".join(e["payload"]["text"] for e in seen if e["type"] == "token")
    [complete] = [e for e in seen if e["type"] == "message.complete"]
    assert tokens == complete["payload"]["reply"] == REPLY
    assert complete["payload"]["client_message_id"] == "c1"
    assert kinds[-2:] == ["message.complete", "turn.completed"]
    assert all(e["corr"] == started["payload"]["turn_id"] for e in seen)
    assert all(e["v"] == 1 and e["id"] for e in seen)


def test_the_next_message_continues_the_same_conversation(
    socket: Any, http: TestClient, token: str
) -> None:
    _send(socket, "Remember that my sister Ana lives in Porto.")
    session_id = _until(socket, "turn.started")[-1]["payload"]["session_id"]
    _until(socket, "turn.completed")

    _send(socket, "Where does Ana live?", session_id=session_id)
    seen = _until(socket, "turn.completed")

    assert seen[0]["payload"]["session_id"] == session_id
    history = http.get(
        f"/v1/sessions/{session_id}/messages", headers={"Authorization": f"Bearer {token}"}
    ).json()["messages"]
    assert [m["role"] for m in history] == ["user", "hedwig", "user", "hedwig"]


def test_the_stream_and_rest_return_the_same_reply(
    socket: Any, http: TestClient, token: str, llm_provider: RecordedProvider
) -> None:
    llm_provider.script("compose.reply", REPLY)
    _send(socket, "Say hello please.")
    streamed = _until(socket, "message.complete")[-1]["payload"]

    session = http.post("/v1/sessions", headers={"Authorization": f"Bearer {token}"}).json()["id"]
    rest = http.post(
        f"/v1/sessions/{session}/messages",
        json={"text": "Say hello please."},
        headers={"Authorization": f"Bearer {token}"},
    ).json()

    shape = set(rest)
    assert set(streamed) - {"client_message_id"} == shape
    assert streamed["reply"] == rest["reply"] == REPLY


def test_a_model_outage_streams_an_honest_error(
    socket: Any, llm_provider: RecordedProvider
) -> None:
    llm_provider.available = False
    _send(socket, "Are you there?", client_message_id="c9")
    seen = _until(socket, "turn.completed")

    [error] = [e for e in seen if e["type"] == "error"]
    assert error["payload"]["code"] == "capability_unavailable"
    assert error["payload"]["detail"]["reply"]
    assert error["payload"]["client_message_id"] == "c9"
    assert seen[-1]["payload"]["status"] == "failed"


@pytest.mark.parametrize(
    ("payload", "code"),
    [({"text": ""}, "invalid_request"), ({"text": "hi", "session_id": "sess_nope"}, "not_found")],
)
def test_bad_sends_are_answered_not_dropped(
    socket: Any, payload: dict[str, Any], code: str
) -> None:
    socket.send_json({"type": "message.send", "payload": payload})
    assert socket.receive_json()["payload"]["code"] == code


def test_an_unknown_message_type_is_an_error(socket: Any) -> None:
    socket.send_json({"type": "teleport", "payload": {}})
    assert socket.receive_json()["payload"]["code"] == "invalid_request"


# =========================================================================
# The sink (ADR-0023)
# =========================================================================


async def test_a_watcher_that_goes_away_never_fails_the_turn(running_container: Container) -> None:
    calls: list[str] = []

    async def gone(kind: str, payload: Any) -> None:
        calls.append(kind)
        raise ConnectionError("client closed the tab")

    with live_scope(gone):
        final = await running_container.brain.run_turn("Hello, anyone?", session_id="s")

    assert final["status"] == "completed"
    assert calls == ["stage"], "the sink was called again after it failed"


async def test_nothing_is_emitted_without_a_watcher() -> None:
    await emit_live("token", {"text": "into the void"})  # no scope bound: a no-op
