"""The Ollama provider.

Speaks Ollama's HTTP API and nothing else. No retries, no routing, no schema validation —
those belong to the gateway, and keeping them out is what makes a second provider (llama.cpp,
vLLM, an OpenAI-compatible endpoint) a small file rather than a rewrite.

Endpoints used:

* `POST /api/chat`   — generation, streaming or not, NDJSON when streaming
* `POST /api/embed`  — embeddings
* `GET  /api/tags`   — installed models
* `GET  /api/version`— liveness and version

Two behaviours are worth knowing about. Ollama reports timings in nanoseconds, which are
converted here so nothing downstream has to remember that. And a *stalled* stream is
distinguished from a slow one: the read timeout applies between chunks, so a model that
stops producing tokens fails as `StreamStalledError` with the partial text preserved,
rather than hanging until the total deadline (docs/14 §10).
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

import httpx

from hedwig.core.logging import fields, get_logger
from hedwig.core.ports import (
    FinishReason,
    GenerationRequest,
    GenerationResult,
    Message,
    ModelInfo,
    ModelTier,
    ProviderHealth,
    StreamChunk,
    TokenUsage,
)
from hedwig.llm.errors import GenerationTimeoutError, ModelUnavailableError, StreamStalledError

logger = get_logger(__name__)

NANOSECONDS = 1_000_000_000
CHARS_PER_TOKEN = 4
"""Fallback estimate when the backend reports no usage. Deliberately conservative: a
budget that under-counts is worse than one that over-counts (docs/14 §11)."""


class OllamaProvider:
    """The `LLMProvider` implementation for a local Ollama daemon."""

    def __init__(
        self,
        endpoint: str = "http://127.0.0.1:11434",
        *,
        client: httpx.AsyncClient | None = None,
        request_timeout: float = 120.0,
        connect_timeout: float = 5.0,
        stall_timeout: float = 20.0,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._request_timeout = request_timeout
        self._stall_timeout = stall_timeout
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self._endpoint,
            timeout=httpx.Timeout(request_timeout, connect=connect_timeout, read=stall_timeout),
        )

    @property
    def name(self) -> str:
        return "ollama"

    @property
    def endpoint(self) -> str:
        return self._endpoint

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # -- generation --------------------------------------------------------

    async def generate(self, request: GenerationRequest, *, model: str) -> GenerationResult:
        started = time.monotonic()
        payload = self._chat_payload(request, model=model, stream=False)

        data = await self._post_json(
            "/api/chat", payload, timeout=request.timeout_seconds or self._request_timeout
        )
        duration_ms = (time.monotonic() - started) * 1000

        message = data.get("message", {})
        text = str(message.get("content", ""))
        reasoning = str(message.get("thinking", "")) or None
        if not text and reasoning:
            # A reasoning model that used its whole budget thinking. Silently returning ""
            # would look like a broken model; this says what actually happened.
            logger.warning(
                "model produced only reasoning, no answer",
                extra=fields(
                    model=model, max_tokens=request.max_tokens, reasoning_chars=len(reasoning)
                ),
            )
        return GenerationResult(
            text=text,
            reasoning=reasoning,
            model=str(data.get("model", model)),
            tier=request.tier or ModelTier.UTILITY,
            purpose=request.purpose,
            usage=self._usage(data, request, text),
            duration_ms=round(duration_ms, 2),
            finish_reason=_finish_reason(data.get("done_reason")),
        )

    async def stream(self, request: GenerationRequest, *, model: str) -> AsyncIterator[StreamChunk]:
        started = time.monotonic()
        first_token_ms: float | None = None
        payload = self._chat_payload(request, model=model, stream=True)
        parts: list[str] = []
        thoughts: list[str] = []
        index = 0
        final: Mapping[str, Any] = {}

        try:
            async with self._client.stream(
                "POST",
                "/api/chat",
                json=payload,
                timeout=httpx.Timeout(
                    request.timeout_seconds or self._request_timeout,
                    connect=5.0,
                    # The read timeout is per-chunk, which is what turns "the model went
                    # quiet" into a distinct, catchable failure.
                    read=self._stall_timeout,
                ),
            ) as response:
                if response.status_code >= 400:
                    await response.aread()
                    raise self._http_error(response, model)

                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    message = chunk.get("message", {})
                    piece = str(message.get("content", ""))
                    thought = str(message.get("thinking", ""))
                    if thought:
                        # Collected, never yielded: streaming a model's chain-of-thought to
                        # the user as if it were the reply is a category error.
                        thoughts.append(thought)

                    if piece:
                        if first_token_ms is None:
                            first_token_ms = (time.monotonic() - started) * 1000
                        parts.append(piece)
                        yield StreamChunk(text=piece, index=index)
                        index += 1

                    if chunk.get("done"):
                        final = chunk
                        break
        except httpx.ReadTimeout as exc:
            raise StreamStalledError(
                f"no output for {self._stall_timeout:g}s from {model}",
                model=model,
                partial_chars=len("".join(parts)),
            ) from exc
        except httpx.TimeoutException as exc:
            raise GenerationTimeoutError(f"{model} timed out", model=model) from exc
        except httpx.HTTPError as exc:
            raise ModelUnavailableError(
                f"cannot reach Ollama at {self._endpoint}: {exc}", endpoint=self._endpoint
            ) from exc

        text = "".join(parts)
        yield StreamChunk(
            text="",
            index=index,
            done=True,
            result=GenerationResult(
                text=text,
                model=str(final.get("model", model)),
                tier=request.tier or ModelTier.UTILITY,
                purpose=request.purpose,
                reasoning="".join(thoughts) or None,
                usage=self._usage(final, request, text),
                duration_ms=round((time.monotonic() - started) * 1000, 2),
                first_token_ms=round(first_token_ms, 2) if first_token_ms is not None else None,
                finish_reason=_finish_reason(final.get("done_reason")),
            ),
        )

    # -- embeddings --------------------------------------------------------

    async def embed(self, texts: Sequence[str], *, model: str) -> Sequence[Sequence[float]]:
        if not texts:
            return []
        data = await self._post_json(
            "/api/embed", {"model": model, "input": list(texts)}, timeout=self._request_timeout
        )
        vectors = data.get("embeddings") or []
        if len(vectors) != len(texts):
            raise ModelUnavailableError(
                f"{model} returned {len(vectors)} embeddings for {len(texts)} inputs",
                model=model,
            )
        return [[float(value) for value in vector] for vector in vectors]

    # -- introspection -----------------------------------------------------

    async def count_tokens(self, text: str, *, model: str) -> int:
        """Estimate by characters.

        Ollama exposes no tokeniser endpoint. Rather than pull in a tokeniser per model
        family and pretend to precision we do not have, this over-estimates slightly; the
        real counts come back in `usage` after every call and are what budgets are
        reconciled against.
        """
        return max(1, (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN)

    async def list_models(self) -> Sequence[ModelInfo]:
        data = await self._get_json("/api/tags")
        return [
            ModelInfo(
                name=str(entry.get("name", "")),
                size_bytes=entry.get("size"),
                family=(entry.get("details") or {}).get("family"),
                parameter_size=(entry.get("details") or {}).get("parameter_size"),
                quantization=(entry.get("details") or {}).get("quantization_level"),
            )
            for entry in data.get("models", [])
        ]

    async def health(self, *, expected: Sequence[str] = ()) -> ProviderHealth:
        """Probe. Never raises — an unavailable backend is a result, not an exception."""
        started = time.monotonic()
        try:
            version = await self._get_json("/api/version", timeout=5.0)
            models = await self.list_models()
        except Exception as exc:
            return ProviderHealth(
                available=False,
                endpoint=self._endpoint,
                error=f"{type(exc).__name__}: {exc}",
                latency_ms=round((time.monotonic() - started) * 1000, 2),
            )

        installed = tuple(model.name for model in models)
        missing = tuple(name for name in expected if name and not _installed(name, installed))
        return ProviderHealth(
            available=True,
            endpoint=self._endpoint,
            installed=installed,
            missing=missing,
            latency_ms=round((time.monotonic() - started) * 1000, 2),
            version=str(version.get("version")) if version else None,
        )

    # -- HTTP --------------------------------------------------------------

    def _chat_payload(
        self, request: GenerationRequest, *, model: str, stream: bool
    ) -> dict[str, Any]:
        options: dict[str, Any] = {
            "temperature": request.temperature,
            "num_predict": request.max_tokens,
        }
        if request.top_p is not None:
            options["top_p"] = request.top_p
        if request.stop:
            options["stop"] = list(request.stop)
        if request.seed is not None:
            options["seed"] = request.seed

        payload: dict[str, Any] = {
            "model": model,
            "messages": [_message(message) for message in request.messages],
            "stream": stream,
            "options": options,
        }
        if request.json_schema is not None:
            # Ollama accepts a JSON Schema in `format`, which constrains decoding rather
            # than merely asking politely. The gateway still validates the result.
            payload["format"] = dict(request.json_schema)
        return payload

    async def _post_json(
        self, path: str, payload: Mapping[str, Any], *, timeout: float
    ) -> Mapping[str, Any]:
        try:
            response = await self._client.post(path, json=payload, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise GenerationTimeoutError(
                f"Ollama did not respond within {timeout:g}s", path=path
            ) from exc
        except httpx.HTTPError as exc:
            raise ModelUnavailableError(
                f"cannot reach Ollama at {self._endpoint}: {exc}", endpoint=self._endpoint
            ) from exc

        if response.status_code >= 400:
            raise self._http_error(response, str(payload.get("model", "")))
        return dict(response.json())

    async def _get_json(self, path: str, *, timeout: float | None = None) -> Mapping[str, Any]:
        try:
            response = await self._client.get(path, timeout=timeout or self._request_timeout)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ModelUnavailableError(
                f"cannot reach Ollama at {self._endpoint}: {exc}", endpoint=self._endpoint
            ) from exc
        return dict(response.json())

    def _http_error(self, response: httpx.Response, model: str) -> ModelUnavailableError:
        detail = response.text[:300]

        if response.status_code == 404:
            # Ollama's 404 for an absent model is the single most common failure a new
            # install hits, so it gets the actionable message rather than a status code.
            error = ModelUnavailableError(
                f"model {model!r} is not installed. Run: ollama pull {model}",
                model=model,
                status=response.status_code,
            )
        else:
            error = ModelUnavailableError(
                f"Ollama returned {response.status_code}: {detail}",
                model=model,
                status=response.status_code,
            )

        # A missing model or a disabled feature will not fix itself in 500 ms. Retrying
        # here only delays the message that tells the user what to actually do.
        if response.status_code in {400, 404, 501} or 405 <= response.status_code < 500:
            error.retryable = False
        return error

    def _usage(self, data: Mapping[str, Any], request: GenerationRequest, text: str) -> TokenUsage:
        prompt = data.get("prompt_eval_count")
        completion = data.get("eval_count")
        if prompt is None:
            prompt = sum(len(message.content) // CHARS_PER_TOKEN for message in request.messages)
        if completion is None:
            completion = len(text) // CHARS_PER_TOKEN
        return TokenUsage(prompt=int(prompt), completion=int(completion))


def _message(message: Message) -> dict[str, str]:
    payload = {"role": message.role.value, "content": message.content}
    if message.name:
        payload["name"] = message.name
    return payload


def _installed(wanted: str, installed: Sequence[str]) -> bool:
    """`llama3.1:8b` matches exactly; a bare `llama3.1` matches any tag of that model."""
    if wanted in installed:
        return True
    if ":" in wanted:
        return False
    return any(name.split(":")[0] == wanted for name in installed)


def _finish_reason(raw: Any) -> FinishReason:
    match str(raw or "stop"):
        case "length":
            return FinishReason.LENGTH
        case "stop":
            return FinishReason.STOP
        case _:
            return FinishReason.STOP
