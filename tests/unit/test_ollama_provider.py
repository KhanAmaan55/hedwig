"""The Ollama provider.

Driven through `httpx.MockTransport`, so the real request-building, NDJSON stream parsing
and error translation are exercised without a running daemon or an 8 GB model. What is
faked is the network, not our code.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator

import httpx
import pytest

from hedwig.core.ports import (
    FinishReason,
    GenerationRequest,
    Message,
    ModelTier,
    Role,
)
from hedwig.llm.errors import GenerationTimeoutError, ModelUnavailableError, StreamStalledError
from hedwig.llm.ollama import OllamaProvider

Handler = Callable[[httpx.Request], httpx.Response]


def _provider(handler: Handler, **kwargs: object) -> OllamaProvider:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://127.0.0.1:11434"
    )
    return OllamaProvider("http://127.0.0.1:11434", client=client, **kwargs)  # type: ignore[arg-type]


def _request(**kwargs: object) -> GenerationRequest:
    defaults: dict[str, object] = {
        "messages": [Message(role=Role.USER, content="hello")],
        "purpose": "plan.queries",
        "tier": ModelTier.UTILITY,
    }
    return GenerationRequest(**{**defaults, **kwargs})  # type: ignore[arg-type]


def _chat_response(content: str, **extra: object) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "qwen2.5:3b",
            "message": {"role": "assistant", "content": content},
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 12,
            "eval_count": 7,
            **extra,
        },
    )


def _ndjson(chunks: list[dict[str, object]]) -> Iterator[bytes]:
    for chunk in chunks:
        yield (json.dumps(chunk) + "\n").encode()


# -- generation ------------------------------------------------------------


async def test_generate_returns_a_structured_result() -> None:
    provider = _provider(lambda _: _chat_response("a considered answer"))

    result = await provider.generate(_request(), model="qwen2.5:3b")

    assert result.text == "a considered answer"
    assert result.model == "qwen2.5:3b"
    assert result.purpose == "plan.queries"
    assert result.usage.prompt == 12
    assert result.usage.completion == 7
    assert result.usage.total == 19
    assert result.duration_ms >= 0
    assert result.finish_reason is FinishReason.STOP
    await provider.close()


async def test_the_request_carries_the_documented_options() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return _chat_response("ok")

    provider = _provider(handler)
    await provider.generate(
        _request(temperature=0.2, max_tokens=64, stop=("</end>",), seed=7),
        model="qwen2.5:3b",
    )

    assert seen["model"] == "qwen2.5:3b"
    assert seen["stream"] is False
    assert seen["messages"] == [{"role": "user", "content": "hello"}]
    options = seen["options"]
    assert options["temperature"] == 0.2  # type: ignore[index]
    assert options["num_predict"] == 64  # type: ignore[index]
    assert options["stop"] == ["</end>"]  # type: ignore[index]
    assert options["seed"] == 7  # type: ignore[index]
    await provider.close()


async def test_a_json_schema_is_passed_to_the_backend() -> None:
    """Ollama constrains decoding with `format`, which beats asking politely."""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return _chat_response('{"ok": true}')

    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
    provider = _provider(handler)
    await provider.generate(_request(json_schema=schema), model="qwen2.5:3b")

    assert seen["format"] == schema
    await provider.close()


async def test_truncation_is_reported() -> None:
    provider = _provider(lambda _: _chat_response("cut off here", done_reason="length"))

    result = await provider.generate(_request(), model="qwen2.5:3b")

    assert result.finish_reason is FinishReason.LENGTH
    assert result.truncated is True
    await provider.close()


async def test_usage_is_estimated_when_the_backend_omits_it() -> None:
    """A budget that under-counts is worse than one that over-counts."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"model": "m", "message": {"content": "x" * 40}, "done": True}
        )

    provider = _provider(handler)
    result = await provider.generate(_request(), model="m")

    assert result.usage.completion == 10  # 40 chars / 4
    await provider.close()


# -- streaming -------------------------------------------------------------


async def test_stream_yields_pieces_then_a_final_result() -> None:
    chunks: list[dict[str, object]] = [
        {"message": {"content": "Hello"}, "done": False},
        {"message": {"content": " there"}, "done": False},
        {
            "model": "llama3.1:8b",
            "message": {"content": ""},
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 5,
            "eval_count": 2,
        },
    ]
    provider = _provider(lambda _: httpx.Response(200, content=b"".join(_ndjson(chunks))))

    received = [chunk async for chunk in provider.stream(_request(), model="llama3.1:8b")]

    assert [chunk.text for chunk in received[:-1]] == ["Hello", " there"]
    final = received[-1]
    assert final.done is True
    assert final.result is not None
    assert final.result.text == "Hello there"
    assert final.result.usage.completion == 2
    assert final.result.first_token_ms is not None
    await provider.close()


async def test_stream_indexes_chunks_in_order() -> None:
    chunks: list[dict[str, object]] = [
        {"message": {"content": word}, "done": False} for word in ("a", "b", "c")
    ]
    chunks.append({"message": {"content": ""}, "done": True})
    provider = _provider(lambda _: httpx.Response(200, content=b"".join(_ndjson(chunks))))

    received = [chunk async for chunk in provider.stream(_request(), model="m")]

    assert [chunk.index for chunk in received] == [0, 1, 2, 3]
    await provider.close()


async def test_a_stalled_stream_is_distinguished_from_a_slow_one() -> None:
    """The response is partial rather than absent, and the caller may want what arrived."""

    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("no data")

    provider = _provider(handler, stall_timeout=0.01)

    with pytest.raises(StreamStalledError) as caught:
        async for _ in provider.stream(_request(), model="m"):
            pass

    assert caught.value.retryable is True
    await provider.close()


# -- errors ----------------------------------------------------------------


async def test_a_missing_model_gets_an_actionable_message() -> None:
    """The most common failure on a fresh install deserves the fix, not a status code."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "model 'ghost' not found"})

    provider = _provider(handler)

    with pytest.raises(ModelUnavailableError, match="ollama pull ghost"):
        await provider.generate(_request(), model="ghost")
    await provider.close()


async def test_a_connection_failure_becomes_a_typed_error() -> None:
    """Ports never leak provider exceptions (docs/03 §2 rule 5)."""

    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    provider = _provider(handler)

    with pytest.raises(ModelUnavailableError, match="cannot reach Ollama"):
        await provider.generate(_request(), model="m")
    await provider.close()


async def test_a_timeout_becomes_a_typed_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("too slow")

    provider = _provider(handler)

    with pytest.raises(GenerationTimeoutError):
        await provider.generate(_request(), model="m")
    await provider.close()


async def test_a_server_error_includes_the_detail() -> None:
    provider = _provider(lambda _: httpx.Response(500, text="out of memory"))

    with pytest.raises(ModelUnavailableError, match="out of memory"):
        await provider.generate(_request(), model="m")
    await provider.close()


# -- embeddings ------------------------------------------------------------


async def test_embed_returns_one_vector_per_input() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        return httpx.Response(200, json={"embeddings": [[0.1, 0.2]] * len(payload["input"])})

    provider = _provider(handler)
    vectors = await provider.embed(["one", "two"], model="nomic-embed-text")

    assert len(vectors) == 2
    assert vectors[0] == [0.1, 0.2]
    await provider.close()


async def test_embedding_an_empty_list_makes_no_request() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise AssertionError("should not have called the backend")

    provider = _provider(handler)
    assert await provider.embed([], model="m") == []
    await provider.close()


async def test_a_mismatched_embedding_count_is_an_error() -> None:
    provider = _provider(lambda _: httpx.Response(200, json={"embeddings": [[0.1]]}))

    with pytest.raises(ModelUnavailableError, match="1 embeddings for 2 inputs"):
        await provider.embed(["one", "two"], model="m")
    await provider.close()


# -- health ----------------------------------------------------------------


async def test_health_reports_installed_and_missing_models() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.5.1"})
        return httpx.Response(200, json={"models": [{"name": "qwen2.5:3b", "size": 1}]})

    provider = _provider(handler)
    health = await provider.health(expected=["qwen2.5:3b", "llama3.1:8b"])

    assert health.available is True
    assert health.version == "0.5.1"
    assert health.installed == ("qwen2.5:3b",)
    assert health.missing == ("llama3.1:8b",)
    await provider.close()


async def test_health_never_raises_when_the_backend_is_down() -> None:
    """A health endpoint that can fail is not a health endpoint."""

    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nothing listening")

    provider = _provider(handler)
    health = await provider.health(expected=["qwen2.5:3b"])

    assert health.available is False
    assert health.error is not None
    assert health.latency_ms is not None
    await provider.close()


async def test_an_untagged_model_name_matches_any_tag() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.5.1"})
        return httpx.Response(200, json={"models": [{"name": "llama3.1:8b"}]})

    provider = _provider(handler)
    health = await provider.health(expected=["llama3.1"])

    assert health.missing == ()
    await provider.close()


async def test_list_models_carries_the_details() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "llama3.1:8b",
                        "size": 4_700_000_000,
                        "details": {
                            "family": "llama",
                            "parameter_size": "8.0B",
                            "quantization_level": "Q4_0",
                        },
                    }
                ]
            },
        )

    provider = _provider(handler)
    models = await provider.list_models()

    assert models[0].name == "llama3.1:8b"
    assert models[0].family == "llama"
    assert models[0].parameter_size == "8.0B"
    await provider.close()


async def test_token_counting_over_estimates_rather_than_under() -> None:
    provider = _provider(lambda _: _chat_response(""))
    assert await provider.count_tokens("a" * 10, model="m") == 3
    assert await provider.count_tokens("", model="m") == 1
    await provider.close()


# -- reasoning models ------------------------------------------------------


async def test_reasoning_is_kept_separate_from_the_answer() -> None:
    """qwen3 and deepseek-r1 return chain-of-thought in `thinking`. Concatenating it into
    `text` would show the user the model's scratchpad as if it were the reply."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "qwen3:4b",
                "message": {
                    "role": "assistant",
                    "content": "Lisbon.",
                    "thinking": "The user asks about Portugal...",
                },
                "done": True,
            },
        )

    provider = _provider(handler)
    result = await provider.generate(_request(), model="qwen3:4b")

    assert result.text == "Lisbon."
    assert result.reasoning == "The user asks about Portugal..."
    await provider.close()


async def test_a_reasoning_only_response_is_visible_not_silent() -> None:
    """Returning "" with 20 tokens spent looks like a broken model; it is a spent budget."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "message": {"content": "", "thinking": "still working..."},
                "done": True,
                "eval_count": 20,
            },
        )

    provider = _provider(handler)
    result = await provider.generate(_request(max_tokens=20), model="qwen3:4b")

    assert result.text == ""
    assert result.reasoning == "still working..."
    await provider.close()


async def test_streamed_reasoning_is_collected_but_never_yielded() -> None:
    chunks: list[dict[str, object]] = [
        {"message": {"content": "", "thinking": "hmm "}, "done": False},
        {"message": {"content": "Answer", "thinking": "ok"}, "done": False},
        {"message": {"content": ""}, "done": True},
    ]
    provider = _provider(lambda _: httpx.Response(200, content=b"".join(_ndjson(chunks))))

    received = [chunk async for chunk in provider.stream(_request(), model="qwen3:4b")]

    assert "".join(chunk.text for chunk in received) == "Answer"
    assert received[-1].result is not None
    assert received[-1].result.reasoning == "hmm ok"
    await provider.close()


@pytest.mark.parametrize(
    ("status", "retryable"),
    [
        (404, False),  # model not installed — will not fix itself
        (501, False),  # feature disabled on this server
        (400, False),  # malformed request
        (500, True),  # transient server fault
        (503, True),  # daemon still warming up
    ],
)
async def test_permanent_failures_are_marked_non_retryable(status: int, retryable: bool) -> None:
    provider = _provider(lambda _: httpx.Response(status, text="nope"))

    with pytest.raises(ModelUnavailableError) as caught:
        await provider.generate(_request(), model="m")

    assert caught.value.retryable is retryable
    await provider.close()
