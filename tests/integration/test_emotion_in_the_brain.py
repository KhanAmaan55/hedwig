"""Emotion reaching a real turn, and the API that exposes it (docs/09 §6, docs/16 §4).

The Milestone-4 `MindReader` seam being cashed in a second time: the `snapshot` node was
written against a narrow port with `DefaultMindReader` behind it, and switching to real
emotional state is one adapter plus one line in `wiring.py`. If `nodes.py` had needed to
change, the seam was decoration.

The prohibition tests at the end are the ones that matter most. Emotion may change how
HEDWIG speaks; it may not change what is refused, what needs approval, or what is recalled.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from hedwig.api.app import create_app
from hedwig.core.clock import FakeClock
from hedwig.core.ports.emotion import Dimension, EmotionState
from hedwig.emotion import EmotionMindReader
from hedwig.wiring import Container


@pytest.fixture
async def hedwig(running_container: Container) -> Container:
    return running_container


@pytest.fixture
async def api(running_container: Container) -> AsyncIterator[AsyncClient]:
    """A client over a *started* container.

    The shared `client` fixture builds an app over an unstarted one, which is right for a
    liveness probe and wrong here: these tests publish events and expect the bus to be
    draining them.
    """
    transport = ASGITransport(app=create_app(running_container))
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


def _engine(container: Container) -> Any:
    """The container types `emotion` as the read-only port, which is right for production
    and unhelpful here: these tests drive it."""
    assert container.emotion is not None
    return container.emotion


def _set(container: Container, **dimensions: float) -> None:
    """Drive the state directly. Bypasses the tick on purpose: these tests are about what
    the state *does*, not about how it got there."""
    state = EmotionState()
    for name, value in dimensions.items():
        state = state.with_dimension(Dimension(name), value)
    _engine(container)._state = state


# =========================================================================
# The seam
# =========================================================================


async def test_the_brain_reads_real_emotional_state_without_node_changes(
    hedwig: Container,
) -> None:
    assert isinstance(hedwig.brain.collaborators.mind, EmotionMindReader)


async def test_curiosity_widens_retrieval_in_an_actual_turn(hedwig: Container) -> None:
    _set(hedwig, curiosity=0.1)
    incurious = await hedwig.brain.run_turn("tell me about harbour cranes", session_id="s1")

    _set(hedwig, curiosity=0.95)
    curious = await hedwig.brain.run_turn("tell me about harbour cranes", session_id="s2")

    assert curious["policy"].diversity > incurious["policy"].diversity


async def test_stress_shortens_the_reply_budget_in_an_actual_turn(hedwig: Container) -> None:
    _set(hedwig, stress=0.0)
    calm = await hedwig.brain.run_turn("explain the ferry timetable", session_id="s3")

    _set(hedwig, stress=0.9)
    strained = await hedwig.brain.run_turn("explain the ferry timetable", session_id="s4")

    assert strained["policy"].max_tokens < calm["policy"].max_tokens


async def test_low_confidence_buys_a_larger_retrieval_budget(hedwig: Container) -> None:
    _set(hedwig, confidence=0.9)
    sure = await hedwig.brain.run_turn("what did we decide?", session_id="s5")

    _set(hedwig, confidence=0.1)
    unsure = await hedwig.brain.run_turn("what did we decide?", session_id="s6")

    assert unsure["policy"].token_budget > sure["policy"].token_budget


async def test_style_directives_reach_the_turn(hedwig: Container) -> None:
    _set(hedwig, trust=0.9, curiosity=0.9, happiness=0.9)
    final = await hedwig.brain.run_turn("hello again", session_id="s7")

    assert final["policy"].style


# =========================================================================
# The loop closes: a turn changes the mood that shapes the next one
# =========================================================================


async def test_a_completed_turn_moves_the_mood(hedwig: Container, clock: FakeClock) -> None:
    """Observe -> appraise -> integrate, through the real bus."""
    before = await _engine(hedwig).state()

    await hedwig.brain.run_turn("thanks, that was exactly what I needed", session_id="s8")
    await hedwig.bus.drain()
    clock.advance(seconds=30)
    after = await _engine(hedwig).tick()

    assert before.distance(after) > 0


async def test_a_refusal_does_not_read_as_failure(hedwig: Container, clock: FakeClock) -> None:
    """The rule worth protecting: HEDWIG must not learn to dread its own safety behaviour."""
    from dataclasses import replace

    from hedwig.brain import Brain, PermissiveGuard

    guarded = Brain(
        replace(
            hedwig.brain.collaborators,
            guard=PermissiveGuard(blocked_phrases=frozenset({"forbidden"})),
        )
    )
    _set(hedwig, confidence=0.5, stress=0.15, trust=0.6, happiness=0.55, curiosity=0.55, energy=0.5)

    await guarded.run_turn("this contains a forbidden phrase", session_id="s9")
    await hedwig.bus.drain()
    clock.advance(seconds=30)
    refused = await _engine(hedwig).tick()

    _set(hedwig, confidence=0.5, stress=0.15, trust=0.6, happiness=0.55, curiosity=0.55, energy=0.5)
    await hedwig.brain.run_turn("an ordinary question", session_id="s10")
    await hedwig.bus.drain()
    # Force the failure path through the same machinery.
    await _engine(hedwig)._on_event(_failure_event(hedwig))
    clock.advance(seconds=30)
    failed = await _engine(hedwig).tick()

    assert refused.confidence > failed.confidence


def _failure_event(container: Container) -> Any:
    from datetime import UTC, datetime

    from hedwig.core.ports.event_bus import Event

    return Event(
        id="evt_fail",
        type="conversation.turn.completed",
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        source="test",
        correlation_id="c",
        payload={"turn_id": "t", "session_id": "s", "status": "failed"},
    )


# =========================================================================
# The hard prohibitions (docs/09 §6.1)
# =========================================================================


@pytest.mark.parametrize(
    "mood",
    [
        {
            "happiness": 1.0,
            "trust": 1.0,
            "curiosity": 1.0,
            "confidence": 1.0,
            "energy": 1.0,
            "stress": 0.0,
        },
        {
            "happiness": 0.0,
            "trust": 0.25,
            "curiosity": 0.0,
            "confidence": 0.0,
            "energy": 0.0,
            "stress": 1.0,
        },
    ],
    ids=["elated", "flattened"],
)
async def test_no_mood_changes_what_is_refused(hedwig: Container, mood: dict[str, float]) -> None:
    from dataclasses import replace

    from hedwig.brain import Brain, PermissiveGuard

    guarded = Brain(
        replace(
            hedwig.brain.collaborators,
            guard=PermissiveGuard(blocked_phrases=frozenset({"forbidden"})),
        )
    )
    _set(hedwig, **mood)

    final = await guarded.run_turn("this contains a forbidden phrase", session_id="s11")

    assert final["status"] == "refused"
    assert "refuse" in final["visited"]


@pytest.mark.parametrize(
    "mood",
    [
        {"confidence": 1.0, "stress": 0.0, "trust": 1.0},
        {"confidence": 0.0, "stress": 1.0, "trust": 0.25},
    ],
    ids=["assured", "rattled"],
)
async def test_no_mood_changes_whether_tools_are_allowed(
    hedwig: Container, mood: dict[str, float]
) -> None:
    """Approval requirements are policy, never mood."""
    _set(hedwig, **mood)
    final = await hedwig.brain.run_turn("what is the time?", session_id="s12")

    assert final["policy"].allow_tools is True


async def test_no_mood_changes_what_is_recalled(hedwig: Container) -> None:
    """Style may vary with mood; the facts may not (docs/09 §6.1).

    Retrieval runs directly here rather than through two turns, because a turn *writes* — the
    second turn would recall a memory the first one created, and the test would be measuring
    the store growing rather than the mood changing.
    """
    from hedwig.core.ports.brain import TurnPolicy
    from hedwig.emotion.bindings import turn_policy

    await hedwig.brain.run_turn(
        "Please remember the sourdough starter doubled overnight.", session_id="s13"
    )
    await hedwig.bus.drain()

    recaller = hedwig.brain.collaborators.recaller
    base = TurnPolicy()
    calm = await recaller.recall(
        ["sourdough starter"], policy=turn_policy(EmotionState(stress=0.0), base=base)
    )
    strained = await recaller.recall(
        ["sourdough starter"], policy=turn_policy(EmotionState(stress=1.0), base=base)
    )

    assert calm.items, "nothing was recalled at all, so the test proves nothing"
    assert [item.memory_id for item in calm.items] == [item.memory_id for item in strained.items]


async def test_a_flattened_mood_still_recalls_the_relevant_memory(hedwig: Container) -> None:
    """The same claim through the whole graph, stated as the thing a user would notice."""
    await hedwig.brain.run_turn(
        "Please remember the ferry timetable changed in March.", session_id="s16"
    )
    await hedwig.bus.drain()

    _set(hedwig, happiness=0.0, trust=0.25, curiosity=0.0, confidence=0.0, energy=0.0, stress=1.0)
    final = await hedwig.brain.run_turn("what happened to the ferry timetable?", session_id="s16")

    assert any("ferry" in item.text for item in final["context"].items)


# =========================================================================
# The API
# =========================================================================


async def test_the_endpoint_reports_the_state_and_what_it_does(api: AsyncClient) -> None:
    response = await api.get("/v1/mind/emotion")

    assert response.status_code == 200
    body = response.json()
    assert set(body) >= {
        "happiness",
        "trust",
        "curiosity",
        "confidence",
        "energy",
        "stress",
        "valence",
        "arousal",
        "baselines",
        "behaviour",
    }
    assert 0.0 <= body["behaviour"]["diversity"] <= 1.0


async def test_the_endpoint_exposes_no_user_content(api: AsyncClient, hedwig: Container) -> None:
    """The reason these two endpoints ship before the bearer token (docs/16 §3): six floats
    about HEDWIG, and nothing a person said."""
    secret = "my passport number is 123456789"
    await hedwig.brain.run_turn(secret, session_id="s14")
    await hedwig.bus.drain()
    await _engine(hedwig).tick()

    for path in ("/v1/mind/emotion", "/v1/mind/emotion/history"):
        assert secret not in (await api.get(path)).text


async def test_the_history_endpoint_returns_a_timeline(
    api: AsyncClient, hedwig: Container, clock: FakeClock
) -> None:
    for index in range(3):
        await hedwig.brain.run_turn(f"something happened, number {index}", session_id="s15")
        await hedwig.bus.drain()
        clock.advance(seconds=30)
        await _engine(hedwig).tick()

    body = (await api.get("/v1/mind/emotion/history?limit=10")).json()

    assert body["count"] >= 1
    assert "recorded_at" in body["entries"][0]


async def test_health_reports_emotion_as_a_service(api: AsyncClient) -> None:
    body = (await api.get("/v1/health")).json()

    assert "emotion" in body["subsystems"]


# =========================================================================
# The loop closes both ways (docs/07 §14)
# =========================================================================


async def test_the_turn_records_the_mood_it_ran_under(hedwig: Container) -> None:
    """docs/05 §5.1's `emotion_ref`. A mood timeline beside a conversation is decoration;
    one joined to the messages it produced is an explanation."""
    await hedwig.brain.run_turn("something worth recording", session_id="s20")

    row = hedwig.database.query_one(
        "SELECT emotion_ref FROM message WHERE session_id = 's20' AND role = 'hedwig'"
    )
    assert row is not None
    assert row["emotion_ref"] is not None

    history = hedwig.database.query_one(
        "SELECT dims FROM emotion_history WHERE id = ?", (row["emotion_ref"],)
    )
    assert history is not None, "the reply points at a mood that does not exist"


async def test_the_guards_verdict_reaches_emotion(hedwig: Container, clock: FakeClock) -> None:
    """A blocked input is a thing that happened, and cognition should see it (docs/07 §14.3)."""
    from dataclasses import replace

    from hedwig.brain import Brain, PermissiveGuard

    guarded = Brain(
        replace(
            hedwig.brain.collaborators,
            guard=PermissiveGuard(blocked_phrases=frozenset({"forbidden"})),
        )
    )
    await guarded.run_turn("this contains a forbidden phrase", session_id="s21")
    await hedwig.bus.drain()
    clock.advance(seconds=30)
    await _engine(hedwig).tick()

    rows = hedwig.database.query(
        "SELECT rationale FROM appraisal WHERE event_type = 'perception.input.appraised'"
    )
    assert rows, "the guard's verdict never reached the emotion engine"
    assert "blocked" in str(rows[0]["rationale"])


async def test_the_appraisal_of_an_input_never_records_its_text(hedwig: Container) -> None:
    """The one place content could reach emotion through a side door."""
    secret = "my passport number is 123456789"
    await hedwig.brain.run_turn(secret, session_id="s22")
    await hedwig.bus.drain()

    rows = hedwig.database.query("SELECT * FROM event WHERE type = 'perception.input.appraised'")
    assert rows
    for row in rows:
        assert secret not in str(dict(row))


async def test_emotion_events_are_correlated_with_the_turn_that_caused_them(
    hedwig: Container, clock: FakeClock
) -> None:
    """docs/04 §6: a turn, the memories it formed and the mood it produced share one id.

    Without this the Mind Inspector's causal graph breaks at exactly the link it exists to
    draw.
    """
    final = await hedwig.brain.run_turn("thanks, that was genuinely helpful", session_id="s23")
    await hedwig.bus.drain()
    clock.advance(seconds=30)
    await _engine(hedwig).tick()
    await hedwig.bus.drain()

    rows = hedwig.database.query(
        "SELECT correlation_id FROM event WHERE type = 'emotion.state.changed' "
        "ORDER BY occurred_at DESC LIMIT 1"
    )
    assert rows
    assert rows[0]["correlation_id"] == final["correlation_id"]


async def test_the_turn_event_carries_the_mood(hedwig: Container) -> None:
    await hedwig.brain.run_turn("an ordinary message", session_id="s24")
    await hedwig.bus.drain()

    row = hedwig.database.query_one(
        "SELECT payload FROM event WHERE type = 'conversation.turn.completed' "
        "ORDER BY occurred_at DESC LIMIT 1"
    )
    assert row is not None
    assert "happiness" in str(row["payload"])


async def test_directives_reach_the_composed_context_in_a_real_turn(hedwig: Container) -> None:
    """Emotion produces them, the brain carries them, the responder renders them."""
    _set(hedwig, curiosity=0.95, trust=0.9, happiness=0.9, confidence=0.9, energy=0.5, stress=0.0)

    final = await hedwig.brain.run_turn("tell me about the harbour", session_id="s25")

    assert final["response_context"].directives
    assert final["response_context"].directives == final["policy"].style
    assert final["response_context"].mood["curiosity"] == pytest.approx(0.95)


async def test_the_mood_reaching_a_turn_is_the_one_the_next_turn_reports(
    hedwig: Container, clock: FakeClock
) -> None:
    """The one-turn emotional latency of docs/02 §5, stated as a test rather than a note:
    what a turn does affects the *next* turn, not itself."""
    first = await hedwig.brain.run_turn("thanks, that was perfect", session_id="s26")
    await hedwig.bus.drain()
    clock.advance(seconds=30)
    await _engine(hedwig).tick()

    second = await hedwig.brain.run_turn("and another thing", session_id="s26")

    assert second["mind"].mood != first["mind"].mood
