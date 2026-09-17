"""Routing (docs/07 §3.2).

Every branch in the graph is a pure function of state, living here rather than inside a
node. Two reasons, both practical: a routing decision can then be tested exhaustively
without building a graph, and the whole control flow of a turn can be read in one file.

The caps are what make the graph **provably terminating**. A cognitive loop without them
eventually spends an afternoon thinking, and every cap degrades to a *reply* rather than to
an error — the user always gets something (docs/07 §3.2).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from hedwig.brain.state import TurnState, TurnStatus
from hedwig.core.ports.brain import Intent


@dataclass(frozen=True, slots=True)
class TurnCaps:
    """Hard limits on one turn."""

    max_tool_iterations: int = 4
    max_tool_calls: int = 6
    max_turn_seconds: float = 90.0
    max_turn_tokens: int = 8000

    def __post_init__(self) -> None:
        if self.max_tool_iterations < 1:
            raise ValueError("a turn must allow at least one deliberation round")


DEFAULT_CAPS = TurnCaps()


# -- branch functions ------------------------------------------------------


def after_guard(state: TurnState) -> Literal["snapshot", "refuse"]:
    """The only node that can divert a turn away from answering."""
    verdict = state.get("verdict")
    return "snapshot" if verdict is None or verdict.allowed else "refuse"


def after_deliberate(state: TurnState) -> Literal["compose", "approve", "act"]:
    """Tools, approval, or straight to a reply."""
    plan = state.get("plan")
    if plan is None or not plan.needs_tool:
        return "compose"
    if state.get("approved") is True:
        # Already approved this round; do not ask twice.
        return "act"
    return "approve" if _needs_approval(state) else "act"


def after_approve(state: TurnState) -> Literal["act", "compose"]:
    """A denied tool is not an error: compose around it."""
    return "act" if state.get("approved") else "compose"


def after_act(state: TurnState, caps: TurnCaps = DEFAULT_CAPS) -> Literal["deliberate", "compose"]:
    """Loop back to think again, unless a cap says stop."""
    return "compose" if exhausted(state, caps) else "deliberate"


# -- caps ------------------------------------------------------------------


def exhausted(state: TurnState, caps: TurnCaps = DEFAULT_CAPS) -> bool:
    return truncation_reason(state, caps) is not None


def truncation_reason(state: TurnState, caps: TurnCaps = DEFAULT_CAPS) -> str | None:
    """Which cap stopped the loop, or `None` if none did.

    Returned as a reason rather than a boolean so the reply can say what happened and the
    trace can show it.
    """
    if state.get("iteration", 0) >= caps.max_tool_iterations:
        return f"reached the {caps.max_tool_iterations}-round deliberation limit"

    if len(state.get("outcomes") or ()) >= caps.max_tool_calls:
        return f"used the {caps.max_tool_calls}-tool budget for this turn"

    metrics = state.get("metrics") or {}
    if metrics.get("elapsed_s", 0.0) >= caps.max_turn_seconds:
        return f"ran for {caps.max_turn_seconds:g}s"

    if metrics.get("tokens", 0.0) >= caps.max_turn_tokens:
        return f"used the {caps.max_turn_tokens}-token budget for this turn"

    return None


def _needs_approval(state: TurnState) -> bool:
    """Whether the pending tool call must be confirmed by the user.

    Read from state rather than decided here: the tool runner owns the policy, and a
    routing function that consulted a service would stop being pure.
    """
    return bool((state.get("metrics") or {}).get("approval_required"))


# -- introspection ---------------------------------------------------------


def terminal_status(state: TurnState) -> TurnStatus:
    """The status a finished turn should carry."""
    if state.get("status") == "failed":
        return "failed"
    if (intent := state.get("plan")) is not None and intent.intent is Intent.REFUSE:
        return "refused"
    if state.get("truncation_reason"):
        return "truncated"
    return "completed"
