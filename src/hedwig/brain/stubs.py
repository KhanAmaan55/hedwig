"""Stub capabilities for Milestone 4.

The graph is real; the things it orchestrates are not yet. These satisfy the ports in
`core/ports/brain.py` with deterministic, obviously-fake answers so the orchestration can
be built and tested before memory, models or an avatar exist.

**Every one of them is honest about being a stub.** `StubResponder` says so in the reply
text rather than producing plausible prose, because a stub that looks like a working system
is how a placeholder survives to production. When the real services land, `wiring.py`
swaps them and no node changes — that is the whole point of the seam (docs/07 §2).
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from hedwig.core.ports.brain import (
    ContextItem,
    Plan,
    RecalledContext,
    ResponseContext,
    SafetyVerdict,
    ToolOutcome,
    ToolRequest,
    TrustTier,
    TurnPolicy,
    TurnSummary,
)
from hedwig.core.ports.sessions import MessageRole, TurnRecord, WindowMessage, WorkingSetRecord
from hedwig.core.types import TrustTier as MemoryTrust

STUB_MARKER = "[stub]"
"""Present in every stubbed reply. A test asserts no real path can emit it (see
tests/unit/test_brain_graph.py), so a forgotten stub cannot reach a user quietly."""


@dataclass(frozen=True, slots=True)
class PermissiveGuard:
    """Allows everything, and marks it as user-trusted.

    The real guard classifies trust tier, scans for injection, and can refuse
    (docs/13 §6). This one exists so the `refuse` branch has something to be routed by;
    `blocked_phrases` lets a test drive that branch without a real classifier.
    """

    blocked_phrases: frozenset[str] = frozenset()

    async def inspect(self, text: str) -> SafetyVerdict:
        lowered = text.lower()
        for phrase in self.blocked_phrases:
            if phrase in lowered:
                return SafetyVerdict(
                    allowed=False,
                    trust=TrustTier.USER,
                    flags=("blocked_phrase",),
                    reason=f"input contains {phrase!r}",
                )
        return SafetyVerdict(allowed=True, trust=TrustTier.USER)


@dataclass(frozen=True, slots=True)
class DefaultMindReader:
    """Returns a fixed policy.

    Emotion and personality will produce this (docs/09 §6, docs/10 §4). Until then the
    defaults are the documented ones, so the numbers a node sees now are the numbers it
    will see later.
    """

    policy_value: TurnPolicy = field(default_factory=TurnPolicy)

    async def policy(self) -> TurnPolicy:
        return self.policy_value


@dataclass(slots=True)
class StubRecaller:
    """Returns whatever it was told to, or nothing.

    Nothing by default: a brain that hallucinates recalled context while memory does not
    exist would make the graph tests meaningless.
    """

    items: list[ContextItem] = field(default_factory=list)
    queries_seen: list[list[str]] = field(default_factory=list)

    async def recall(
        self, queries: Sequence[str], *, policy: TurnPolicy, turn_id: str | None = None
    ) -> RecalledContext:
        self.queries_seen.append(list(queries))
        kept = self.items[: max(0, policy.token_budget // 100)] if self.items else []
        return RecalledContext(
            items=tuple(kept),
            token_count=sum(len(item.text) // 4 for item in kept),
            dropped=len(self.items) - len(kept),
        )


@dataclass(slots=True)
class StubResponder:
    """Produces an obviously-fake reply that still reflects the turn.

    It reports what the graph decided — intent, tool count, context size — which makes it
    useful for verifying orchestration and useless for pretending to be a companion.
    """

    async def compose(
        self,
        *,
        text: str,
        plan: Plan,
        context: ResponseContext,
        policy: TurnPolicy,
    ) -> str:
        outcomes = context.outcomes
        parts = [
            f"{STUB_MARKER} intent={plan.intent.value}",
            f"context={len(context.recalled)}",
            f"window={len(context.window)}",
            f"tools={len(outcomes)}",
        ]
        if context.has_untrusted:
            parts.append("untrusted=yes")
        if outcomes:
            parts.append("results=" + "; ".join(o.output or o.error or "" for o in outcomes))
        if policy.style:
            parts.append("style=" + ",".join(policy.style))
        return " ".join(parts)

    async def refuse(self, *, verdict: SafetyVerdict) -> str:
        return f"{STUB_MARKER} refused: {verdict.reason or 'not permitted'}"


@dataclass(slots=True)
class StubToolRunner:
    """Answers a small fixed set of tools, and fails loudly for anything else.

    `approval_required` drives the human-in-the-loop branch; `failing` drives the
    tool-failure branch. Both exist so the graph's error paths are exercised rather than
    assumed.
    """

    approval_required: frozenset[str] = frozenset()
    failing: frozenset[str] = frozenset()
    calls: list[ToolRequest] = field(default_factory=list)

    def requires_approval(self, name: str) -> bool:
        return name in self.approval_required

    async def run(self, request: ToolRequest) -> ToolOutcome:
        started = time.monotonic()
        self.calls.append(request)

        if request.name in self.failing:
            return ToolOutcome(
                name=request.name,
                ok=False,
                error="stub tool failure",
                duration_ms=round((time.monotonic() - started) * 1000, 3),
            )

        return ToolOutcome(
            name=request.name,
            ok=True,
            output=f"{STUB_MARKER} {request.name}({_render(request)})",
            duration_ms=round((time.monotonic() - started) * 1000, 3),
        )


@dataclass(slots=True)
class InMemoryConversation:
    """A conversation log that forgets when the process does.

    The real one is `sessions.SqliteSessionStore`. This exists so a node test can run a
    turn without a database, and so `default_brain()` stays constructible with no
    dependencies at all. Its being in-memory is the whole tell: a turn run against it
    leaves no trace, which is exactly what Milestone 6 was about fixing.
    """

    sessions: dict[str, str] = field(default_factory=dict)
    messages: list[tuple[str, str, str]] = field(default_factory=list)
    turns: list[TurnRecord] = field(default_factory=list)
    working_sets: list[WorkingSetRecord] = field(default_factory=list)
    size: int = 8

    async def open(self, session_id: str, *, channel: str = "api") -> bool:
        created = session_id not in self.sessions
        self.sessions[session_id] = channel
        return created

    async def record_message(
        self,
        *,
        session_id: str,
        message_id: str,
        role: MessageRole,
        text: str,
        trust: MemoryTrust = MemoryTrust.USER,
        meta: Mapping[str, Any] | None = None,
    ) -> int:
        self.messages.append((session_id, role.value, text))
        return sum(1 for entry in self.messages if entry[0] == session_id)

    async def window(self, session_id: str, *, limit: int | None = None) -> Sequence[WindowMessage]:
        rows = [entry for entry in self.messages if entry[0] == session_id]
        return tuple(
            WindowMessage(role=role, text=text, at="")
            for _, role, text in rows[-(limit or self.size) :]
        )

    async def record_turn(self, record: TurnRecord) -> None:
        self.turns = [turn for turn in self.turns if turn.id != record.id]
        self.turns.append(record)

    async def record_working_set(self, record: WorkingSetRecord) -> None:
        self.working_sets.append(record)


@dataclass(slots=True)
class RecordingAnnouncer:
    """Records announcements instead of publishing them.

    Nothing subscribes in a node test, so a real bus would only add a drain to every
    assertion. The list it keeps is also the cheapest possible assertion that a node
    announced what it was supposed to.
    """

    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def session_started(self, *, session_id: str, channel: str) -> None:
        self.events.append(("session_started", {"session_id": session_id, "channel": channel}))

    async def message_received(
        self, *, session_id: str, message_id: str, text: str, trust: str
    ) -> None:
        self.events.append(
            ("message_received", {"session_id": session_id, "message_id": message_id, "text": text})
        )

    async def reply_produced(
        self, *, session_id: str, message_id: str, text: str, working_set_ref: str | None
    ) -> None:
        self.events.append(
            ("reply_produced", {"session_id": session_id, "message_id": message_id, "text": text})
        )

    async def turn_completed(self, summary: TurnSummary) -> None:
        self.events.append(("turn_completed", {"turn_id": summary.turn_id, "summary": summary}))

    def types(self) -> list[str]:
        return [name for name, _ in self.events]


def _render(request: ToolRequest) -> str:
    return ", ".join(f"{key}={value!r}" for key, value in sorted(request.arguments.items()))
