"""Graph nodes (docs/07 §3.1).

**A node reads state, calls one collaborator, and writes state.** No business logic, no
branching beyond what routing decides, nothing over twenty lines. That discipline is not
tidiness: it is what keeps the cognitive system independent of LangGraph, so replacing the
framework rewrites this file and nothing else (ADR-0004).

The anti-pattern to reject in review is a node that imports two collaborators *and*
contains an `if` about cognitive state. That node has become the system.

Nodes are built by `make_nodes`, which closes over the collaborators. They are therefore
plain async functions of state — trivially callable in a test without a graph.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from hedwig.brain.context import assemble_context
from hedwig.brain.routing import DEFAULT_CAPS, TurnCaps, terminal_status, truncation_reason
from hedwig.brain.state import TurnState, outcomes_of
from hedwig.core.ids import new_id
from hedwig.core.logging import fields, get_logger
from hedwig.core.ports.brain import (
    Guard,
    Intent,
    MindReader,
    Plan,
    Planner,
    RecalledContext,
    Recaller,
    Responder,
    ResponseContext,
    ToolRunner,
    TurnAnnouncer,
    TurnPolicy,
    TurnSummary,
)
from hedwig.core.ports.sessions import (
    Conversation,
    MessageRole,
    TurnRecord,
    WorkingSetRecord,
)
from hedwig.core.types import TrustTier

logger = get_logger(__name__)

Node = Callable[[TurnState], Awaitable[TurnState]]


@dataclass(frozen=True, slots=True)
class Collaborators:
    """Everything the nodes call. Injected once, at graph construction."""

    guard: Guard
    mind: MindReader
    recaller: Recaller
    planner: Planner
    responder: Responder
    tools: ToolRunner
    conversation: Conversation
    announcer: TurnAnnouncer
    caps: TurnCaps = DEFAULT_CAPS


def make_nodes(deps: Collaborators) -> dict[str, Node]:
    """Build the node table. The graph wires these by name."""

    async def ingest(state: TurnState) -> TurnState:
        """**Observe.** Open the session, record what was said, announce it.

        The only node that mints identifiers — including two for rows written later, so
        that `emit` can announce an id `finalize` will actually store the reply under
        (docs/26 §8).

        The user message is written *here*, at the start of the turn, rather than at the
        end: the message log is the authoritative record from which any lost extraction can
        be re-derived (docs/05 §7), and a record that only exists if the turn succeeds is
        not a record.
        """
        turn_id = state.get("turn_id") or new_id("turn")
        session_id = state.get("session_id") or "session_local"
        user_message_id = state.get("user_message_id") or new_id("msg")
        text = state.get("input", "")

        created = await deps.conversation.open(session_id)
        await deps.conversation.record_message(
            session_id=session_id,
            message_id=user_message_id,
            role=MessageRole.USER,
            text=text,
            trust=TrustTier.USER,
        )

        if created:
            await deps.announcer.session_started(session_id=session_id, channel="api")
        await deps.announcer.message_received(
            session_id=session_id,
            message_id=user_message_id,
            text=text,
            trust=TrustTier.USER.value,
        )

        return TurnState(
            turn_id=turn_id,
            session_id=session_id,
            correlation_id=state.get("correlation_id") or turn_id,
            user_message_id=user_message_id,
            reply_message_id=state.get("reply_message_id") or new_id("msg"),
            working_set_id=state.get("working_set_id") or new_id("ws"),
            visited=["ingest"],
            metrics={"started_monotonic": time.monotonic()},
        )

    async def guard(state: TurnState) -> TurnState:
        """Classify the input. The only node that can divert the turn (docs/07 §3.2)."""
        verdict = await deps.guard.inspect(state.get("input", ""))
        return TurnState(
            verdict=verdict,
            safety_flags=list(verdict.flags),
            visited=["guard"],
        )

    async def snapshot(state: TurnState) -> TurnState:
        """Read cognitive state **once**, so the turn is internally consistent.

        Emotion may change mid-flight; this turn keeps the reading it started with, which
        removes a whole class of "why did the tone shift halfway through?" (docs/07 §4).
        """
        return TurnState(policy=await deps.mind.policy(), visited=["snapshot"])

    async def plan_queries(state: TurnState) -> TurnState:
        policy = state.get("policy") or TurnPolicy()
        queries = await deps.planner.plan_queries(state.get("input", ""), policy=policy)
        return TurnState(queries=list(queries), visited=["plan_queries"])

    async def recall(state: TurnState) -> TurnState:
        """**Retrieve and inject.** Long-term memory and the current conversation.

        Two sources, deliberately kept apart all the way into state: retrieval is scored,
        budgeted and may return nothing; the window is verbatim, always present, and never
        charged to the budget (docs/06 §5.1).
        """
        policy = state.get("policy") or TurnPolicy()
        context = await deps.recaller.recall(
            state.get("queries") or [], policy=policy, turn_id=state.get("turn_id")
        )
        window = await deps.conversation.window(state.get("session_id") or "")
        return TurnState(
            context=context,
            window=list(window),
            visited=["recall"],
            metrics={
                "context_items": float(len(context.items)),
                "dropped": float(context.dropped),
                "window_messages": float(len(window)),
            },
        )

    async def deliberate(state: TurnState) -> TurnState:
        """Decide what to do this round. One planner call, no interpretation."""
        policy = state.get("policy") or TurnPolicy()
        plan = await deps.planner.deliberate(
            text=state.get("input", ""),
            context=state.get("context") or RecalledContext(),
            policy=policy,
            outcomes=outcomes_of(state),
            iteration=state.get("iteration", 0),
        )
        approval = bool(plan.tool and deps.tools.requires_approval(plan.tool.name))
        return TurnState(
            plan=plan,
            tool_request=plan.tool,
            approved=None,
            iteration=state.get("iteration", 0) + 1,
            visited=["deliberate"],
            metrics={"approval_required": float(approval)},
        )

    async def approve(state: TurnState) -> TurnState:
        """Pause for a human decision.

        Milestone 4 auto-denies: there is no channel to ask on yet, and a graph that
        silently auto-*approves* side effects would be a bad default to discover later.
        The interrupt that replaces this is a one-line change here (docs/07 §7).
        """
        request = state.get("tool_request")
        logger.info(
            "tool approval required",
            extra=fields(tool=request.name if request else None, decision="denied (no channel)"),
        )
        return TurnState(approved=False, visited=["approve"])

    async def act(state: TurnState) -> TurnState:
        """Run one tool. Failures come back as outcomes, never as exceptions."""
        request = state.get("tool_request")
        if request is None:  # pragma: no cover - routing guarantees a request
            return TurnState(visited=["act"])

        outcome = await deps.tools.run(request)
        return TurnState(
            outcomes=[outcome],
            tool_request=None,
            visited=["act"],
            metrics={"tool_ms": outcome.duration_ms},
        )

    async def compose(state: TurnState) -> TurnState:
        """**Generate the response context**, then the reply.

        The assembly is a pure function in `context.py`, not logic here: ordering, trust
        labelling and token accounting are decisions that belong to a module, and a node
        that made them would have started becoming the system (docs/07 §2, docs/26 §5).
        """
        policy = state.get("policy") or TurnPolicy()
        plan = state.get("plan") or Plan(intent=Intent.ANSWER)
        response_context = assemble_context(
            window=state.get("window") or [],
            recalled=state.get("context") or RecalledContext(),
            outcomes=outcomes_of(state),
        )
        reply = await deps.responder.compose(
            text=state.get("input", ""),
            plan=plan,
            context=response_context,
            policy=policy,
        )
        reason = truncation_reason(state, deps.caps)
        if reason:
            # A cap degrades to a reply that says so, never to an error (docs/07 §3.2).
            reply = f"{reply}\n\n(I stopped early: I {reason}.)"
        return TurnState(
            reply=reply,
            response_context=response_context,
            truncation_reason=reason,
            visited=["compose"],
            metrics={"context_tokens": float(response_context.token_count)},
        )

    async def refuse(state: TurnState) -> TurnState:
        verdict = state.get("verdict")
        reply = await deps.responder.refuse(verdict=verdict) if verdict else ""
        return TurnState(
            reply=reply,
            plan=Plan(intent=Intent.REFUSE, rationale=verdict.reason if verdict else ""),
            visited=["refuse"],
        )

    async def express(state: TurnState) -> TurnState:
        """Record expression intent.

        Semantic, never geometric, and derived from state rather than from the reply text
        (docs/15 §2). Nothing consumes it until the avatar exists; recording it now means
        the graph shape does not change when it does.
        """
        plan = state.get("plan")
        intent = plan.intent.value if plan else "answer"
        return TurnState(expression=f"{intent}:neutral", visited=["express"])

    async def emit(state: TurnState) -> TurnState:
        """The point at which the reply is considered delivered."""
        await deps.announcer.reply_produced(
            session_id=state.get("session_id") or "",
            message_id=state.get("reply_message_id") or "",
            text=state.get("reply") or "",
            working_set_ref=state.get("working_set_id") or None,
        )
        return TurnState(visited=["emit"])

    async def learn(state: TurnState) -> TurnState:
        """**Store new memory** — by announcing, not by storing.

        Capture is a *subscriber*, not a node: it can take its time, and the graph stays
        short (docs/07 §3.1). The summary carries the turn's substance so that capture
        never has to read back a row `finalize` has not written yet (ADR-0018).
        """
        context = state.get("response_context") or ResponseContext()
        plan = state.get("plan")
        started = (state.get("metrics") or {}).get("started_monotonic", time.monotonic())

        await deps.announcer.turn_completed(
            TurnSummary(
                turn_id=state.get("turn_id") or "",
                session_id=state.get("session_id") or "",
                correlation_id=state.get("correlation_id") or "",
                status=terminal_status(state),
                input=state.get("input", ""),
                reply=state.get("reply") or "",
                intent=plan.intent.value if plan else "",
                recalled_memory_ids=context.cited_memory_ids,
                tool_calls=len(outcomes_of(state)),
                latency_ms=(time.monotonic() - started) * 1000,
            )
        )
        return TurnState(visited=["learn"])

    async def finalize(state: TurnState) -> TurnState:
        """**Persist.** Close the turn. The one node whose write must not be lost."""
        started = (state.get("metrics") or {}).get("started_monotonic", time.monotonic())
        elapsed = time.monotonic() - started

        # The elapsed time has to be visible to `terminal_status`, which decides whether
        # this turn was truncated by the wall-clock cap.
        timed: TurnState = dict(state)  # type: ignore[assignment]
        timed["metrics"] = {**(state.get("metrics") or {}), "elapsed_s": elapsed}
        status = terminal_status(timed)

        persisted = await _persist(deps, timed, status=status, elapsed=elapsed)

        return TurnState(
            status=status,
            visited=["finalize"],
            metrics={"elapsed_s": elapsed, "persisted": float(persisted)},
        )

    return {
        "ingest": ingest,
        "guard": guard,
        "snapshot": snapshot,
        "plan_queries": plan_queries,
        "recall": recall,
        "deliberate": deliberate,
        "approve": approve,
        "act": act,
        "compose": compose,
        "refuse": refuse,
        "express": express,
        "emit": emit,
        "learn": learn,
        "finalize": finalize,
    }


async def _persist(deps: Collaborators, state: TurnState, *, status: str, elapsed: float) -> bool:
    """Write the durable trace of a turn: the reply, its retrieval, and the turn row.

    Kept out of `finalize` so the node stays a node. It is the only place in the graph
    where a failure is *reported* rather than raised: the reply has already reached the
    user, so failing the turn now would help nobody. It is logged at error with everything
    needed to reconstruct the write, and `persisted=0` appears in the metrics.

    docs/07 §8 additionally specifies a durable spool file here. That is deliberately not
    built yet — see docs/26 §11 for the trigger.
    """
    session_id = state.get("session_id") or ""
    turn_id = state.get("turn_id") or ""
    reply = state.get("reply") or ""
    reply_message_id = state.get("reply_message_id") or ""
    working_set_id = state.get("working_set_id") or ""
    context = state.get("response_context") or ResponseContext()

    try:
        if reply and reply_message_id:
            await deps.conversation.record_message(
                session_id=session_id,
                message_id=reply_message_id,
                role=MessageRole.HEDWIG,
                text=reply,
                trust=TrustTier.SELF,
                meta={"status": status, "intent": _intent_of(state)},
            )

        # Recorded whenever retrieval ran, including when it found nothing: "we searched
        # and there was nothing" is as much of an explanation as a list of hits.
        searched = bool(context.recalled or state.get("queries"))
        if searched:
            await deps.conversation.record_working_set(
                WorkingSetRecord(
                    id=working_set_id,
                    turn_id=turn_id,
                    queries=tuple(state.get("queries") or ()),
                    policy=_policy_of(state),
                    items=tuple(
                        {
                            "memory_id": item.memory_id,
                            "score": item.score,
                            "trust": item.trust.value,
                            "source": item.source,
                        }
                        for item in context.recalled
                    ),
                    token_count=context.token_count,
                    dropped=context.dropped,
                )
            )

        await deps.conversation.record_turn(
            TurnRecord(
                id=turn_id,
                session_id=session_id,
                correlation_id=state.get("correlation_id") or turn_id,
                status=status,
                user_message_id=state.get("user_message_id") or None,
                reply_message_id=reply_message_id if reply else None,
                latency_ms=int(elapsed * 1000),
                working_set_id=working_set_id if searched else None,
            )
        )
    except Exception as error:  # a turn whose reply is delivered must not fail here
        logger.error(
            "could not persist turn",
            extra=fields(
                turn=turn_id,
                session=session_id,
                status=status,
                error=str(error),
                reply_chars=len(reply),
            ),
        )
        return False
    return True


def _intent_of(state: TurnState) -> str:
    plan = state.get("plan")
    return plan.intent.value if plan else ""


def _policy_of(state: TurnState) -> dict[str, float]:
    """The retrieval-relevant part of the turn policy, for the explanation log."""
    policy = state.get("policy") or TurnPolicy()
    return {"token_budget": float(policy.token_budget), "diversity": policy.diversity}
