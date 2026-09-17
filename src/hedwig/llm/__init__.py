"""Layer 5 — Capability: the language faculty.

The only package that talks to a language model. Everything above it asks the gateway for a
bounded job and receives a structured result (docs/14).
"""

from __future__ import annotations

from hedwig.llm.errors import (
    BudgetExceededError,
    GenerationTimeoutError,
    LLMError,
    ModelUnavailableError,
    SchemaValidationError,
    StreamStalledError,
)
from hedwig.llm.gateway import (
    DEFAULT_TIER,
    PURPOSE_TIERS,
    LanguageModelGateway,
    ModelSwitch,
    request,
    system,
    user,
)
from hedwig.llm.metrics import GatewayMetrics, Histogram
from hedwig.llm.ollama import OllamaProvider
from hedwig.llm.recorded import RecordedProvider, ScriptedResponse, json_response

__all__ = [
    "DEFAULT_TIER",
    "PURPOSE_TIERS",
    "BudgetExceededError",
    "GatewayMetrics",
    "GenerationTimeoutError",
    "Histogram",
    "LLMError",
    "LanguageModelGateway",
    "ModelSwitch",
    "ModelUnavailableError",
    "OllamaProvider",
    "RecordedProvider",
    "SchemaValidationError",
    "ScriptedResponse",
    "StreamStalledError",
    "json_response",
    "request",
    "system",
    "user",
]
