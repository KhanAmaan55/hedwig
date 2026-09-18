"""Turn state (docs/07 §4).

One `TypedDict` threaded through the graph, checkpointed at every node boundary.

Four rules, each preventing a specific bug:

1. **Only accumulating fields get reducers.** Everything else is last-write-wins, which is
   safe only because no two nodes write the same field — asserted by
   `tests/unit/test_brain_state.py::test_no_two_nodes_write_the_same_field`.
2. **`policy` is immutable for the whole turn.** Cognition may change underneath us;
   this turn keeps the reading it started with, so a reply cannot change tone halfway
   through (docs/07 §4 rule 2).
3. **Nothing here holds a port, a connection, or a callable.** State is checkpointed, so it
   must be JSON-serialisable; dependencies are closed over at graph-construction time.
4. **State is turn-scoped.** Nothing durable lives here (docs/08 tier 1).
"""

from __future__ import annotations

import operator
from collections.abc import Sequence
from typing import Annotated, Any, Literal, TypedDict

from hedwig.core.ports.brain import (
    Intent,
    MindSnapshot,
    Plan,
    RecalledContext,
    ResponseContext,
    SafetyVerdict,
    ToolOutcome,
    ToolRequest,
    TurnPolicy,
)
from hedwig.core.ports.sessions import WindowMessage

TurnStatus = Literal["running", "completed", "refused", "failed", "truncated"]


def merge_metrics(left: dict[str, float], right: dict[str, float]) -> dict[str, float]:
    """Later values win, but nothing is dropped.

    A reducer rather than last-write-wins because several nodes contribute timings and
    counts, and losing one would silently break the trace.
    """
    return {**left, **right}


class TurnState(TypedDict, total=False):
    """Everything one turn carries."""

    # -- identity ----------------------------------------------------------
    turn_id: str
    session_id: str
    correlation_id: str
    user_message_id: str
    reply_message_id: str
    """Minted at `ingest` and written at `finalize`, so `emit` can announce an id that the
    reply will actually be stored under (docs/26 §8)."""
    working_set_id: str

    # -- input -------------------------------------------------------------
    input: str
    verdict: SafetyVerdict
    safety_flags: Annotated[list[str], operator.add]

    # -- cognition (read once, never mutated within the turn) --------------
    mind: MindSnapshot
    """The mood the turn is running under, and what it means. Immutable for the whole turn
    (docs/07 §4 rule 2, §14.2)."""
    policy: TurnPolicy
    """`mind.policy`, unpacked once so later nodes read a policy rather than reaching
    through a snapshot for one."""

    # -- recall ------------------------------------------------------------
    queries: list[str]
    context: RecalledContext
    window: list[WindowMessage]
    """The current conversation, verbatim. Separate from `context` because it is never
    charged to the retrieval budget (docs/06 §5.1)."""

    # -- composition -------------------------------------------------------
    response_context: ResponseContext
    """What the responder was shown: window + memories + tool results, ordered and
    trust-labelled (docs/26 §5)."""

    # -- deliberation ------------------------------------------------------
    plan: Plan
    iteration: int
    tool_request: ToolRequest | None
    approved: bool | None
    outcomes: Annotated[list[ToolOutcome], operator.add]

    # -- output ------------------------------------------------------------
    reply: str
    expression: str
    """Semantic expression intent. Consumed by the avatar in Phase 6; recorded now so the
    graph shape does not change when it arrives (docs/15 §3)."""

    # -- bookkeeping -------------------------------------------------------
    status: TurnStatus
    truncation_reason: str | None
    visited: Annotated[list[str], operator.add]
    """Node names in execution order. The cheapest possible trace, and what the graph
    tests assert against."""
    metrics: Annotated[dict[str, float], merge_metrics]


def new_turn(*, turn_id: str, session_id: str, correlation_id: str, text: str) -> TurnState:
    """The initial state for a turn. The only place defaults are set."""
    return TurnState(
        turn_id=turn_id,
        session_id=session_id,
        correlation_id=correlation_id,
        input=text,
        iteration=0,
        status="running",
        safety_flags=[],
        outcomes=[],
        visited=[],
        metrics={},
        queries=[],
        window=[],
        reply="",
        truncation_reason=None,
        tool_request=None,
        approved=None,
    )


def intent_of(state: TurnState) -> Intent | None:
    plan = state.get("plan")
    return plan.intent if plan is not None else None


def outcomes_of(state: TurnState) -> Sequence[ToolOutcome]:
    return tuple(state.get("outcomes") or ())


def summarise(state: TurnState) -> dict[str, Any]:
    """A compact view for logs and tests. Never the reply text itself (docs/19 §4)."""
    plan = state.get("plan")
    mind = state.get("mind")
    return {
        "turn_id": state.get("turn_id"),
        "valence": round(mind.valence, 3) if mind else None,
        "status": state.get("status"),
        "intent": plan.intent.value if plan else None,
        "iterations": state.get("iteration", 0),
        "tools": len(state.get("outcomes") or []),
        "context_items": len((state.get("context") or RecalledContext()).items),
        "window": len(state.get("window") or []),
        "visited": list(state.get("visited") or []),
        "reply_chars": len(state.get("reply") or ""),
        "truncation_reason": state.get("truncation_reason"),
    }
