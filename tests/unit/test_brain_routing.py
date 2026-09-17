"""Routing and caps (docs/07 §3.2).

Routing lives in pure functions precisely so it can be tested like this: exhaustively, in
microseconds, without building a graph. If these ever need a graph to test, the logic has
leaked into a node.
"""

from __future__ import annotations

import pytest

from hedwig.brain.routing import (
    DEFAULT_CAPS,
    TurnCaps,
    after_act,
    after_approve,
    after_deliberate,
    after_guard,
    exhausted,
    terminal_status,
    truncation_reason,
)
from hedwig.brain.state import TurnState, new_turn
from hedwig.core.ports.brain import Intent, Plan, SafetyVerdict, ToolOutcome, ToolRequest


def _state(**overrides: object) -> TurnState:
    state = new_turn(turn_id="turn_1", session_id="s", correlation_id="c", text="hello")
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


# -- guard -----------------------------------------------------------------


def test_an_allowed_input_proceeds() -> None:
    assert after_guard(_state(verdict=SafetyVerdict(allowed=True))) == "snapshot"


def test_a_disallowed_input_is_refused() -> None:
    """The only node that can divert a turn away from answering."""
    assert after_guard(_state(verdict=SafetyVerdict(allowed=False))) == "refuse"


def test_a_missing_verdict_proceeds() -> None:
    """Absence of a verdict is not a refusal; the guard simply had nothing to say."""
    assert after_guard(_state()) == "snapshot"


# -- deliberate ------------------------------------------------------------


def test_a_plan_without_a_tool_composes() -> None:
    assert after_deliberate(_state(plan=Plan(intent=Intent.ANSWER))) == "compose"


def test_a_clarify_plan_composes() -> None:
    """Asking a question is still a reply."""
    assert after_deliberate(_state(plan=Plan(intent=Intent.CLARIFY))) == "compose"


def test_a_tool_plan_acts_when_no_approval_is_needed() -> None:
    plan = Plan(intent=Intent.ACT, tool=ToolRequest(name="get_datetime"))
    assert after_deliberate(_state(plan=plan, metrics={})) == "act"


def test_a_tool_plan_seeks_approval_when_required() -> None:
    plan = Plan(intent=Intent.ACT, tool=ToolRequest(name="write_file"))
    state = _state(plan=plan, metrics={"approval_required": 1.0})
    assert after_deliberate(state) == "approve"


def test_an_already_approved_tool_is_not_asked_about_twice() -> None:
    plan = Plan(intent=Intent.ACT, tool=ToolRequest(name="write_file"))
    state = _state(plan=plan, approved=True, metrics={"approval_required": 1.0})
    assert after_deliberate(state) == "act"


def test_an_act_intent_without_a_tool_composes() -> None:
    """`needs_tool` requires both; an intent alone is not a request."""
    assert after_deliberate(_state(plan=Plan(intent=Intent.ACT))) == "compose"


def test_a_missing_plan_composes() -> None:
    assert after_deliberate(_state()) == "compose"


# -- approve ---------------------------------------------------------------


def test_approval_leads_to_the_tool() -> None:
    assert after_approve(_state(approved=True)) == "act"


def test_denial_composes_rather_than_failing() -> None:
    """A denied tool is not an error: compose around it (docs/07 §8)."""
    assert after_approve(_state(approved=False)) == "compose"


# -- the loop and its caps -------------------------------------------------


def test_the_loop_continues_while_budget_remains() -> None:
    assert after_act(_state(iteration=1)) == "deliberate"


def test_the_iteration_cap_stops_the_loop() -> None:
    state = _state(iteration=DEFAULT_CAPS.max_tool_iterations)
    assert after_act(state) == "compose"
    assert exhausted(state) is True


def test_the_tool_call_cap_stops_the_loop() -> None:
    outcomes = [ToolOutcome(name="t", ok=True) for _ in range(DEFAULT_CAPS.max_tool_calls)]
    assert after_act(_state(iteration=1, outcomes=outcomes)) == "compose"


def test_the_wall_clock_cap_stops_the_loop() -> None:
    state = _state(iteration=1, metrics={"elapsed_s": DEFAULT_CAPS.max_turn_seconds})
    assert after_act(state) == "compose"


def test_the_token_cap_stops_the_loop() -> None:
    state = _state(iteration=1, metrics={"tokens": DEFAULT_CAPS.max_turn_tokens})
    assert after_act(state) == "compose"


def test_the_reason_names_the_cap_that_fired() -> None:
    """A cap degrades to a reply that can say what happened, not to a bare boolean."""
    reason = truncation_reason(_state(iteration=99))
    assert reason is not None
    assert "deliberation limit" in reason


def test_no_cap_means_no_reason() -> None:
    assert truncation_reason(_state(iteration=0)) is None


def test_caps_are_configurable() -> None:
    tight = TurnCaps(max_tool_iterations=1)
    assert after_act(_state(iteration=1), tight) == "compose"
    assert after_act(_state(iteration=1)) == "deliberate"  # default is looser


def test_a_turn_must_allow_at_least_one_round() -> None:
    with pytest.raises(ValueError, match="at least one"):
        TurnCaps(max_tool_iterations=0)


# -- terminal status -------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ({}, "completed"),
        ({"status": "failed"}, "failed"),
        ({"plan": Plan(intent=Intent.REFUSE)}, "refused"),
        ({"truncation_reason": "ran out of rounds"}, "truncated"),
    ],
)
def test_terminal_status(state: dict[str, object], expected: str) -> None:
    assert terminal_status(_state(**state)) == expected
