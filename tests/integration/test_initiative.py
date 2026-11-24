"""Speaking first (INI-01, docs/11 §14, ADR-0026): rare, goal-linked, only with the app open."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hedwig.api.app import create_app
from hedwig.core.clock import FakeClock
from hedwig.core.ports.goals import GoalKind, NoteKind
from hedwig.core.token import TokenFile
from hedwig.curiosity.store import Finding
from hedwig.initiative import Initiative
from hedwig.initiative.engine import yours
from hedwig.llm.recorded import RecordedProvider
from hedwig.wiring import Container

SOURCE = [{"url": "https://example.org/tides", "title": "Tides"}]


class Screen:
    """A stand-in for the app: present, showing a conversation, collecting what is pushed."""

    def __init__(self) -> None:
        self.received: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, kind: str, payload: Mapping[str, Any]) -> None:
        self.received.append((kind, dict(payload)))


@pytest.fixture
def initiative(running_container: Container) -> Initiative:
    running_container.initiative.timezone = "UTC"  # 09:00 on the fake clock: daytime everywhere
    return running_container.initiative


@pytest.fixture
def screen(running_container: Container) -> Screen:
    shown = Screen()
    running_container.presence.join(shown)
    return shown


async def _asked(container: Container, question: str, summary: str) -> Finding:
    gap = await container.curiosity.record_gap(question, origin="user")
    assert gap is not None
    return await container.curiosity.add_finding(
        gap_id=gap.id, summary=summary, sources=SOURCE, status="new", novelty=1.0, relevance=1.0
    )


async def test_a_finding_the_user_asked_for_is_said_into_the_conversation(
    running_container: Container, initiative: Initiative, screen: Screen
) -> None:
    finding = await _asked(
        running_container, "Why are neap tides weaker?", "The sun and moon pull at right angles."
    )

    said = await initiative.check()

    assert said["offered"] and said["kind"] == "finding"
    assert said["text"].startswith("You asked me to find out “Why are neap tides weaker?”")
    assert "right angles" in said["text"] and "example.org/tides" in said["text"]
    assert screen.received == [("initiative.message", {k: said[k] for k in screen.received[0][1]})]
    (message,) = await running_container.history.history(said["session_id"])
    assert (message.role, message.offer_id) == ("hedwig", said["offer_id"])
    assert (await running_container.curiosity.finding(finding.id)).status == "surfaced"  # type: ignore[union-attr]
    trust = running_container.database.query_one(
        "SELECT trust_tier FROM message WHERE id = ?", (said["message_id"],)
    )
    assert trust is not None and trust["trust_tier"] == "untrusted", "web text stays untrusted"


async def test_nothing_is_said_to_nobody(
    running_container: Container, initiative: Initiative
) -> None:
    await _asked(running_container, "Why are neap tides weaker?", "Right angles.")
    assert await initiative.check() == {"offered": False, "reason": "nobody_here"}


@pytest.mark.usefixtures("screen")
async def test_every_gate_holds_or_it_stays_quiet(
    running_container: Container, initiative: Initiative, clock: FakeClock
) -> None:
    await _asked(running_container, "Why are neap tides weaker?", "Right angles.")
    await _asked(running_container, "What makes a spring tide?", "The sun and moon line up.")

    await running_container.brain.run_turn("Hello.", session_id="s")
    assert (await initiative.check())["reason"] == "conversation_in_progress"

    clock.advance(hours=13)  # 22:00 UTC
    assert (await initiative.check())["reason"] == "quiet_hours"

    clock.advance(hours=12)  # 10:00 the next day
    assert (await initiative.check())["offered"]
    clock.advance(hours=2)
    assert (await initiative.check())["reason"] == "already_spoke_today", "one a day, at most"

    initiative.enabled = False
    assert (await initiative.check())["reason"] == "disabled"


@pytest.mark.usefixtures("screen")
async def test_a_finding_speaks_only_for_a_goal_the_user_set(
    running_container: Container, initiative: Initiative
) -> None:
    gap = await running_container.curiosity.record_gap(
        "How do compilers parse generics?", origin="user_interest"
    )
    assert gap is not None
    await running_container.curiosity.add_finding(
        gap_id=gap.id,
        summary="Most compilers parse generics with a lookahead.",
        sources=SOURCE,
        status="new",
        novelty=1.0,
        relevance=1.0,
    )
    await running_container.goals.create(
        "Understand how the user's compiler works", kind=GoalKind.SELF_GENERATED
    )
    assert (await initiative.check())["reason"] == "nothing_to_say", "no interesting facts"

    await running_container.goals.create("Help the user finish their compiler")
    said = await initiative.check()

    assert said["offered"]
    assert "might help with “finish your compiler”" in said["text"]


@pytest.mark.usefixtures("screen")
async def test_an_open_question_gets_one_check_in_until_waved_away(
    running_container: Container, initiative: Initiative, clock: FakeClock
) -> None:
    goal = await running_container.goals.create("Help the user plan the Porto trip")
    await running_container.goals.note(
        goal.id, NoteKind.QUESTION, "Whether to take the train or fly.", source="reflection"
    )
    assert (await initiative.check())["reason"] == "nothing_to_say", "still fresh"

    clock.advance(days=3)
    said = await initiative.check()
    assert said["kind"] == "goal_check_in"
    assert said["text"] == (
        "On “plan the Porto trip”: last time this was still open — whether to take the train "
        "or fly. Did you settle it?"
    )

    clock.advance(days=2)
    assert (await initiative.check())["reason"] == "nothing_to_say", "one a week per goal"
    await initiative.dismiss(said["offer_id"])
    clock.advance(days=8)
    assert (await initiative.check())["reason"] == "nothing_to_say", "never again"


async def test_the_reply_continues_from_what_hedwig_said(
    running_container: Container,
    initiative: Initiative,
    screen: Screen,
    llm_provider: RecordedProvider,
) -> None:
    await _asked(
        running_container, "Why are neap tides weaker?", "The sun and moon pull at right angles."
    )
    said = await initiative.check()

    await running_container.brain.run_turn("Tell me more.", session_id=said["session_id"])

    prompt = " ".join(m.content for m in llm_provider.calls[-1].messages)
    assert "right angles" in prompt
    (offer,) = initiative.offers()
    assert offer["answered"] and offer["text"] == said["text"]


async def test_it_speaks_into_the_conversation_on_screen_resuming_it_if_it_ended(
    running_container: Container, initiative: Initiative, clock: FakeClock
) -> None:
    await running_container.brain.run_turn("Hello.", session_id="sess_shown")
    await running_container.brain.end_session("sess_shown")
    running_container.presence.join(Screen(), session_id="sess_shown")
    clock.advance(minutes=5)
    await _asked(running_container, "Why are neap tides weaker?", "Right angles.")

    said = await initiative.check()

    assert said["session_id"] == "sess_shown"
    session = await running_container.history.get_session("sess_shown")
    assert session is not None and session.ended_at is None


async def test_not_interested_dismisses_the_finding(
    running_container: Container, initiative: Initiative, screen: Screen
) -> None:
    finding = await _asked(running_container, "Why are neap tides weaker?", "Right angles.")
    said = await initiative.check()

    assert await initiative.dismiss(said["offer_id"]) == {"dismissed": True, "kind": "finding"}
    assert (await running_container.curiosity.finding(finding.id)).status == "dismissed"  # type: ignore[union-attr]


def test_yours_says_a_goal_to_the_user() -> None:
    assert yours("Help the user finish their Rust compiler") == "finish your Rust compiler"
    assert yours("Learn Portuguese") == "learn Portuguese"
    assert yours("Plan the user's trip.") == "plan your trip"


# -- over the live channel -------------------------------------------------------------------


@pytest.fixture
def http(container: Container) -> Iterator[TestClient]:
    container.initiative.timezone = "UTC"
    with TestClient(create_app(container)) as client:
        yield client


def test_a_subscribed_app_is_sent_it_live_and_can_wave_it_away(
    http: TestClient, container: Container
) -> None:
    token = TokenFile(container.config.runtime.token_path).read()
    auth = {"Authorization": f"Bearer {token}"}
    gap = http.post(
        "/v1/curiosity/gaps", json={"question": "Why are neap tides weaker?"}, headers=auth
    )
    assert gap.status_code in (200, 201), gap.text

    with http.websocket_connect(
        "/v1/stream", subprotocols=["hedwig.v1", f"hedwig.token.{token}"]
    ) as socket:
        socket.send_json({"type": "initiative.subscribe", "payload": {"session_id": None}})
        socket.send_json({"type": "ping"})
        assert socket.receive_json()["type"] == "pong"  # subscribed before the check
        assert http.portal is not None
        http.portal.call(_find, container, gap.json()["id"])

        checked = http.post("/v1/initiative/check", headers=auth).json()
        pushed = socket.receive_json()

    assert checked["offered"]
    assert pushed["type"] == "initiative.message"
    assert pushed["payload"]["offer_id"] == checked["offer_id"]
    history = http.get(f"/v1/sessions/{checked['session_id']}/messages", headers=auth).json()
    assert history["messages"][0]["offer_id"] == checked["offer_id"]
    assert http.post(f"/v1/initiative/{checked['offer_id']}/dismiss", headers=auth).json() == {
        "dismissed": True,
        "kind": "finding",
    }
    status = http.get("/v1/initiative", headers=auth).json()
    assert status["offers"][0]["status"] == "dismissed"
    assert http.post("/v1/initiative/ini_nope/dismiss", headers=auth).status_code == 404


async def _find(container: Container, gap_id: str) -> None:
    await container.curiosity.add_finding(
        gap_id=gap_id,
        summary="The sun and moon pull at right angles.",
        sources=SOURCE,
        status="new",
        novelty=1.0,
        relevance=1.0,
    )
