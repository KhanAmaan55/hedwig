"""Typed errors for the language faculty.

Ports never leak provider exceptions (docs/03 §2 rule 5), so every `httpx` failure becomes
one of these before it crosses a boundary. The distinction that matters most is
**retryable or not** — the gateway reads it rather than guessing from the message.
"""

from __future__ import annotations

from hedwig.core.errors import HedwigError


class LLMError(HedwigError):
    """Base for everything the language faculty raises."""

    code = "llm_error"


class ModelUnavailableError(LLMError):
    """The backend is unreachable, or the model is not installed.

    Retryable: the usual cause is Ollama not running yet, which fixes itself.
    """

    code = "capability_unavailable"
    http_status = 503
    retryable = True


class GenerationTimeoutError(LLMError):
    """The model did not answer within the deadline."""

    code = "generation_timeout"
    http_status = 504
    retryable = True


class StreamStalledError(GenerationTimeoutError):
    """A stream stopped producing tokens.

    Distinct from a plain timeout because the response is *partial* rather than absent:
    the caller may still have something worth showing (docs/14 §10).
    """

    code = "stream_stalled"


class SchemaValidationError(LLMError):
    """Structured output could not be produced, even after repair and escalation.

    Never fatal to a turn by itself: docs/14 §6 requires every caller to hold a typed
    default. This is what tells them to use it.
    """

    code = "schema_validation_failed"
    http_status = 502


class BudgetExceededError(LLMError):
    """The configured token or concurrency budget refused this request."""

    code = "budget_exhausted"
    http_status = 429
    retryable = False
