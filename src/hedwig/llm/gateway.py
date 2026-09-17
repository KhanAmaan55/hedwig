"""The language model gateway (docs/14).

The only part of HEDWIG that talks to a model. Everything else asks it for a bounded job.

What it adds on top of a provider:

* **Tier routing by purpose.** `compose.reply` goes to the conversational model,
  `reflect.summarise` to the small one. Callers name a purpose, not a model, so the cost
  model of the whole system is one table (docs/14 §3.1).
* **Retries with backoff**, on transient failures only, and never after a stream has begun
  emitting — duplicating tokens the user has already seen is worse than failing.
* **Timeouts** at three levels: connect, per-chunk stall, and total deadline.
* **Structured output** through parse → repair → retry → escalate (docs/14 §6).
* **Model switching** at runtime, verified against what is actually installed.
* **Metrics and call records**, because "what spent the token budget last night?" is asked
  constantly and is unanswerable after the fact otherwise.

It deliberately does *not* assemble prompts from memory or personality. That is
conversation, and it belongs to a later milestone; this service takes messages it is given.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from hedwig.core.config import LlmConfig
from hedwig.core.context import current_correlation_id
from hedwig.core.errors import InvalidRequestError, NotFoundError
from hedwig.core.ids import new_id
from hedwig.core.logging import fields, get_logger
from hedwig.core.ports import (
    Clock,
    EventBus,
    FinishReason,
    GenerationRequest,
    GenerationResult,
    Health,
    HealthStatus,
    LLMProvider,
    Message,
    ModelTier,
    ProviderHealth,
    Role,
    StreamChunk,
)
from hedwig.core.store import Database
from hedwig.llm.errors import (
    BudgetExceededError,
    LLMError,
    SchemaValidationError,
    StreamStalledError,
)
from hedwig.llm.metrics import GatewayMetrics
from hedwig.llm.structured import parse_json, repair_instruction, validate

logger = get_logger(__name__)

PURPOSE_TIERS: Mapping[str, ModelTier] = {
    # docs/14 §3.1. Matched by longest dotted prefix, so `compose` covers `compose.reply`.
    "compose": ModelTier.CONVERSATIONAL,
    "plan": ModelTier.UTILITY,
    "reflect": ModelTier.UTILITY,
    "emotion": ModelTier.UTILITY,
    "curiosity": ModelTier.UTILITY,
    "extract": ModelTier.UTILITY,
    "embed": ModelTier.EMBEDDING,
}
DEFAULT_TIER = ModelTier.UTILITY
"""Unknown purposes get the cheap model. Being wrong in the cheap direction is recoverable;
silently routing everything to the large model is how a laptop becomes unusable."""


@dataclass(frozen=True, slots=True)
class ModelSwitch:
    """The record of a runtime model change."""

    tier: ModelTier
    previous: str
    current: str
    reason: str
    verified: bool
    """Whether the new model was confirmed installed before the switch was applied."""


class LanguageModelGateway:
    """The `LLMProvider`-backed service the rest of HEDWIG calls."""

    def __init__(
        self,
        provider: LLMProvider,
        *,
        config: LlmConfig,
        clock: Clock,
        database: Database | None = None,
        bus: EventBus | None = None,
        metrics: GatewayMetrics | None = None,
    ) -> None:
        self._provider = provider
        self._config = config
        self._clock = clock
        self._database = database
        self._bus = bus
        self.metrics = metrics or GatewayMetrics()

        self._models: dict[ModelTier, str] = {
            ModelTier.CONVERSATIONAL: config.conversational,
            ModelTier.UTILITY: config.utility,
            ModelTier.EMBEDDING: config.embedding,
        }
        # One concurrent generation by default: Ollama serves a single model efficiently,
        # and a background job holding it while the user types is the worst experience
        # available (docs/14 §8).
        self._slots = asyncio.Semaphore(max(1, config.max_concurrent))
        self._last_health: ProviderHealth | None = None
        self._started = False

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        """Probe the backend, but never refuse to start because of it.

        A missing model is a fixable situation, and HEDWIG is more useful running and
        saying so than refusing to boot (docs/17 §3).
        """
        self._started = True
        health = await self.provider_health()
        if not health.available:
            logger.warning(
                "language model backend unavailable at startup",
                extra=fields(endpoint=health.endpoint, error=health.error),
            )
        elif health.missing:
            logger.warning(
                "configured models are not installed",
                extra=fields(missing=list(health.missing), hint="ollama pull <model>"),
            )
        else:
            logger.info(
                "language model gateway ready",
                extra=fields(
                    endpoint=health.endpoint,
                    models={tier.value: name for tier, name in self._models.items()},
                ),
            )

    async def stop(self) -> None:
        self._started = False
        await self._provider.close()
        logger.info("language model gateway stopped", extra=fields(**self.metrics.snapshot()))

    # -- model selection ---------------------------------------------------

    def tier_for(self, purpose: str) -> ModelTier:
        """Longest-prefix match over `PURPOSE_TIERS`."""
        parts = purpose.split(".")
        for length in range(len(parts), 0, -1):
            candidate = ".".join(parts[:length])
            if candidate in PURPOSE_TIERS:
                return PURPOSE_TIERS[candidate]
        return DEFAULT_TIER

    def model_for(self, purpose_or_tier: str | ModelTier) -> str:
        tier = (
            purpose_or_tier
            if isinstance(purpose_or_tier, ModelTier)
            else self.tier_for(purpose_or_tier)
        )
        return self._models[tier]

    def models(self) -> Mapping[str, str]:
        return {tier.value: name for tier, name in self._models.items()}

    async def switch_model(
        self, tier: ModelTier, model: str, *, reason: str = "manual", verify: bool = True
    ) -> ModelSwitch:
        """Point a tier at a different model.

        Verified by default: switching to a model that is not installed would turn every
        subsequent call into a 404, which is a bad way to find out about a typo.
        """
        if not model.strip():
            raise InvalidRequestError("model name cannot be empty", tier=tier.value)

        verified = False
        if verify:
            health = await self.provider_health()
            if health.available:
                if model not in health.installed and not any(
                    name.split(":")[0] == model for name in health.installed
                ):
                    raise NotFoundError(
                        f"model {model!r} is not installed. Run: ollama pull {model}",
                        model=model,
                        installed=list(health.installed),
                    )
                verified = True

        previous = self._models[tier]
        self._models[tier] = model
        self.metrics.record_model_switch()

        switch = ModelSwitch(
            tier=tier, previous=previous, current=model, reason=reason, verified=verified
        )
        logger.info(
            "model switched",
            extra=fields(
                tier=tier.value,
                previous=previous,
                current=model,
                reason=reason,
                verified=verified,
            ),
        )
        if self._bus is not None:
            await self._bus.emit(
                "llm.model.switched",
                {
                    "tier": tier.value,
                    "previous": previous,
                    "current": model,
                    "reason": reason,
                },
                source="llm.gateway",
            )
        return switch

    # -- generation --------------------------------------------------------

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        """Run a request to completion, with routing, retries and validation."""
        tier = request.tier or self.tier_for(request.purpose)
        model = request.model or self._models[tier]
        resolved = replace(request, tier=tier)

        if request.json_schema is not None:
            return await self._generate_structured(resolved, tier=tier, model=model)
        return await self._generate_with_retries(resolved, tier=tier, model=model)

    async def stream(self, request: GenerationRequest) -> AsyncIterator[StreamChunk]:
        """Stream a response.

        Retries happen only *before* the first token. Once the caller has seen output,
        transparently restarting would duplicate it, so a mid-stream failure is raised and
        the partial text is what the caller keeps.
        """
        tier = request.tier or self.tier_for(request.purpose)
        model = request.model or self._models[tier]
        resolved = replace(request, tier=tier)

        if resolved.json_schema is not None:
            raise InvalidRequestError(
                "structured output cannot be streamed: the schema can only be validated "
                "once the whole document has arrived",
                purpose=request.purpose,
            )

        attempt = 0
        started_streaming = False

        while True:
            attempt += 1
            try:
                async with self._slots:
                    async for chunk in self._provider.stream(resolved, model=model):
                        started_streaming = started_streaming or bool(chunk.text)
                        if chunk.done and chunk.result is not None:
                            final = replace(
                                chunk.result,
                                attempts=attempt,
                                correlation_id=current_correlation_id(),
                            )
                            self._record(final)
                            yield replace(chunk, result=final)
                        else:
                            yield chunk
                return
            except LLMError as exc:
                if started_streaming or not self._should_retry(exc, attempt):
                    self.metrics.record_failure(
                        purpose=request.purpose, tier=tier.value, model=model, code=exc.code
                    )
                    self._persist_failure(request, tier, model, exc)
                    raise
                await self._backoff(attempt, exc, purpose=request.purpose)

    async def embed(
        self, texts: Sequence[str], *, model: str | None = None
    ) -> Sequence[Sequence[float]]:
        """Embed texts with the embedding-tier model."""
        chosen = model or self._models[ModelTier.EMBEDDING]
        attempt = 0
        while True:
            attempt += 1
            try:
                async with self._slots:
                    return await self._provider.embed(texts, model=chosen)
            except LLMError as exc:
                if not self._should_retry(exc, attempt):
                    self.metrics.record_failure(
                        purpose="embed",
                        tier=ModelTier.EMBEDDING.value,
                        model=chosen,
                        code=exc.code,
                    )
                    raise
                await self._backoff(attempt, exc, purpose="embed")

    async def count_tokens(self, text: str, *, tier: ModelTier = ModelTier.UTILITY) -> int:
        return await self._provider.count_tokens(text, model=self._models[tier])

    # -- retry -------------------------------------------------------------

    async def _generate_with_retries(
        self, request: GenerationRequest, *, tier: ModelTier, model: str
    ) -> GenerationResult:
        attempt = 0
        while True:
            attempt += 1
            try:
                async with self._slots:
                    result = await self._provider.generate(request, model=model)
            except LLMError as exc:
                if not self._should_retry(exc, attempt):
                    self.metrics.record_failure(
                        purpose=request.purpose, tier=tier.value, model=model, code=exc.code
                    )
                    self._persist_failure(request, tier, model, exc)
                    raise
                await self._backoff(attempt, exc, purpose=request.purpose)
                continue

            final = replace(result, attempts=attempt, correlation_id=current_correlation_id())
            self._record(final)
            return final

    def _should_retry(self, error: LLMError, attempt: int) -> bool:
        """Retryability is a property of the error, not of its class.

        A 404 for a missing model and a 503 for a daemon still warming up are both
        `ModelUnavailableError`; only one of them is worth waiting for.
        """
        if attempt >= self._config.max_attempts:
            return False
        # A stalled stream has already produced partial output; restarting it would
        # duplicate what the caller has seen, so that is their decision, not ours.
        if isinstance(error, StreamStalledError):
            return False
        return error.retryable

    async def _backoff(self, attempt: int, error: LLMError, *, purpose: str) -> None:
        delay = self._config.retry_base_delay_seconds * (2 ** (attempt - 1))
        # Jitter, so several queued callers do not retry in lockstep.
        delay = min(delay, 30.0) * (0.5 + random.random() / 2)
        logger.warning(
            "model call failed, retrying",
            extra=fields(
                purpose=purpose,
                attempt=attempt,
                retry_in_s=round(delay, 2),
                error=f"{type(error).__name__}: {error}",
            ),
        )
        await self._clock.sleep(delay)

    # -- structured output -------------------------------------------------

    async def _generate_structured(
        self, request: GenerationRequest, *, tier: ModelTier, model: str
    ) -> GenerationResult:
        """parse → repair → retry at temperature 0 → escalate a tier → give up.

        Every caller must hold a typed default for the final failure (docs/14 §6): a
        structured-output failure must never abort a turn.
        """
        schema = dict(request.json_schema or {})
        # Determinism helps twice: it makes the retry meaningfully different from the first
        # attempt only because of the correction, and it makes tests reproducible.
        attempt_request = replace(request, temperature=0.0, seed=request.seed or 7)
        errors: list[str] = []
        escalated = False
        attempts = 0

        for stage in range(3):
            if stage == 2:
                if tier is ModelTier.CONVERSATIONAL:
                    break
                # Last resort: the large model. Worth it for the ~2% that get here, and
                # ruinous if it were the default (docs/14 §6).
                tier, model, escalated = (
                    ModelTier.CONVERSATIONAL,
                    self._models[ModelTier.CONVERSATIONAL],
                    True,
                )
                attempt_request = replace(attempt_request, tier=tier)
                self.metrics.record_escalation(
                    purpose=request.purpose, tier=tier.value, model=model
                )

            result = await self._generate_with_retries(attempt_request, tier=tier, model=model)
            attempts += result.attempts

            parsed = parse_json(result.text)
            if parsed is not None:
                errors = validate(parsed, schema)
                if not errors:
                    return replace(
                        result,
                        parsed=parsed,
                        attempts=attempts,
                        escalated=escalated,
                        tier=tier,
                    )
            else:
                errors = ["the reply was not JSON"]

            self.metrics.record_schema_failure(
                purpose=request.purpose, tier=tier.value, model=model
            )
            logger.warning(
                "structured output did not validate",
                extra=fields(
                    purpose=request.purpose, model=model, stage=stage, problems=errors[:3]
                ),
            )
            attempt_request = replace(
                attempt_request,
                messages=[
                    *attempt_request.messages,
                    Message(role=Role.ASSISTANT, content=result.text),
                    Message(role=Role.USER, content=repair_instruction(errors, schema)),
                ],
            )

        raise SchemaValidationError(
            f"{request.purpose} could not produce valid JSON after repair and escalation",
            purpose=request.purpose,
            model=model,
            problems=errors[:5],
        )

    # -- health ------------------------------------------------------------

    async def provider_health(self) -> ProviderHealth:
        health = await self._provider.health(expected=tuple(self._models.values()))
        self._last_health = health
        return health

    async def health(self) -> Health:
        health = await self.provider_health()

        if not health.available:
            status = HealthStatus.DOWN
            message = health.error or "backend unreachable"
        elif health.missing:
            status = HealthStatus.DEGRADED
            message = f"not installed: {', '.join(health.missing)}"
        elif self.metrics.schema_failure_rate() > 0.02:
            # The earliest signal that a model or a template has degraded (docs/14 §6).
            status = HealthStatus.DEGRADED
            message = f"schema failure rate {self.metrics.schema_failure_rate():.1%}"
        else:
            status, message = HealthStatus.OK, ""

        return Health(
            status=status,
            message=message,
            detail={
                "endpoint": health.endpoint,
                "provider": self._provider.name,
                "version": health.version,
                "models": self.models(),
                "missing": list(health.missing),
                "latency_ms": health.latency_ms,
                **self.metrics.snapshot(),
            },
        )

    # -- telemetry ---------------------------------------------------------

    def _record(self, result: GenerationResult) -> None:
        self.metrics.record_success(
            purpose=result.purpose,
            tier=result.tier.value,
            model=result.model,
            duration_ms=result.duration_ms,
            tokens_in=result.usage.prompt,
            tokens_out=result.usage.completion,
            first_token_ms=result.first_token_ms,
            attempts=result.attempts,
        )
        self._persist(result)
        logger.debug(
            "model call completed",
            extra={
                "duration_ms": result.duration_ms,
                "fields": {
                    "purpose": result.purpose,
                    "model": result.model,
                    "tokens_out": result.usage.completion,
                    "attempts": result.attempts,
                },
            },
        )

    def _persist(self, result: GenerationResult) -> None:
        if self._database is None:
            return
        self._database.execute(
            """
            INSERT INTO llm_call (id, purpose, tier, model, status, tokens_in, tokens_out,
                                  duration_ms, first_token_ms, attempts, escalated,
                                  finish_reason, correlation_id, created_at)
            VALUES (?, ?, ?, ?, 'ok', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("llm"),
                result.purpose,
                result.tier.value,
                result.model,
                result.usage.prompt,
                result.usage.completion,
                result.duration_ms,
                result.first_token_ms,
                result.attempts,
                1 if result.escalated else 0,
                result.finish_reason.value,
                result.correlation_id,
                self._clock.now().isoformat(timespec="milliseconds"),
            ),
        )

    def _persist_failure(
        self, request: GenerationRequest, tier: ModelTier, model: str, error: LLMError
    ) -> None:
        if self._database is None:
            return
        self._database.execute(
            """
            INSERT INTO llm_call (id, purpose, tier, model, status, error,
                                  finish_reason, correlation_id, created_at)
            VALUES (?, ?, ?, ?, 'failed', ?, ?, ?, ?)
            """,
            (
                new_id("llm"),
                request.purpose,
                tier.value,
                model,
                f"{type(error).__name__}: {error}",
                FinishReason.ERROR.value,
                current_correlation_id(),
                self._clock.now().isoformat(timespec="milliseconds"),
            ),
        )

    # -- budgets -----------------------------------------------------------

    def check_budget(self, tokens: int) -> None:
        """Refuse a request that would exceed the per-request cap.

        Deliberately small in this milestone: the daily and per-job budgets in docs/14 §8
        belong with the resource governor, which has the scheduler context to enforce them.
        """
        if self._config.max_request_tokens and tokens > self._config.max_request_tokens:
            raise BudgetExceededError(
                f"request of {tokens} tokens exceeds the "
                f"{self._config.max_request_tokens} token cap",
                tokens=tokens,
            )


def user(content: str) -> Message:
    """Convenience for callers assembling a one-shot request."""
    return Message(role=Role.USER, content=content)


def system(content: str) -> Message:
    return Message(role=Role.SYSTEM, content=content)


def request(
    prompt: str, *, purpose: str, instructions: str | None = None, **kwargs: Any
) -> GenerationRequest:
    """Build a single-turn request. The gateway's front door for simple callers."""
    messages = [system(instructions)] if instructions else []
    messages.append(user(prompt))
    return GenerationRequest(messages=messages, purpose=purpose, **kwargs)
