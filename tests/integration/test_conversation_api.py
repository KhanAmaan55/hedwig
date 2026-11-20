"""The conversation surface, end to end (docs/16 §12, ADR-0022).

A real container — graph, bus, database, memory, emotion, personality — behind the real
FastAPI app, with a `FakeClock` and the scripted model provider. Nothing is patched.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from hedwig.api.app import create_app
from hedwig.core.token import TokenFile
from hedwig.llm.recorded import RecordedProvider
from hedwig.wiring import Container


@pytest.fixture
async def anonymous(running_container: Container) -> AsyncIterator[AsyncClient]:
    """A client that does not present the token."""
    app = create_app(running_container)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest.fixture
def token(running_container: Container, anonymous: AsyncClient) -> str:
    # `create_app` (inside `anonymous`) has created it by now.
    value = TokenFile(running_container.config.runtime.token_path).read()
    assert value
    return value


@pytest.fixture
async def api(anonymous: AsyncClient, token: str) -> AsyncClient:
    anonymous.headers["Authorization"] = f"Bearer {token}"
    return anonymous


async def _session(api: AsyncClient, channel: str = "api") -> str:
    response = await api.post("/v1/sessions", json={"channel": channel})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


# =========================================================================
# The token (FND-01)
# =========================================================================


@pytest.mark.parametrize(
    "header",
    [None, "", "Bearer", "Bearer ", "Basic abc", "Bearer not-the-token", "bearer"],
)
async def test_user_content_requires_the_token(anonymous: AsyncClient, header: str | None) -> None:
    if header is not None:
        anonymous.headers["Authorization"] = header
    for method, path in [
        ("POST", "/v1/sessions"),
        ("GET", "/v1/sessions"),
        ("GET", "/v1/sessions/sess_x"),
        ("GET", "/v1/sessions/sess_x/messages"),
        ("POST", "/v1/sessions/sess_x/messages"),
    ]:
        response = await anonymous.request(method, path, json={"text": "hello there"})
        assert response.status_code == 401, (method, path)
        assert response.json()["error"]["code"] == "unauthorized"


async def test_the_token_is_created_private_at_startup(
    running_container: Container, token: str
) -> None:
    assert TokenFile(running_container.config.runtime.token_path).is_private()


async def test_health_and_hedwigs_own_state_stay_open(anonymous: AsyncClient) -> None:
    """ADR-0022 §2: no user content, and needed before a token can be delivered."""
    for path in ("/v1/health", "/v1/mind/emotion", "/v1/mind/expression"):
        response = await anonymous.get(path)
        assert response.status_code != 401, path


async def test_a_rotated_token_is_refused_on_the_next_request(
    api: AsyncClient, running_container: Container
) -> None:
    assert (await api.get("/v1/sessions")).status_code == 200

    new = TokenFile(running_container.config.runtime.token_path).rotate()

    assert (await api.get("/v1/sessions")).status_code == 401
    api.headers["Authorization"] = f"Bearer {new}"
    assert (await api.get("/v1/sessions")).status_code == 200


# =========================================================================
# Sessions (FND-02)
# =========================================================================


async def test_opening_a_session_announces_it_once(
    api: AsyncClient, running_container: Container
) -> None:
    """The brain opens it, so the turn's `ingest` does not announce it a second time —
    and emotion, which appraises the event, hears about it exactly once (docs/16 §12.3)."""
    response = await api.post("/v1/sessions", json={"channel": "cli"})
    body = response.json()
    assert body["channel"] == "cli"
    assert body["is_open"] is True
    assert body["id"].startswith("sess_")

    await api.post(f"/v1/sessions/{body['id']}/messages", json={"text": "Hello HEDWIG!"})
    await running_container.bus.drain()

    started = running_container.database.query(
        "SELECT payload FROM event WHERE type = 'conversation.session.started'"
    )
    assert len(started) == 1


async def test_the_default_channel_is_api(api: AsyncClient) -> None:
    response = await api.post("/v1/sessions")
    assert response.status_code == 201
    assert response.json()["channel"] == "api"


async def test_internal_is_not_a_client_channel(api: AsyncClient) -> None:
    response = await api.post("/v1/sessions", json={"channel": "internal"})
    assert response.status_code == 422


async def test_sessions_are_listed_and_fetched(api: AsyncClient) -> None:
    first = await _session(api)
    second = await _session(api)

    listed = (await api.get("/v1/sessions")).json()["sessions"]
    assert {first, second} <= {session["id"] for session in listed}

    one = await api.get(f"/v1/sessions/{first}")
    assert one.status_code == 200
    assert one.json()["id"] == first


async def test_an_unknown_session_is_404(api: AsyncClient) -> None:
    for method, path in [
        ("GET", "/v1/sessions/sess_nope"),
        ("GET", "/v1/sessions/sess_nope/messages"),
        ("POST", "/v1/sessions/sess_nope/messages"),
    ]:
        response = await api.request(method, path, json={"text": "hello there"})
        assert response.status_code == 404, (method, path)
        assert response.json()["error"]["code"] == "not_found"


# =========================================================================
# Messages (FND-02)
# =========================================================================


async def test_a_message_gets_the_complete_reply(
    api: AsyncClient, llm_provider: RecordedProvider
) -> None:
    llm_provider.script("compose.reply", "Fine, thank you. Curious as ever.")
    session_id = await _session(api)

    response = await api.post(
        f"/v1/sessions/{session_id}/messages", json={"text": "How are you today, HEDWIG?"}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reply"] == "Fine, thank you. Curious as ever."
    assert body["status"] == "completed"
    assert body["session_id"] == session_id
    assert body["turn_id"].startswith("turn_")
    assert body["message_id"].startswith("msg_")
    assert body["intent"] == "answer"
    assert set(body["mood"]) >= {"happiness", "curiosity", "stress"}


async def test_the_second_message_recalls_the_first(
    api: AsyncClient, running_container: Container
) -> None:
    """The loop the whole system exists for, now reachable by a person (docs/26)."""
    session_id = await _session(api)
    await api.post(
        f"/v1/sessions/{session_id}/messages",
        json={"text": "Please remember that my compiler written in Rust is called Fernweh."},
    )
    await running_container.bus.drain()

    other = await _session(api)
    body = (
        await api.post(
            f"/v1/sessions/{other}/messages", json={"text": "What is my Rust compiler called?"}
        )
    ).json()

    assert body["cited_memory_ids"], "the new session recalled nothing from the old one"


async def test_history_is_oldest_first_and_paginates(api: AsyncClient) -> None:
    session_id = await _session(api)
    for text in ("first message here", "second message here", "third message here"):
        await api.post(f"/v1/sessions/{session_id}/messages", json={"text": text})

    page = (await api.get(f"/v1/sessions/{session_id}/messages", params={"limit": 4})).json()
    assert [m["seq"] for m in page["messages"]] == [3, 4, 5, 6]
    assert [m["role"] for m in page["messages"]] == ["user", "hedwig", "user", "hedwig"]
    assert page["messages"][0]["text"] == "second message here"
    assert page["next_before"] == page["messages"][0]["id"]

    older = (
        await api.get(
            f"/v1/sessions/{session_id}/messages",
            params={"limit": 4, "before": page["next_before"]},
        )
    ).json()
    assert [m["seq"] for m in older["messages"]] == [1, 2]
    assert older["next_before"] is None


async def test_a_cursor_from_another_session_reads_nothing(api: AsyncClient) -> None:
    mine = await _session(api)
    theirs = await _session(api)
    await api.post(f"/v1/sessions/{theirs}/messages", json={"text": "a message elsewhere"})
    cursor = (await api.get(f"/v1/sessions/{theirs}/messages")).json()["messages"][-1]["id"]

    page = (await api.get(f"/v1/sessions/{mine}/messages", params={"before": cursor})).json()
    assert page["messages"] == []


@pytest.mark.parametrize("text", ["", "   \n\t", "x" * 32_769])
async def test_invalid_text_is_rejected(api: AsyncClient, text: str) -> None:
    session_id = await _session(api)
    response = await api.post(f"/v1/sessions/{session_id}/messages", json={"text": text})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


async def test_a_model_outage_is_503_and_still_carries_the_reply(
    api: AsyncClient, llm_provider: RecordedProvider
) -> None:
    """docs/07 §8: the turn fails honestly. The client gets a code it can branch on and the
    sentence HEDWIG would have said (docs/16 §12.3)."""
    session_id = await _session(api)
    llm_provider.available = False

    response = await api.post(f"/v1/sessions/{session_id}/messages", json={"text": "Hello?"})

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "capability_unavailable"
    assert error["retryable"] is True
    assert error["detail"]["status"] == "failed"
    assert error["detail"]["reply"]
    assert error["detail"]["turn_id"].startswith("turn_")


async def test_the_session_counts_its_turns(api: AsyncClient) -> None:
    session_id = await _session(api)
    await api.post(f"/v1/sessions/{session_id}/messages", json={"text": "one turn here"})
    await api.post(f"/v1/sessions/{session_id}/messages", json={"text": "and another one"})

    assert (await api.get(f"/v1/sessions/{session_id}")).json()["turn_count"] == 2


# =========================================================================
# Ending a session (FND-03) and one turn at a time (FND-04)
# =========================================================================


def _events(container: Container, event_type: str) -> int:
    return len(container.database.query("SELECT id FROM event WHERE type = ?", (event_type,)))


async def test_ending_a_session_applies_the_relationship_now(
    api: AsyncClient, running_container: Container
) -> None:
    session_id = await _session(api)
    await api.post(f"/v1/sessions/{session_id}/messages", json={"text": "Hello there, HEDWIG."})
    await running_container.bus.drain()
    before = (await running_container.relationship.with_user()).interaction_count

    response = await api.post(f"/v1/sessions/{session_id}/end")
    await running_container.bus.drain()

    assert response.status_code == 200
    body = response.json()
    assert body["is_open"] is False
    assert body["end_reason"] == "explicit"
    after = (await running_container.relationship.with_user()).interaction_count
    assert after == before + 1, "the relationship waited for the idle timeout"


async def test_ending_is_idempotent(api: AsyncClient, running_container: Container) -> None:
    session_id = await _session(api)
    await api.post(f"/v1/sessions/{session_id}/messages", json={"text": "One turn, then done."})

    first = await api.post(f"/v1/sessions/{session_id}/end")
    second = await api.post(f"/v1/sessions/{session_id}/end")
    await running_container.bus.drain()

    assert first.status_code == second.status_code == 200
    assert second.json()["ended_at"] == first.json()["ended_at"]
    assert _events(running_container, "conversation.session.ended") == 1


async def test_ending_an_unknown_session_is_404(api: AsyncClient) -> None:
    response = await api.post("/v1/sessions/sess_nope/end")
    assert response.status_code == 404


async def test_an_end_right_after_a_reply_still_counts_that_turn(
    api: AsyncClient, running_container: Container
) -> None:
    """The race FND-03 closed: `turn.completed` and `session.ended` used to travel through two
    unordered subscriptions, so an immediate end could close the tally before the last turn
    was counted (docs/27 §5.1). No drain between the reply and the end, on purpose."""
    session_id = await _session(api)
    for text in ("first thing to say", "second thing to say", "third thing to say"):
        await api.post(f"/v1/sessions/{session_id}/messages", json={"text": text})
    await api.post(f"/v1/sessions/{session_id}/end")
    await running_container.bus.drain()

    leftover = running_container.database.query(
        "SELECT turns FROM relationship_tally WHERE session_id = ?", (session_id,)
    )
    assert leftover == [], "a turn was tallied after its session had already closed"
    updated = running_container.database.query(
        "SELECT payload FROM event WHERE type = 'memory.relationship.updated'"
    )
    assert len(updated) == 1
    assert json.loads(updated[0]["payload"])["turns"] == 3


async def test_a_message_after_the_end_starts_a_new_conversation(
    api: AsyncClient, running_container: Container
) -> None:
    session_id = await _session(api)
    await api.post(f"/v1/sessions/{session_id}/messages", json={"text": "Before the end."})
    await api.post(f"/v1/sessions/{session_id}/end")

    response = await api.post(f"/v1/sessions/{session_id}/messages", json={"text": "After it."})
    await running_container.bus.drain()

    assert response.status_code == 200
    assert (await api.get(f"/v1/sessions/{session_id}")).json()["is_open"] is True
    assert _events(running_container, "conversation.session.started") == 2


async def test_concurrent_messages_to_one_session_are_answered_in_order(
    api: AsyncClient,
) -> None:
    session_id = await _session(api)
    texts = [f"message number {index} here" for index in range(4)]

    responses = await asyncio.gather(
        *(api.post(f"/v1/sessions/{session_id}/messages", json={"text": t}) for t in texts)
    )

    assert all(r.status_code == 200 for r in responses)
    history = (await api.get(f"/v1/sessions/{session_id}/messages")).json()["messages"]
    roles = [m["role"] for m in history]
    assert roles == ["user", "hedwig"] * 4, "turns interleaved"
