"""The brain's collaborator ports (docs/07).

The turn graph sequences work; these are the things it sequences. Each is deliberately
narrower than the full service that will eventually satisfy it, because the brain only
needs a slice: `Recaller` is what the retrieval engine will look like *to a node*, not the
whole of `RetrievalEngine` (docs/03 §5.2).

That narrowness is the point. A node written against `Recaller` does not change when the
memory subsystem lands — an adapter appears in `wiring.py` and the stub goes away. It is
also what lets Milestone 4 build the orchestration for real while every capability behind
it is still a stub.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from hedwig.core.ports.sessions import WindowMessage


class Intent(StrEnum):
    """What the planner decided this turn is."""

    ANSWER = "answer"
    """Reply from what is already known."""
    ACT = "act"
    """A tool is needed before answering."""
    CLARIFY = "clarify"
    """The request is ambiguous; ask rather than guess."""
    REFUSE = "refuse"
    """The guard or the planner declined."""


class TrustTier(StrEnum):
    """Whether content may be treated as instruction (docs/13 §3).

    Only `USER` may instruct. The full five-tier model arrives with the knowledge layer;
    the brain needs the distinction now because the guard node assigns it.
    """

    USER = "user"
    SELF = "self"
    TOOL = "tool"
    UNTRUSTED = "untrusted"


@dataclass(frozen=True, slots=True)
class SafetyVerdict:
    """The guard's reading of an input."""

    allowed: bool
    trust: TrustTier = TrustTier.USER
    flags: tuple[str, ...] = ()
    injection_score: float = 0.0
    reason: str = ""


@dataclass(frozen=True, slots=True)
class TurnPolicy:
    """How this turn should be conducted.

    The only channel by which cognition influences the turn (docs/02 §3). Emotion and
    personality will produce it; until they exist a stub returns defaults, and no node
    knows the difference.
    """

    token_budget: int = 3000
    max_tokens: int = 1024
    temperature: float = 0.7
    diversity: float = 0.3
    style: tuple[str, ...] = ()
    """Rendered behavioural directives, e.g. "Be concise." (docs/10 §4)."""
    allow_tools: bool = True


@dataclass(frozen=True, slots=True)
class MindSnapshot:
    """Cognitive state, read once at the start of a turn (docs/07 §4, §14.2).

    Both what the mood *is* and what it *means*. The policy alone was not enough: a turn
    that receives only the consequences of a mood can act on it but cannot report it, record
    it, or explain it afterwards — and explainability is a product feature here (ADR-0014).

    Immutable for the whole turn. Emotion may move underneath; this turn keeps the reading
    it started with, which is what stops a reply changing tone halfway through.
    """

    policy: TurnPolicy = field(default_factory=lambda: TurnPolicy())
    mood: Mapping[str, float] = field(default_factory=dict)
    """The dimensions as read. Empty when no emotion engine is wired, which is a legitimate
    configuration rather than a degraded one."""
    valence: float = 0.0
    arousal: float = 0.0
    directives: tuple[str, ...] = ()
    """The behavioural directives, verbatim. Produced by emotion, carried by the brain,
    rendered by the responder — three modules, one string, each doing what it owns."""
    reference: str | None = None
    """The `emotion_history` row this reading came from, so the reply can be joined back to
    the mood that produced it (docs/05 §5.1, `message.emotion_ref`)."""

    @property
    def is_flat(self) -> bool:
        """Whether there is any cognitive state behind this at all."""
        return not self.mood


@dataclass(frozen=True, slots=True)
class ContextItem:
    """One piece of recalled material."""

    text: str
    source: str
    score: float = 0.0
    trust: TrustTier = TrustTier.SELF
    memory_id: str = ""
    """Which memory this is. Carried so that what was shown to the model can be reinforced
    afterwards (docs/06 §7.2) and so `/v1/explain` can walk back to the row."""


@dataclass(frozen=True, slots=True)
class RecalledContext:
    """What recall assembled for this turn. Ephemeral (docs/06 §2)."""

    items: tuple[ContextItem, ...] = ()
    token_count: int = 0
    dropped: int = 0
    """Candidates the budget excluded. Counted so starvation is visible."""


@dataclass(frozen=True, slots=True)
class ResponseContext:
    """Everything the responder is allowed to see, ordered and accounted for (docs/26 §5).

    Structure, not prose. docs/07 §1 says the brain owns no prompt content, and that rule
    survives this stage: rendering these parts into a prompt is the responder's job. What
    is decided here is *what is present, in what order, and under whose trust* — which is a
    cognitive decision, not a formatting one.
    """

    window: tuple[WindowMessage, ...] = ()
    """The current conversation, verbatim and never charged to the retrieval budget."""
    recalled: tuple[ContextItem, ...] = ()
    outcomes: tuple[ToolOutcome, ...] = ()
    window_tokens: int = 0
    recalled_tokens: int = 0
    dropped: int = 0
    cited_memory_ids: tuple[str, ...] = ()
    """What was actually shown to the model. The input to reinforcement (docs/06 §7.2)."""
    has_untrusted: bool = False
    """Hoisted so a responder cannot forget to delimit untrusted material (docs/13 §6)."""
    directives: tuple[str, ...] = ()
    """How to speak, from cognition (docs/09 §6). Here rather than only on the policy so the
    responder reads one object, and so a test can assert the directives arrived."""
    mood: Mapping[str, float] = field(default_factory=dict)
    """The mood this reply is being composed under. Carried for the record and the
    inspector, not for the responder to narrate (docs/07 §14.4)."""

    @property
    def token_count(self) -> int:
        return self.window_tokens + self.recalled_tokens

    @property
    def is_empty(self) -> bool:
        return not (self.window or self.recalled or self.outcomes)


@dataclass(frozen=True, slots=True)
class TurnSummary:
    """What a completed turn announces about itself (docs/26 §7.1).

    Carries the turn's substance rather than only its identifiers, so a subscriber never
    has to read back a row the publisher has not written yet (ADR-0018).
    """

    turn_id: str
    session_id: str
    correlation_id: str
    status: str
    input: str = ""
    reply: str = ""
    intent: str = ""
    recalled_memory_ids: tuple[str, ...] = ()
    tool_calls: int = 0
    latency_ms: float = 0.0
    mood: Mapping[str, float] = field(default_factory=dict)
    """The state the turn ran under. What makes the mood timeline joinable to the
    conversation rather than a graph floating beside it."""


@dataclass(frozen=True, slots=True)
class ToolRequest:
    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    name: str
    ok: bool
    output: str = ""
    error: str | None = None
    duration_ms: float = 0.0
    trust: TrustTier = TrustTier.TOOL
    """Tool output is data, never instruction (docs/07 §7)."""


@dataclass(frozen=True, slots=True)
class Plan:
    """The planner's decision for one deliberation round."""

    intent: Intent
    rationale: str = ""
    tool: ToolRequest | None = None
    confidence: float = 0.5

    @property
    def needs_tool(self) -> bool:
        return self.intent is Intent.ACT and self.tool is not None


@runtime_checkable
class Guard(Protocol):
    """Classifies an input before anything else touches it."""

    async def inspect(self, text: str) -> SafetyVerdict: ...


@runtime_checkable
class MindReader(Protocol):
    """Reads cognitive state for a turn (docs/03 §5.7).

    `snapshot` rather than `policy` because the turn needs both the reading and its
    consequences: one call, one moment, one internally consistent turn.
    """

    async def snapshot(self) -> MindSnapshot: ...


@runtime_checkable
class Recaller(Protocol):
    """Assembles context for a turn. Satisfied by the retrieval engine."""

    async def recall(
        self, queries: Sequence[str], *, policy: TurnPolicy, turn_id: str | None = None
    ) -> RecalledContext:
        """`turn_id` is optional and only for attribution: it lets the access log say which
        turn surfaced a memory (docs/06 §7.2). Recall works without it."""
        ...


@runtime_checkable
class Planner(Protocol):
    """Decides what this turn should do.

    The one genuinely decision-making collaborator. A rule-based implementation ships now;
    an LLM-backed one replaces it without the graph changing (docs/07 §3.1, `deliberate`).
    """

    async def plan_queries(self, text: str, *, policy: TurnPolicy) -> Sequence[str]:
        """Expand the input into retrieval queries (docs/06 §5.2)."""
        ...

    async def deliberate(
        self,
        *,
        text: str,
        context: RecalledContext,
        policy: TurnPolicy,
        outcomes: Sequence[ToolOutcome],
        iteration: int,
    ) -> Plan: ...


@runtime_checkable
class Responder(Protocol):
    """Produces the user-facing reply. Satisfied later by the language model gateway.

    It receives a `ResponseContext` rather than loose parts: the ordering, the trust
    labelling and the token accounting are decided before it is called, so a responder
    cannot quietly reorder the context or drop the window.
    """

    async def compose(
        self,
        *,
        text: str,
        plan: Plan,
        context: ResponseContext,
        policy: TurnPolicy,
    ) -> str: ...

    async def refuse(self, *, verdict: SafetyVerdict) -> str: ...


@runtime_checkable
class TurnAnnouncer(Protocol):
    """Publishes what happened in a turn (docs/26 §7.1).

    A node never touches the bus directly. This port is why `nodes.py` keeps its property
    of calling ports and nothing else — and why the graph could be driven by something
    other than an event bus without a node changing.
    """

    async def session_started(self, *, session_id: str, channel: str) -> None: ...

    async def message_received(
        self, *, session_id: str, message_id: str, text: str, trust: str
    ) -> None: ...

    async def input_appraised(self, verdict: SafetyVerdict) -> None:
        """Announce how the guard read an input (docs/02 §5, docs/07 §14.3).

        The verdict, never the content: an appraisal of *what was decided about* an input,
        so no lexical judgement of a person enters emotion through a side door.
        """
        ...

    async def reply_produced(
        self, *, session_id: str, message_id: str, text: str, working_set_ref: str | None
    ) -> None: ...

    async def turn_completed(self, summary: TurnSummary) -> None: ...


@runtime_checkable
class ToolRunner(Protocol):
    """Executes a tool. Satisfied later by the tool registry (docs/03 §5.8)."""

    def requires_approval(self, name: str) -> bool: ...

    async def run(self, request: ToolRequest) -> ToolOutcome: ...
