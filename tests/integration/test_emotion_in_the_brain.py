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
