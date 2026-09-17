"""The `LLMProvider` port (docs/03 §5.3, docs/14).

The premise the whole architecture rests on (docs/01 §5): **the language model is a
stateless faculty.** It holds nothing between calls. Every call is fully specified by what
the caller assembles, and swapping the model changes how eloquently HEDWIG speaks, not who
HEDWIG is.

So this port is deliberately narrow: generate, stream, embed, count tokens, report health.
Not in it — and not going in it — are chat memory, a tool-calling protocol, or an agent
loop. A provider that offers those is a provider whose abstractions we would inherit.

Everything crossing this boundary is an immutable value object, so a request can be logged,
replayed, or handed to a different provider without surprises.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class ModelTier(StrEnum):
    """Which class of model a request needs.

    Tiering is what makes a full cognitive apparatus affordable on a laptop (docs/14 §3):
    only user-facing prose pays for the large model.
    """

    CONVERSATIONAL = "conversational"
    UTILITY = "utility"
    EMBEDDING = "embedding"


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class RequestPriority(StrEnum):
    """Interactive work always wins (docs/17 §7).

    The gateway carries this so the resource governor has something to read; the governor
    itself is a later milestone.
    """

    INTERACTIVE = "interactive"
    BACKGROUND = "background"


class FinishReason(StrEnum):
    STOP = "stop"
    """The model finished on its own."""
    LENGTH = "length"
    """Hit `max_tokens`. The text is truncated."""
    STOP_SEQUENCE = "stop_sequence"
    TIMEOUT = "timeout"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class Message:
    """One turn of input. Not a conversation — the caller owns that."""

    role: Role
    content: str
    name: str | None = None


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    """Everything a provider needs for one call.

    `purpose` and `priority` are not decoration: `purpose` routes the request to a tier and
    is how "what spent the token budget last night?" becomes answerable, and `priority` is
    what the resource governor will read.
    """

    messages: Sequence[Message]
    purpose: str
    """Dotted name, e.g. `compose.reply` or `reflect.summarise` (docs/14 §3.1)."""

    tier: ModelTier | None = None
    """Explicit override. Normally `None` — the gateway routes by `purpose`."""

    model: str | None = None
    """Explicit model override, bypassing tier selection. For evaluation and comparison."""

    max_tokens: int = 1024
    temperature: float = 0.7
    top_p: float | None = None
    stop: tuple[str, ...] = ()
    seed: int | None = None
    """Set for reproducibility. Structured calls set it automatically."""

    json_schema: Mapping[str, Any] | None = None
    """When present, the result must validate against it (docs/14 §6)."""

    priority: RequestPriority = RequestPriority.INTERACTIVE
    timeout_seconds: float | None = None
    """Overrides the configured request timeout for this call alone."""

    metadata: Mapping[str, Any] = field(default_factory=dict)
    """Free-form, carried into telemetry. Never sent to the model."""


@dataclass(frozen=True, slots=True)
class TokenUsage:
    prompt: int = 0
    completion: int = 0

    @property
    def total(self) -> int:
        return self.prompt + self.completion


@dataclass(frozen=True, slots=True)
class GenerationResult:
    """The structured response object.

    Carries enough to answer "why was that slow?" and "which model said this?" without a
    second lookup, because both questions are asked constantly and the alternative is
    correlating logs by hand.
    """

    text: str
    model: str
    tier: ModelTier
    purpose: str
    usage: TokenUsage
    duration_ms: float
    finish_reason: FinishReason = FinishReason.STOP
    first_token_ms: float | None = None
    """Time to first token when streamed. The number that decides whether HEDWIG *feels*
    responsive; total duration hides it (docs/19 §5)."""
    parsed: Any | None = None
    """Populated only when `json_schema` was supplied and validation succeeded."""
    reasoning: str | None = None
    """Chain-of-thought from a reasoning model (qwen3, deepseek-r1), which Ollama returns
    separately from the answer. Kept apart from `text` deliberately: it is diagnostic
    material, not output, and must never be shown as if it were the reply."""
    attempts: int = 1
    escalated: bool = False
    """True when a structured call had to fall back to a larger tier (docs/14 §6)."""
    correlation_id: str | None = None

    @property
    def truncated(self) -> bool:
        return self.finish_reason is FinishReason.LENGTH


@dataclass(frozen=True, slots=True)
class StreamChunk:
    """One piece of a streamed response.

    The final chunk has `done=True` and carries the completed `result`, so a consumer never
    has to reassemble usage and timing itself.
    """

    text: str
    index: int
    done: bool = False
    result: GenerationResult | None = None


@dataclass(frozen=True, slots=True)
class ModelInfo:
    name: str
    size_bytes: int | None = None
    family: str | None = None
    parameter_size: str | None = None
    quantization: str | None = None
    context_length: int | None = None


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    available: bool
    endpoint: str
    installed: tuple[str, ...] = ()
    """Models the provider can serve right now."""
    missing: tuple[str, ...] = ()
    """Configured models that are not installed. A precise, actionable failure."""
    latency_ms: float | None = None
    version: str | None = None
    error: str | None = None


@runtime_checkable
class LLMProvider(Protocol):
    """A language model backend.

    Implementations translate these calls into whatever the backend speaks. They do not
    retry, route by purpose, enforce budgets, or validate schemas — that is the gateway's
    job, and keeping it out of here is what makes a second provider cheap to write.
    """

    @property
    def name(self) -> str: ...

    @property
    def endpoint(self) -> str: ...

    async def generate(self, request: GenerationRequest, *, model: str) -> GenerationResult:
        """Run one completion.

        Raises `ModelUnavailableError` if the backend or model cannot be reached, and
        `GenerationTimeoutError` if it does not answer in time.
        """
        ...

    def stream(self, request: GenerationRequest, *, model: str) -> AsyncIterator[StreamChunk]:
        """Run one completion, yielding chunks as they arrive.

        The last chunk has `done=True` and carries the full `GenerationResult`.
        """
        ...

    async def embed(self, texts: Sequence[str], *, model: str) -> Sequence[Sequence[float]]:
        """Embed one or more texts. Order of the result matches the input."""
        ...

    async def count_tokens(self, text: str, *, model: str) -> int:
        """Token count for budgeting. May be an estimate; the contract is that it never
        under-counts by more than a few percent (docs/14 §11)."""
        ...

    async def list_models(self) -> Sequence[ModelInfo]: ...

    async def health(self, *, expected: Sequence[str] = ()) -> ProviderHealth:
        """Probe the backend. Never raises: an unavailable backend is a health result."""
        ...

    async def close(self) -> None:
        """Release connections."""
        ...
