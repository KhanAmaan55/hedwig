"""The turn graph, its nodes, and the planner (docs/07).

The graph tests assert on `visited` — the node sequence — because the shape of the control
flow *is* the deliverable of this milestone. What the nodes produce is stubbed; the order
in which they run is not.
"""

from __future__ import annotations

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
    TurnCaps,
    default_brain,
    make_nodes,
    new_turn,
    summarise,
)
from hedwig.brain.stubs import STUB_MARKER
from hedwig.core.ports.brain import (
    ContextItem,
    Intent,
    MindSnapshot,
    Plan,
    RecalledContext,
    SafetyVerdict,
    ToolOutcome,
    ToolRequest,
    TurnPolicy,
)


def _brain(**overrides: object) -> Brain:
    deps = {
        "guard": PermissiveGuard(),
        "mind": DefaultMindReader(),
        "recaller": StubRecaller(),
        "planner": RulePlanner(),
        "responder": StubResponder(),
        "tools": StubToolRunner(),
        "conversation": InMemoryConversation(),
        "announcer": RecordingAnnouncer(),
        **overrides,
    }
    return Brain(Collaborators(**deps))  # type: ignore[arg-type]


# =========================================================================
# The planner — rules, exhaustively
# =========================================================================


async def test_an_empty_message_asks_rather_than_guesses() -> None:
    plan = await RulePlanner().deliberate(
        text="   ", context=RecalledContext(), policy=TurnPolicy(), outcomes=(), iteration=0
    )
    assert plan.intent is Intent.CLARIFY


async def test_a_clock_question_reaches_for_the_clock_tool() -> None:
    plan = await RulePlanner().deliberate(
        text="what time is it?",
        context=RecalledContext(),
        policy=TurnPolicy(),
        outcomes=(),
        iteration=0,
    )
    assert plan.intent is Intent.ACT
    assert plan.tool is not None
    assert plan.tool.name == "get_datetime"


async def test_arithmetic_reaches_for_the_calculator() -> None:
    plan = await RulePlanner().deliberate(
        text="please work out 12 * 7 for me",
        context=RecalledContext(),
        policy=TurnPolicy(),
        outcomes=(),
        iteration=0,
    )
    assert plan.tool is not None
    assert plan.tool.name == "calculate"
    assert plan.tool.arguments["expression"] == "12 * 7"


async def test_a_reference_to_the_past_reaches_for_memory() -> None:
    plan = await RulePlanner().deliberate(
        text="what did we discuss yesterday?",
        context=RecalledContext(),
        policy=TurnPolicy(),
        outcomes=(),
        iteration=0,
    )
    assert plan.tool is not None
    assert plan.tool.name == "search_memory"


async def test_the_planner_cannot_ask_for_a_tool_that_does_not_exist() -> None:
    """A planner bounded by the available tools cannot produce a request to be rejected."""
    planner = RulePlanner(available_tools=frozenset())

    plan = await planner.deliberate(
        text="what time is it?",
        context=RecalledContext(),
        policy=TurnPolicy(),
        outcomes=(),
        iteration=0,
    )

    assert plan.intent is Intent.ANSWER
    assert plan.tool is None


async def test_policy_can_disable_tools_for_a_turn() -> None:
    plan = await RulePlanner().deliberate(
        text="what time is it?",
        context=RecalledContext(),
        policy=TurnPolicy(allow_tools=False),
        outcomes=(),
        iteration=0,
    )
    assert plan.intent is Intent.ANSWER


async def test_after_a_tool_runs_the_planner_answers() -> None:
    """Two rounds of the same tool is a loop, not a plan."""
    plan = await RulePlanner().deliberate(
        text="what time is it?",
        context=RecalledContext(),
        policy=TurnPolicy(),
        outcomes=(ToolOutcome(name="get_datetime", ok=True, output="12:00"),),
        iteration=1,
    )
    assert plan.intent is Intent.ANSWER


async def test_total_tool_failure_still_produces_an_answer() -> None:
    """A failed tool is not a failed turn (docs/07 §8)."""
    plan = await RulePlanner().deliberate(
        text="what time is it?",
        context=RecalledContext(),
        policy=TurnPolicy(),
        outcomes=(ToolOutcome(name="get_datetime", ok=False, error="broken"),),
        iteration=1,
    )
    assert plan.intent is Intent.ANSWER
    assert "broken" in plan.rationale
    assert plan.confidence < 0.5


async def test_recalled_context_raises_confidence() -> None:
    planner = RulePlanner()
    bare = await planner.deliberate(
        text="tell me about rust",
        context=RecalledContext(),
        policy=TurnPolicy(),
        outcomes=(),
        iteration=0,
    )
    informed = await planner.deliberate(
        text="tell me about rust",
        context=RecalledContext(items=(ContextItem(text="x", source="memory"),)),
        policy=TurnPolicy(),
        outcomes=(),
        iteration=0,
    )
    assert informed.confidence > bare.confidence


async def test_query_planning_deduplicates() -> None:
    queries = await RulePlanner().plan_queries("remember the compiler", policy=TurnPolicy())
    assert len(queries) == len(set(queries))
    assert queries[0] == "remember the compiler"


async def test_query_planning_of_an_empty_message_is_empty() -> None:
    assert await RulePlanner().plan_queries("  ", policy=TurnPolicy()) == ()


# =========================================================================
# Nodes, individually
# =========================================================================


async def test_a_node_is_callable_without_a_graph() -> None:
    """The payoff for nodes being adapters: they are plain async functions."""
    nodes = make_nodes(
        Collaborators(
            guard=PermissiveGuard(),
            mind=DefaultMindReader(),
            recaller=StubRecaller(),
            planner=RulePlanner(),
            responder=StubResponder(),
            tools=StubToolRunner(),
            conversation=InMemoryConversation(),
            announcer=RecordingAnnouncer(),
        )
    )
    state = new_turn(turn_id="t", session_id="s", correlation_id="c", text="hello")

    result = await nodes["guard"](state)

    assert result["verdict"].allowed is True
    assert result["visited"] == ["guard"]


async def test_the_snapshot_node_reads_cognition_once() -> None:
    """The turn keeps the reading it started with (docs/07 §4)."""
    nodes = make_nodes(
        Collaborators(
            guard=PermissiveGuard(),
            mind=DefaultMindReader(policy_value=TurnPolicy(token_budget=999)),
            recaller=StubRecaller(),
            planner=RulePlanner(),
            responder=StubResponder(),
            tools=StubToolRunner(),
            conversation=InMemoryConversation(),
            announcer=RecordingAnnouncer(),
        )
    )
    result = await nodes["snapshot"](
        new_turn(turn_id="t", session_id="s", correlation_id="c", text="x")
    )
    assert result["policy"].token_budget == 999


async def test_every_node_records_that_it_ran() -> None:
    """`visited` is the cheapest possible trace, and the graph tests depend on it."""
    deps = Collaborators(
        guard=PermissiveGuard(),
        mind=DefaultMindReader(),
        recaller=StubRecaller(),
        planner=RulePlanner(),
        responder=StubResponder(),
        tools=StubToolRunner(),
        conversation=InMemoryConversation(),
        announcer=RecordingAnnouncer(),
    )
    nodes = make_nodes(deps)
    state = new_turn(turn_id="t", session_id="s", correlation_id="c", text="hello")
    state["plan"] = Plan(intent=Intent.ANSWER)
    state["verdict"] = SafetyVerdict(allowed=True)

    for name, node in nodes.items():
        result = await node(state)
        assert result.get("visited") == [name], f"{name} did not record itself"


# =========================================================================
# The graph
# =========================================================================


async def test_a_plain_turn_follows_the_documented_path() -> None:
    final = await default_brain().run_turn("tell me something interesting")

    assert final["visited"] == [
        "ingest",
        "guard",
        "snapshot",
        "plan_queries",
        "recall",
        "deliberate",
        "compose",
        "express",
        "emit",
        "learn",
        "finalize",
    ]
    assert final["status"] == "completed"
    assert final["reply"]


async def test_a_tool_turn_loops_through_act_and_back() -> None:
    final = await default_brain().run_turn("what time is it?")

    visited = final["visited"]
    assert visited.count("deliberate") == 2
    assert "act" in visited
    assert visited.index("act") < visited.index("compose")
    assert len(final["outcomes"]) == 1
    assert final["status"] == "completed"


async def test_a_refused_turn_skips_the_whole_middle() -> None:
    brain = _brain(guard=PermissiveGuard(blocked_phrases=frozenset({"forbidden"})))

    final = await brain.run_turn("this is forbidden")

    assert final["visited"] == [
        "ingest",
        "guard",
        "refuse",
        "express",
        "emit",
        "learn",
        "finalize",
    ]
    assert final["status"] == "refused"
    assert "refused" in final["reply"]
    assert "snapshot" not in final["visited"]


async def test_a_tool_needing_approval_is_denied_and_composed_around() -> None:
    """Auto-denial is the safe default while there is no channel to ask on."""
    brain = _brain(tools=StubToolRunner(approval_required=frozenset({"get_datetime"})))

    final = await brain.run_turn("what time is it?")

    assert "approve" in final["visited"]
    assert "act" not in final["visited"]
    assert final["approved"] is False
    assert final["status"] == "completed"


async def test_a_failing_tool_does_not_fail_the_turn() -> None:
    brain = _brain(tools=StubToolRunner(failing=frozenset({"get_datetime"})))

    final = await brain.run_turn("what time is it?")

    assert final["status"] == "completed"
    assert final["outcomes"][0].ok is False
    assert final["reply"]


async def test_the_loop_is_bounded_and_says_so() -> None:
    """A cap degrades to a reply, never to an error (docs/07 §3.2)."""

    class AlwaysTool:
        async def deliberate(self, **kwargs: object) -> Plan:
            return Plan(intent=Intent.ACT, tool=ToolRequest(name="get_datetime"))

        async def plan_queries(self, text: str, **kwargs: object) -> tuple[str, ...]:
            return (text,)

    brain = _brain(planner=AlwaysTool(), caps=TurnCaps(max_tool_iterations=2))

    final = await brain.run_turn("loop forever please")

    assert final["status"] == "truncated"
    assert final["visited"].count("act") == 2
    assert final["truncation_reason"] is not None
    assert "I stopped early" in final["reply"]


async def test_the_graph_always_terminates() -> None:
    """Whatever the planner does, the turn ends."""

    class Adversarial:
        async def deliberate(self, **kwargs: object) -> Plan:
            return Plan(intent=Intent.ACT, tool=ToolRequest(name="calculate"))

        async def plan_queries(self, text: str, **kwargs: object) -> tuple[str, ...]:
            return (text,)

    for caps in (TurnCaps(max_tool_iterations=1), TurnCaps(max_tool_calls=2), TurnCaps()):
        final = await _brain(planner=Adversarial(), caps=caps).run_turn("go")
        assert final["visited"][-1] == "finalize"


async def test_recalled_context_reaches_the_reply() -> None:
    brain = _brain(
        recaller=StubRecaller(
            items=[ContextItem(text="the user likes Rust", source="memory", score=0.9)]
        )
    )

    final = await brain.run_turn("what do I like?")

    assert final["context"].items
    assert "context=1" in final["reply"]


async def test_the_policy_reaches_the_reply() -> None:
    brain = _brain(mind=DefaultMindReader(policy_value=TurnPolicy(style=("Be brief.",))))

    final = await brain.run_turn("hello")

    assert "Be brief." in final["reply"]


async def test_expression_intent_is_recorded_for_an_avatar_that_does_not_exist_yet() -> None:
    """Derived from state, never from the reply text (docs/15 §2)."""
    final = await default_brain().run_turn("hello")
    assert final["expression"].startswith("answer:")


async def test_turns_are_isolated_from_one_another() -> None:
    brain = default_brain()

    first = await brain.run_turn("what time is it?")
    second = await brain.run_turn("hello")

    assert len(first["outcomes"]) == 1
    assert len(second["outcomes"]) == 0  # no leakage through the checkpointer
    assert first["turn_id"] != second["turn_id"]


async def test_the_summary_is_safe_to_log() -> None:
    """Reply text never reaches the logs by default (docs/19 §4)."""
    final = await default_brain().run_turn("something private")
    summary = summarise(final)

    assert "something private" not in str(summary)
    assert summary["reply_chars"] > 0


# =========================================================================
# Honesty about being stubbed
# =========================================================================


async def test_every_stubbed_reply_is_marked_as_such() -> None:
    """A stub that looks like a working system is how a placeholder reaches production."""
    final = await default_brain().run_turn("hello")
    assert STUB_MARKER in final["reply"]


async def test_health_reports_which_capabilities_are_fake() -> None:
    from hedwig.core.ports import HealthStatus

    health = await default_brain().health()

    assert health.status is HealthStatus.DEGRADED
    assert set(health.detail["stubbed"]) == {
        "announcer",
        "conversation",
        "guard",
        "mind",
        "recaller",
        "responder",
        "tools",
    }
    assert health.detail["nodes"] == 14


async def test_a_fully_wired_brain_reports_ok() -> None:
    """The same check that flags stubs must clear once real services arrive."""
    from hedwig.core.ports import HealthStatus

    class Real:
        async def inspect(self, text: str) -> SafetyVerdict:
            return SafetyVerdict(allowed=True)

        async def snapshot(self) -> MindSnapshot:
            return MindSnapshot(policy=TurnPolicy(), mood={"happiness": 0.5})

        async def plan_queries(self, text: str, **kwargs: object) -> tuple[str, ...]:
            return (text,)

        async def recall(self, queries: object, **kwargs: object) -> RecalledContext:
            return RecalledContext()

        async def compose(self, **kwargs: object) -> str:
            return "a real reply"

        async def refuse(self, **kwargs: object) -> str:
            return "no"

        def requires_approval(self, name: str) -> bool:
            return False

        async def run(self, request: ToolRequest) -> ToolOutcome:
            return ToolOutcome(name=request.name, ok=True)

        async def open(self, session_id: str, **kwargs: object) -> bool:
            return True

        async def record_message(self, **kwargs: object) -> int:
            return 1

        async def window(self, session_id: str, **kwargs: object) -> tuple[()]:
            return ()

        async def record_turn(self, record: object) -> None:
            return None

        async def record_working_set(self, record: object) -> None:
            return None

        async def session_started(self, **kwargs: object) -> None:
            return None

        async def message_received(self, **kwargs: object) -> None:
            return None

        async def input_appraised(self, verdict: object) -> None:
            return None

        async def reply_produced(self, **kwargs: object) -> None:
            return None

        async def turn_completed(self, summary: object) -> None:
            return None

    real = Real()
    brain = Brain(
        Collaborators(
            guard=real,
            mind=real,
            recaller=real,
            planner=RulePlanner(),
            responder=real,
            tools=real,
            conversation=real,
            announcer=real,
        )
    )

    health = await brain.health()
    assert health.status is HealthStatus.OK

    final = await brain.run_turn("hello")
    assert STUB_MARKER not in final["reply"]
