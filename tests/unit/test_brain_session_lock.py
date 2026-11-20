"""One turn at a time per session, and ending waits for the turn (FND-04, docs/07 §16)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from hedwig.brain import (
    Brain,
    Collaborators,
    DefaultMindReader,
    InMemoryConversation,
    PermissiveGuard,
    RecordingAnnouncer,
    RulePlanner,
    StubRecaller,
    StubResponder,
    StubToolRunner,
)
from hedwig.core.ports.brain import Plan, ResponseContext, TurnPolicy


@dataclass(slots=True)
class GatedResponder(StubResponder):
    """Composes only when a test opens the gate for that input, and records what it saw."""

    gates: dict[str, asyncio.Event] = field(default_factory=dict)
    seen: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)

    async def compose(
        self, *, text: str, plan: Plan, context: ResponseContext, policy: TurnPolicy
    ) -> str:
        self.seen.append((text, tuple(m.text for m in context.window)))
        if gate := self.gates.get(text):
            await gate.wait()
        return f"reply to {text}"


def _brain(responder: GatedResponder, conversation: InMemoryConversation) -> Brain:
    return Brain(
        Collaborators(
            guard=PermissiveGuard(),
            mind=DefaultMindReader(),
            recaller=StubRecaller(),
            planner=RulePlanner(),
            responder=responder,
            tools=StubToolRunner(),
            conversation=conversation,
            announcer=RecordingAnnouncer(),
        )
    )


async def _settle() -> None:
    for _ in range(50):
        await asyncio.sleep(0)


async def test_a_second_message_waits_and_sees_the_first_reply() -> None:
    gate = asyncio.Event()
    responder = GatedResponder(gates={"first message here": gate})
    conversation = InMemoryConversation()
    brain = _brain(responder, conversation)

    first = asyncio.create_task(brain.run_turn("first message here", session_id="s"))
    await _settle()
    second = asyncio.create_task(brain.run_turn("second message here", session_id="s"))
    await _settle()

    # The second turn has not even been recorded: it is waiting for the session.
    assert [text for _, _, text in conversation.messages] == ["first message here"]

    gate.set()
    await asyncio.gather(first, second)

    assert [text for _, _, text in conversation.messages] == [
        "first message here",
        "reply to first message here",
        "second message here",
        "reply to second message here",
    ]
    window_of_second = dict(responder.seen)["second message here"]
    assert "reply to first message here" in window_of_second


async def test_different_sessions_do_not_wait_for_each_other() -> None:
    gate = asyncio.Event()
    responder = GatedResponder(gates={"slow message here": gate})
    brain = _brain(responder, InMemoryConversation())

    slow = asyncio.create_task(brain.run_turn("slow message here", session_id="a"))
    await _settle()
    other = await asyncio.wait_for(brain.run_turn("quick message here", session_id="b"), 1.0)

    assert other["reply"] == "reply to quick message here"
    assert not slow.done()
    gate.set()
    await slow


async def test_ending_waits_for_the_turn_in_flight() -> None:
    gate = asyncio.Event()
    conversation = InMemoryConversation()
    brain = _brain(GatedResponder(gates={"still talking here": gate}), conversation)

    turn = asyncio.create_task(brain.run_turn("still talking here", session_id="s"))
    await _settle()
    ending = asyncio.create_task(brain.end_session("s"))
    await _settle()

    assert not ending.done(), "the session ended under a running turn"
    assert "s" not in conversation.ended

    gate.set()
    await turn
    assert await ending is True
    assert "s" in conversation.ended


async def test_ending_twice_ends_once() -> None:
    conversation = InMemoryConversation()
    brain = _brain(GatedResponder(), conversation)
    await brain.run_turn("hello there friend", session_id="s")

    assert await brain.end_session("s") is True
    assert await brain.end_session("s") is False
    assert await brain.end_session("never-opened") is False


async def test_locks_do_not_accumulate() -> None:
    brain = _brain(GatedResponder(), InMemoryConversation())
    for index in range(5):
        await brain.run_turn("hello there friend", session_id=f"s{index}")
        await brain.end_session(f"s{index}")

    assert brain._sessions == {}
