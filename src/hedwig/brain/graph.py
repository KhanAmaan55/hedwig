"""The turn graph (docs/07 §3).

Assembles the nodes into the shape documented in docs/07 and compiles it with a
checkpointer. This file is the only one that imports LangGraph — everything cognitive lives
behind `Collaborators`, so replacing the framework touches this module and `nodes.py` and
nothing else (ADR-0004).

Read the edges below and you have read the control flow of a turn:

    ingest → guard → snapshot → plan_queries → recall → deliberate
                  ↘ refuse                              ↙ ↑
                        compose → express → emit → learn → finalize
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from hedwig.brain.nodes import Collaborators, make_nodes
from hedwig.brain.routing import (
    DEFAULT_CAPS,
    TurnCaps,
    after_act,
    after_approve,
    after_deliberate,
    after_guard,
)
from hedwig.brain.state import TurnState, new_turn, summarise
from hedwig.core.context import correlation_scope
from hedwig.core.ids import new_id
from hedwig.core.live import emit_live
from hedwig.core.logging import fields, get_logger
from hedwig.core.ports.sessions import MessageRole
from hedwig.core.tracing import SpanRecorder
from hedwig.core.types import TrustTier

logger = get_logger(__name__)


def build_turn_graph(deps: Collaborators, *, checkpointer: Any | None = None) -> Any:
    """Compile the turn graph.

    `checkpointer` defaults to in-memory. docs/07 §6 specifies `SqliteSaver` against
    `checkpoints.db`; that is deferred until there is something worth resuming — a turn
    that calls no model and touches no memory is cheaper to re-run than to restore, and
    the swap is one argument.
    """
    nodes = {
        name: _traced(name, _staged(name, node), deps.spans)
        for name, node in make_nodes(deps).items()
    }
    # Typed loosely on purpose: LangGraph's generics describe its own machinery, and
    # pinning them here would couple us to a signature that changes between releases.
    builder: Any = StateGraph(TurnState)

    for name, node in nodes.items():
        builder.add_node(name, node)

    builder.add_edge(START, "ingest")
    builder.add_edge("ingest", "guard")

    # The only place a turn stops being a conversation.
    builder.add_conditional_edges(
        "guard", after_guard, {"snapshot": "snapshot", "refuse": "refuse"}
    )

    builder.add_edge("snapshot", "plan_queries")
    builder.add_edge("plan_queries", "recall")
    builder.add_edge("recall", "deliberate")

    builder.add_conditional_edges(
        "deliberate",
        after_deliberate,
        {"compose": "compose", "approve": "approve", "act": "act"},
    )
    builder.add_conditional_edges("approve", after_approve, {"act": "act", "compose": "compose"})
    # The loop. Bounded by the caps in routing.py, which is what makes the graph
    # provably terminating rather than hopefully terminating.
    builder.add_conditional_edges(
        "act",
        lambda state: after_act(state, deps.caps),
        {"deliberate": "deliberate", "compose": "compose"},
    )

    builder.add_edge("compose", "express")
    builder.add_edge("refuse", "express")
    builder.add_edge("express", "emit")
    builder.add_edge("emit", "learn")
    builder.add_edge("learn", "finalize")
    builder.add_edge("finalize", END)

    return builder.compile(checkpointer=checkpointer or InMemorySaver())


STAGES = {
    "recall": "recalling",
    "deliberate": "deliberating",
    "act": "acting",
    "compose": "composing",
}
"""The stages a watcher is told about (docs/16 §5.3). Only the ones worth a word on screen."""


def _staged(name: str, node: Any) -> Any:
    """Announce the stage to a live watcher, if any (ADR-0023), then run the node unchanged."""
    stage = STAGES.get(name)
    if stage is None:
        return node

    async def run(state: TurnState) -> TurnState:
        detail = None
        if name == "act" and (request := state.get("tool_request")) is not None:
            detail = request.name
        await emit_live("stage", {"stage": stage, "detail": detail})
        result: TurnState = await node(state)
        return result

    return run


def _traced(name: str, node: Any, spans: SpanRecorder | None) -> Any:
    """Record the stage's time as a span (docs/19 §13). Content never: a name and a duration."""
    if spans is None:
        return node

    async def run(state: TurnState) -> TurnState:
        started_at, began = spans.now(), time.monotonic()
        ok = False
        try:
            result: TurnState = await node(state)
            ok = True
            return result
        finally:
            spans.record(
                name,
                kind="stage",
                started_at=started_at,
                duration_ms=(time.monotonic() - began) * 1000,
                ok=ok,
                correlation_id=state.get("correlation_id"),
            )

    return run


@dataclass(slots=True)
class _SessionLock:
    """A lock, and how many callers hold or await it — so it can be dropped when idle."""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0


@dataclass(slots=True)
class Brain:
    """The turn graph as a service.

    Registered in the container like everything else, so a caller asks the brain to run a
    turn and receives the finished state. What happens inside is the graph's business.
    """

    collaborators: Collaborators
    checkpointer: Any | None = None
    _graph: Any = None
    _sessions: dict[str, _SessionLock] = field(default_factory=dict)
    _activity: asyncio.Condition = field(default_factory=asyncio.Condition)
    """One lock per session with a turn in flight or waiting (docs/07 §16)."""

    def __post_init__(self) -> None:
        self._graph = build_turn_graph(self.collaborators, checkpointer=self.checkpointer)

    @property
    def graph(self) -> Any:
        return self._graph

    async def open_session(self, *, channel: str = "api") -> str:
        """Start a conversation and announce it. Returns the new session id.

        Here rather than in the API route because the announcement belongs to the brain: had
        a route opened the row itself, the turn's `ingest` would find the session already
        open and never announce `conversation.session.started`, which emotion appraises
        (docs/16 §12.3).
        """
        conversation = self.collaborators.conversation
        session_id = new_id("sess")
        if await conversation.open(session_id, channel=channel):
            await self.collaborators.announcer.session_started(
                session_id=session_id, channel=channel
            )
        return session_id

    async def speak_first(
        self,
        text: str,
        *,
        session_id: str | None,
        trust: TrustTier,
        meta: Mapping[str, Any],
        channel: str = "web",
    ) -> tuple[str, str]:
        """Say something unprompted into a conversation (ADR-0026). Returns (session, message).

        Under the session lock, so it can never land inside a turn. A session the idle reaper
        ended is resumed and announced as a new conversation, like any resumed session.
        """
        conversation = self.collaborators.conversation
        target = session_id or new_id("sess")
        async with self._session_turn(target):
            if await conversation.open(target, channel=channel):
                await self.collaborators.announcer.session_started(
                    session_id=target, channel=channel
                )
            message_id = new_id("msg")
            await conversation.record_message(
                session_id=target,
                message_id=message_id,
                role=MessageRole.HEDWIG,
                text=text,
                trust=trust,
                meta=meta,
            )
        logger.info("spoke first", extra=fields(session=target, message=message_id))
        return target, message_id

    async def run_turn(
        self,
        text: str,
        *,
        session_id: str = "session_local",
        turn_id: str | None = None,
        correlation_id: str | None = None,
        client_message_id: str | None = None,
    ) -> TurnState:
        """Run one turn to completion — or, for a retried client message, return the first.

        The replay check sits inside the session lock: a retry that arrives while the original
        is still running waits for it, then gets it back (FND-06).
        """
        resolved_turn = turn_id or new_id("turn")
        state = new_turn(
            turn_id=resolved_turn,
            session_id=session_id,
            correlation_id=correlation_id or resolved_turn,
            text=text,
            client_message_id=client_message_id,
        )
        config = {"configurable": {"thread_id": resolved_turn}}

        # One turn at a time per session: a second message waits, then sees the first's reply
        # in its window (docs/07 §16).
        # The turn binds its own correlation id, so every row it writes — model calls, tools,
        # events, spans — joins up whoever called it: the API, the CLI or a test (docs/19 §3).
        with correlation_scope(state["correlation_id"]):
            async with self._session_turn(session_id):
                if client_message_id:
                    earlier = await self.collaborators.conversation.replay(
                        session_id, client_message_id
                    )
                    if earlier is not None:
                        logger.info("turn replayed", extra=fields(turn=earlier.get("turn_id")))
                        replayed: TurnState = TurnState(**earlier)  # type: ignore[typeddict-item]
                        return replayed
                final: TurnState = await self._graph.ainvoke(state, config=config)
            logger.info("turn completed", extra=fields(**summarise(final)))
        return final

    @property
    def busy(self) -> bool:
        """Whether any turn is running or waiting — what the avatar shows as thinking (AVA-01).

        True exactly while a session lock is held or awaited: the lock map is reference-counted
        and empties when the last turn leaves (docs/07 §16).
        """
        return bool(self._sessions)

    async def end_session(self, session_id: str) -> bool:
        """End a session once its in-flight turn, if any, has finished (FND-03).

        Waiting is what guarantees the turn's `turn.completed` is published before
        `session.ended`, so the relationship update counts that turn (docs/27 §5.1).
        """
        async with self._session_turn(session_id):
            return await self.collaborators.conversation.end(session_id, reason="explicit")

    @asynccontextmanager
    async def _session_turn(self, session_id: str) -> AsyncIterator[None]:
        was_idle = not self._sessions
        entry = self._sessions.get(session_id)
        if entry is None:
            entry = self._sessions[session_id] = _SessionLock()
        entry.users += 1
        if was_idle:
            await self._announce_activity()
        try:
            async with entry.lock:
                yield
        finally:
            entry.users -= 1
            if entry.users == 0:
                del self._sessions[session_id]
            if not self._sessions:
                await self._announce_activity()

    async def wait_for_activity(self, timeout: float) -> None:
        """Return when HEDWIG starts or stops thinking, or after `timeout` (EMO-02).

        What lets the avatar show "thinking" the moment a turn begins, from any client,
        without anyone polling faster.
        """
        async with self._activity:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._activity.wait(), timeout)

    async def _announce_activity(self) -> None:
        async with self._activity:
            self._activity.notify_all()

    async def health(self) -> Any:
        """Report what the graph is made of.

        Honest about the stubs: a health endpoint that reported "ok" while every capability
        was fake would be the most misleading thing in the system.
        """
        from hedwig.brain.stubs import (
            DefaultMindReader,
            InMemoryConversation,
            PermissiveGuard,
            RecordingAnnouncer,
            StubRecaller,
            StubResponder,
            StubToolRunner,
        )
        from hedwig.core.ports import Health, HealthStatus

        stubs = (
            PermissiveGuard
            | DefaultMindReader
            | StubRecaller
            | StubResponder
            | StubToolRunner
            | InMemoryConversation
            | RecordingAnnouncer
        )
        stubbed = sorted(
            name
            for name, value in (
                ("guard", self.collaborators.guard),
                ("mind", self.collaborators.mind),
                ("recaller", self.collaborators.recaller),
                ("responder", self.collaborators.responder),
                ("tools", self.collaborators.tools),
                ("conversation", self.collaborators.conversation),
                ("announcer", self.collaborators.announcer),
            )
            if isinstance(value, stubs)
        )
        return Health(
            status=HealthStatus.DEGRADED if stubbed else HealthStatus.OK,
            message=f"stubbed: {', '.join(stubbed)}" if stubbed else "",
            detail={
                "nodes": len(make_nodes(self.collaborators)),
                "stubbed": stubbed,
                "caps": {
                    "max_tool_iterations": self.collaborators.caps.max_tool_iterations,
                    "max_tool_calls": self.collaborators.caps.max_tool_calls,
                    "max_turn_seconds": self.collaborators.caps.max_turn_seconds,
                },
            },
        )


def default_brain(caps: TurnCaps = DEFAULT_CAPS) -> Brain:
    """A brain wired entirely from stubs. What Milestone 4 ships."""
    from hedwig.brain.planner import RulePlanner
    from hedwig.brain.stubs import (
        DefaultMindReader,
        InMemoryConversation,
        PermissiveGuard,
        RecordingAnnouncer,
        StubRecaller,
        StubResponder,
        StubToolRunner,
    )

    return Brain(
        Collaborators(
            guard=PermissiveGuard(),
            mind=DefaultMindReader(),
            recaller=StubRecaller(),
            planner=RulePlanner(),
            responder=StubResponder(),
            tools=StubToolRunner(),
            conversation=InMemoryConversation(),
            announcer=RecordingAnnouncer(),
            caps=caps,
        )
    )
