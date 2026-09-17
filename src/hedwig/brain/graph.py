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

from dataclasses import dataclass
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
from hedwig.core.ids import new_id
from hedwig.core.logging import fields, get_logger

logger = get_logger(__name__)


def build_turn_graph(deps: Collaborators, *, checkpointer: Any | None = None) -> Any:
    """Compile the turn graph.

    `checkpointer` defaults to in-memory. docs/07 §6 specifies `SqliteSaver` against
    `checkpoints.db`; that is deferred until there is something worth resuming — a turn
    that calls no model and touches no memory is cheaper to re-run than to restore, and
    the swap is one argument.
    """
    nodes = make_nodes(deps)
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


@dataclass(slots=True)
class Brain:
    """The turn graph as a service.

    Registered in the container like everything else, so a caller asks the brain to run a
    turn and receives the finished state. What happens inside is the graph's business.
    """

    collaborators: Collaborators
    checkpointer: Any | None = None
    _graph: Any = None

    def __post_init__(self) -> None:
        self._graph = build_turn_graph(self.collaborators, checkpointer=self.checkpointer)

    @property
    def graph(self) -> Any:
        return self._graph

    async def run_turn(
        self,
        text: str,
        *,
        session_id: str = "session_local",
        turn_id: str | None = None,
        correlation_id: str | None = None,
    ) -> TurnState:
        """Run one turn to completion."""
        resolved_turn = turn_id or new_id("turn")
        state = new_turn(
            turn_id=resolved_turn,
            session_id=session_id,
            correlation_id=correlation_id or resolved_turn,
            text=text,
        )
        config = {"configurable": {"thread_id": resolved_turn}}

        final: TurnState = await self._graph.ainvoke(state, config=config)
        logger.info("turn completed", extra=fields(**summarise(final)))
        return final

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
