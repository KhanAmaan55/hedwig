"""A provider that answers from a script (docs/20 §3.2).

The determinism seam for everything above the gateway. Without it, every test of a
cognitive path needs a running Ollama, an 8 GB model, and tolerance for a different answer
each run — which is to say, no tests.

Two modes:

* **Scripted** — responses supplied in code, matched by purpose. What unit tests use.
* **Replay** — responses loaded from `tests/fixtures/llm/`, keyed by a hash of the request,
  so a recorded real interaction can be replayed byte-for-byte. A miss is a test failure
  with the prompt written out, not a silent fallback to something plausible.

It is a `LLMProvider` like any other, so it passes the same conformance suite as Ollama.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from hedwig.core.ports import (
    FinishReason,
    GenerationRequest,
    GenerationResult,
    ModelInfo,
    ModelTier,
    ProviderHealth,
    StreamChunk,
    TokenUsage,
)
from hedwig.llm.errors import ModelUnavailableError

CHARS_PER_TOKEN = 4


@dataclass(slots=True)
class ScriptedResponse:
    """One canned answer."""

    text: str
    tokens_in: int = 10
    tokens_out: int = 0
    duration_ms: float = 5.0
    finish_reason: FinishReason = FinishReason.STOP
    error: Exception | None = None
    """Raised instead of answering. For testing retries and failure paths."""

    def __post_init__(self) -> None:
        if not self.tokens_out:
            self.tokens_out = max(1, len(self.text) // CHARS_PER_TOKEN)


@dataclass(slots=True)
class RecordedProvider:
    """The `LLMProvider` implementation for tests."""

    responses: dict[str, list[ScriptedResponse]] = field(default_factory=dict)
    default: ScriptedResponse | None = None
    fixtures: Path | None = None
    installed: tuple[str, ...] = ("llama3.1:8b", "qwen2.5:3b", "nomic-embed-text")
    available: bool = True
    embedding_dimensions: int = 8
    calls: list[GenerationRequest] = field(default_factory=list)
    """Every request received, in order. Tests assert against this."""

    @property
    def name(self) -> str:
        return "recorded"

    @property
    def endpoint(self) -> str:
        return "recorded://"

    async def close(self) -> None:
        return None

    # -- scripting ---------------------------------------------------------

    def script(self, purpose: str, *responses: ScriptedResponse | str) -> RecordedProvider:
        """Queue responses for a purpose. Consumed in order; the last one repeats."""
        queue = self.responses.setdefault(purpose, [])
        for response in responses:
            queue.append(ScriptedResponse(text=response) if isinstance(response, str) else response)
        return self

    def _next(self, request: GenerationRequest) -> ScriptedResponse:
        queue = self.responses.get(request.purpose)
        if queue:
            # The last scripted response repeats, so a retry test does not need to script
            # an unbounded number of failures.
            return queue.pop(0) if len(queue) > 1 else queue[0]

        if self.fixtures is not None:
            loaded = self._load_fixture(request)
            if loaded is not None:
                return loaded

        if self.default is not None:
            return self.default

        raise AssertionError(
            f"RecordedProvider has no response for purpose {request.purpose!r}.\n"
            f"Script one with .script({request.purpose!r}, ...), or set `default`.\n"
            f"Prompt was:\n{_render(request)}"
        )

    def _load_fixture(self, request: GenerationRequest) -> ScriptedResponse | None:
        assert self.fixtures is not None
        path = self.fixtures / f"{request.purpose}__{fingerprint(request)}.json"
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return ScriptedResponse(
            text=data["text"],
            tokens_in=data.get("tokens_in", 10),
            tokens_out=data.get("tokens_out", 0),
        )

    # -- provider ----------------------------------------------------------

    async def generate(self, request: GenerationRequest, *, model: str) -> GenerationResult:
        self.calls.append(request)
        if not self.available:
            raise ModelUnavailableError("recorded provider is marked unavailable")

        response = self._next(request)
        if response.error is not None:
            raise response.error

        return GenerationResult(
            text=response.text,
            model=model,
            tier=request.tier or ModelTier.UTILITY,
            purpose=request.purpose,
            usage=TokenUsage(prompt=response.tokens_in, completion=response.tokens_out),
            duration_ms=response.duration_ms,
            finish_reason=response.finish_reason,
        )

    async def stream(self, request: GenerationRequest, *, model: str) -> AsyncIterator[StreamChunk]:
        self.calls.append(request)
        if not self.available:
            raise ModelUnavailableError("recorded provider is marked unavailable")

        response = self._next(request)
        if response.error is not None:
            raise response.error

        # Word-by-word, so consumers exercise real reassembly rather than one big chunk.
        pieces = [word + " " for word in response.text.split(" ")]
        if pieces:
            pieces[-1] = pieces[-1].rstrip()

        for index, piece in enumerate(pieces):
            yield StreamChunk(text=piece, index=index)

        yield StreamChunk(
            text="",
            index=len(pieces),
            done=True,
            result=GenerationResult(
                text=response.text,
                model=model,
                tier=request.tier or ModelTier.UTILITY,
                purpose=request.purpose,
                usage=TokenUsage(prompt=response.tokens_in, completion=response.tokens_out),
                duration_ms=response.duration_ms,
                first_token_ms=1.0,
                finish_reason=response.finish_reason,
            ),
        )

    async def embed(self, texts: Sequence[str], *, model: str) -> Sequence[Sequence[float]]:
        if not self.available:
            raise ModelUnavailableError("recorded provider is marked unavailable")
        # Deterministic from the text, so the same input always yields the same vector and
        # a similarity assertion is stable.
        return [_pseudo_vector(text, self.embedding_dimensions) for text in texts]

    async def count_tokens(self, text: str, *, model: str) -> int:
        return max(1, len(text) // CHARS_PER_TOKEN)

    async def list_models(self) -> Sequence[ModelInfo]:
        return [ModelInfo(name=name) for name in self.installed]

    async def health(self, *, expected: Sequence[str] = ()) -> ProviderHealth:
        if not self.available:
            return ProviderHealth(
                available=False, endpoint=self.endpoint, error="marked unavailable"
            )
        return ProviderHealth(
            available=True,
            endpoint=self.endpoint,
            installed=self.installed,
            missing=tuple(name for name in expected if name not in self.installed),
            latency_ms=0.1,
            version="recorded",
        )


def fingerprint(request: GenerationRequest) -> str:
    """Stable hash of what actually determines a response."""
    material = json.dumps(
        {
            "purpose": request.purpose,
            "messages": [[m.role.value, m.content] for m in request.messages],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "schema": dict(request.json_schema) if request.json_schema else None,
        },
        sort_keys=True,
    )
    return hashlib.sha256(material.encode()).hexdigest()[:12]


def _render(request: GenerationRequest) -> str:
    return "\n".join(f"[{m.role.value}] {m.content}" for m in request.messages)


def _pseudo_vector(text: str, dimensions: int) -> list[float]:
    digest = hashlib.sha256(text.encode()).digest()
    raw = [digest[index % len(digest)] / 255 for index in range(dimensions)]
    norm = sum(value * value for value in raw) ** 0.5 or 1.0
    return [value / norm for value in raw]


def json_response(payload: Mapping[str, object]) -> ScriptedResponse:
    """A scripted response carrying JSON, for structured-output tests."""
    return ScriptedResponse(text=json.dumps(dict(payload)))
