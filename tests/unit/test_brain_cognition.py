"""Cognition inside a turn (docs/07 §14).

The snapshot, the directives that ride on it, and the verdict announcement — each tested
without a graph, because each is a node calling one port or a pure function.
"""

from __future__ import annotations

from typing import Any

import pytest

from hedwig.brain import (
    Collaborators,
    DefaultMindReader,
    InMemoryConversation,
    PermissiveGuard,
    RecordingAnnouncer,
    RulePlanner,
    StubRecaller,
    StubResponder,
    StubToolRunner,
    assemble_context,
    make_nodes,
    new_turn,
)
from hedwig.core.ports.brain import (
    MindSnapshot,
    RecalledContext,
    SafetyVerdict,
    TurnPolicy,
)


def _deps(**overrides: object) -> Collaborators:
    base: dict[str, object] = {
        "guard": PermissiveGuard(),
        "mind": DefaultMindReader(),
        "recaller": StubRecaller(),
        "planner": RulePlanner(),
        "responder": StubResponder(),
        "tools": StubToolRunner(),
        "conversation": InMemoryConversation(),
        "announcer": RecordingAnnouncer(),
    }
    base.update(overrides)
    return Collaborators(**base)  # type: ignore[arg-type]


def _state() -> dict[str, Any]:
    return dict(new_turn(turn_id="t", session_id="s", correlation_id="c", text="hello"))


class _Mind:
    """A mind reader with a mood behind it, without needing the emotion engine."""

    def __init__(self, snapshot: MindSnapshot) -> None:
        self._snapshot = snapshot
        self.reads = 0

    async def snapshot(self) -> MindSnapshot:
        self.reads += 1
        return self._snapshot


# =========================================================================
# The snapshot
# =========================================================================


async def test_the_turn_learns_its_own_mood_not_only_its_policy() -> None:
    """The gap Milestone 7 left: a turn could act on a mood and not report it."""
    mind = _Mind(
        MindSnapshot(
            policy=TurnPolicy(diversity=0.7),
            mood={"happiness": 0.8, "stress": 0.1},
            valence=0.6,
            arousal=0.4,
            directives=("Lightness is welcome where it fits.",),
            reference="emo_1",
        )
    )
    nodes = make_nodes(_deps(mind=mind))

    result = await nodes["snapshot"](_state())  # type: ignore[arg-type]

    assert result["mind"].mood == {"happiness": 0.8, "stress": 0.1}
    assert result["mind"].reference == "emo_1"
    assert result["policy"].diversity == 0.7
    assert result["metrics"]["valence"] == 0.6


async def test_cognition_is_read_exactly_once_per_turn() -> None:
    """docs/07 §4 rule 2. Two reads a millisecond apart could disagree, and a turn whose
    policy came from one mood and whose record came from another is unexplainable."""
    mind = _Mind(MindSnapshot(mood={"happiness": 0.5}))
    nodes = make_nodes(_deps(mind=mind))

    await nodes["snapshot"](_state())  # type: ignore[arg-type]

    assert mind.reads == 1


async def test_a_flat_mind_is_distinguishable_from_a_neutral_one() -> None:
    """ "No emotion engine" and "every dimension reads 0.5" are different situations."""
    nodes = make_nodes(_deps(mind=DefaultMindReader()))

    result = await nodes["snapshot"](_state())  # type: ignore[arg-type]

    assert result["mind"].is_flat
    assert result["mind"].mood == {}


# =========================================================================
# The verdict, announced
# =========================================================================


async def test_the_guard_announces_how_it_read_the_input() -> None:
    announcer = RecordingAnnouncer()
    nodes = make_nodes(_deps(announcer=announcer))

    await nodes["guard"](_state())  # type: ignore[arg-type]

    assert "input_appraised" in announcer.types()


async def test_a_blocked_input_is_announced_as_blocked() -> None:
    announcer = RecordingAnnouncer()
    nodes = make_nodes(
        _deps(
            guard=PermissiveGuard(blocked_phrases=frozenset({"forbidden"})),
            announcer=announcer,
        )
    )
    state = _state()
    state["input"] = "a forbidden thing"

    await nodes["guard"](state)  # type: ignore[arg-type]

    appraisals = [payload for name, payload in announcer.events if name == "input_appraised"]
    assert appraisals[0]["allowed"] is False


async def test_the_announcement_carries_no_message_text() -> None:
    """The door docs/09 §4.2 closed at the front, kept shut at the side.

    This is the only place where content could reach emotion, so the payload is asserted
    rather than trusted.
    """
    from hedwig.brain.announcer import BusAnnouncer

    published: list[tuple[str, dict[str, Any]]] = []

    class _Bus:
        async def emit(self, type_: str, payload: dict[str, Any], **kwargs: object) -> None:
            published.append((type_, payload))

    secret = "my passport number is 123456789"
    await BusAnnouncer(_Bus()).input_appraised(  # type: ignore[arg-type]
        SafetyVerdict(allowed=False, flags=("blocked_phrase",), reason=f"contains {secret}")
    )

    assert published
    assert secret not in str(published[0][1])
    assert set(published[0][1]) == {"allowed", "trust", "injection_score", "flags"}


# =========================================================================
# Directives reaching the context
# =========================================================================


def test_directives_travel_with_the_response_context() -> None:
    """Emotion produces them, the brain carries them, the responder renders them."""
    mind = MindSnapshot(
        directives=("Ask a follow-up question when it would genuinely help.",),
        mood={"curiosity": 0.9},
    )

    context = assemble_context(mind=mind)

    assert context.directives == mind.directives
    assert context.mood == {"curiosity": 0.9}


def test_a_context_assembled_without_a_mind_carries_no_directives() -> None:
    assert assemble_context(recalled=RecalledContext()).directives == ()


async def test_the_composed_context_reaches_the_responder_with_its_directives() -> None:
    """The end-to-end version of the claim, at node level."""
    mind = _Mind(
        MindSnapshot(
            policy=TurnPolicy(style=("Use short, simple sentences.",)),
            mood={"stress": 0.9},
            directives=("Use short, simple sentences.",),
        )
    )
    nodes = make_nodes(_deps(mind=mind))

    state = _state()
    state.update(await nodes["snapshot"](state))  # type: ignore[arg-type]
    result = await nodes["compose"](state)  # type: ignore[arg-type]

    assert result["response_context"].directives == ("Use short, simple sentences.",)
    assert result["response_context"].mood == {"stress": 0.9}


# =========================================================================
# What the turn announces
# =========================================================================


async def test_the_completed_turn_carries_the_mood_it_ran_under() -> None:
    announcer = RecordingAnnouncer()
    mind = _Mind(MindSnapshot(mood={"happiness": 0.7, "stress": 0.2}))
    nodes = make_nodes(_deps(mind=mind, announcer=announcer))

    state = _state()
    state.update(await nodes["snapshot"](state))  # type: ignore[arg-type]
    await nodes["learn"](state)  # type: ignore[arg-type]

    summary = next(p["summary"] for name, p in announcer.events if name == "turn_completed")
    assert summary.mood == {"happiness": 0.7, "stress": 0.2}


async def test_the_reply_is_recorded_against_the_mood_that_produced_it() -> None:
    """docs/05 §5.1's `emotion_ref`, finally written."""
    conversation = InMemoryConversation()
    mind = _Mind(MindSnapshot(mood={"happiness": 0.6}, reference="emo_42"))
    nodes = make_nodes(_deps(mind=mind, conversation=conversation))

    state = _state()
    state.update(await nodes["snapshot"](state))  # type: ignore[arg-type]
    state["reply"] = "a reply"
    state["reply_message_id"] = "msg_reply"
    await nodes["finalize"](state)  # type: ignore[arg-type]

    assert conversation.emotion_refs == ["emo_42"]


@pytest.mark.parametrize("field_name", ["mind", "policy"])
async def test_no_other_node_writes_the_cognition_fields(field_name: str) -> None:
    """The single-writer rule that makes last-write-wins safe (docs/07 §4 rule 1)."""
    nodes = make_nodes(_deps())
    state = _state()
    state.update(await nodes["snapshot"](state))  # type: ignore[arg-type]

    for name, node in nodes.items():
        if name == "snapshot":
            continue
        written = await node(state)  # type: ignore[arg-type]
        assert field_name not in written, f"{name} also writes {field_name}"
