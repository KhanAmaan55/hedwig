"""The language model gateway.

Uses `RecordedProvider`, so every path — routing, retries, escalation, metrics — is
deterministic and runs in milliseconds. The provider's own HTTP behaviour is covered in
tests/unit/test_ollama_provider.py.
"""

from __future__ import annotations

import pytest

from hedwig.core.bus import InProcessBus
from hedwig.core.clock import FakeClock
from hedwig.core.config import LlmConfig
from hedwig.core.errors import InvalidRequestError, NotFoundError
from hedwig.core.ports import (
    Event,
    GenerationRequest,
    HealthStatus,
    Message,
    ModelTier,
    Role,
)
from hedwig.core.store import Database
from hedwig.llm import (
    GenerationTimeoutError,
    LanguageModelGateway,
    ModelUnavailableError,
    RecordedProvider,
    SchemaValidationError,
    ScriptedResponse,
    json_response,
    request,
)

SCHEMA = {
    "type": "object",
    "properties": {
        "worth_remembering": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["worth_remembering", "confidence"],
}


@pytest.fixture
def provider() -> RecordedProvider:
    return RecordedProvider(default=ScriptedResponse(text="a reply"))


@pytest.fixture
def llm_config() -> LlmConfig:
    return LlmConfig(provider="recorded", max_attempts=3, retry_base_delay_seconds=0.01)


@pytest.fixture
def gateway(
    provider: RecordedProvider, llm_config: LlmConfig, clock: FakeClock
) -> LanguageModelGateway:
    return LanguageModelGateway(provider, config=llm_config, clock=clock)


# -- routing ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("purpose", "expected"),
    [
        ("compose.reply", ModelTier.CONVERSATIONAL),
        ("compose", ModelTier.CONVERSATIONAL),
        ("plan.queries", ModelTier.UTILITY),
        ("reflect.summarise", ModelTier.UTILITY),
        ("emotion.appraise", ModelTier.UTILITY),
        ("embed.memory", ModelTier.EMBEDDING),
        ("something.unknown", ModelTier.UTILITY),
    ],
)
def test_purpose_routes_to_a_tier(
    gateway: LanguageModelGateway, purpose: str, expected: ModelTier
) -> None:
    """Callers name a purpose, not a model, so the cost model is one table (docs/14 §3.1)."""
    assert gateway.tier_for(purpose) is expected


def test_an_unknown_purpose_gets_the_cheap_model(gateway: LanguageModelGateway) -> None:
    """Being wrong in the cheap direction is recoverable; the reverse ruins a laptop."""
    assert gateway.model_for("nonsense.purpose") == gateway.model_for(ModelTier.UTILITY)


async def test_the_routed_model_is_the_one_called(
    gateway: LanguageModelGateway, provider: RecordedProvider
) -> None:
    result = await gateway.generate(request("hi", purpose="compose.reply"))
    assert result.model == "llama3.1:8b"

    result = await gateway.generate(request("hi", purpose="reflect.summarise"))
    assert result.model == "qwen2.5:3b"


async def test_an_explicit_model_overrides_routing(gateway: LanguageModelGateway) -> None:
    result = await gateway.generate(
        request("hi", purpose="compose.reply", model="experimental:70b")
    )
    assert result.model == "experimental:70b"


# -- structured results ----------------------------------------------------


async def test_the_result_carries_everything_needed_to_explain_it(
    gateway: LanguageModelGateway,
) -> None:
    result = await gateway.generate(request("hi", purpose="plan.queries"))

    assert result.text == "a reply"
    assert result.tier is ModelTier.UTILITY
    assert result.purpose == "plan.queries"
    assert result.usage.total > 0
    assert result.attempts == 1
    assert result.escalated is False


# -- streaming -------------------------------------------------------------


async def test_streaming_yields_pieces_and_a_final_result(
    gateway: LanguageModelGateway, provider: RecordedProvider
) -> None:
    provider.script("compose.reply", "one two three")

    chunks = [chunk async for chunk in gateway.stream(request("hi", purpose="compose.reply"))]

    assert "".join(chunk.text for chunk in chunks) == "one two three"
    assert chunks[-1].done is True
    assert chunks[-1].result is not None
    assert chunks[-1].result.text == "one two three"


async def test_structured_output_cannot_be_streamed(gateway: LanguageModelGateway) -> None:
    """A schema can only be checked once the whole document has arrived."""
    with pytest.raises(InvalidRequestError, match="cannot be streamed"):
        async for _ in gateway.stream(request("hi", purpose="plan.queries", json_schema=SCHEMA)):
            pass


async def test_streaming_retries_before_the_first_token(
    gateway: LanguageModelGateway, provider: RecordedProvider
) -> None:
    provider.script(
        "compose.reply",
        ScriptedResponse(text="", error=ModelUnavailableError("warming up")),
        ScriptedResponse(text="recovered"),
    )

    chunks = [chunk async for chunk in gateway.stream(request("hi", purpose="compose.reply"))]

    assert "".join(chunk.text for chunk in chunks) == "recovered"


# -- retries ---------------------------------------------------------------


async def test_transient_failures_are_retried(
    gateway: LanguageModelGateway, provider: RecordedProvider
) -> None:
    provider.script(
        "plan.queries",
        ScriptedResponse(text="", error=ModelUnavailableError("still loading")),
        ScriptedResponse(text="", error=GenerationTimeoutError("slow")),
        ScriptedResponse(text="third time lucky"),
    )

    result = await gateway.generate(request("hi", purpose="plan.queries"))

    assert result.text == "third time lucky"
    assert result.attempts == 3
    assert gateway.metrics.snapshot()["retries"] == 2


async def test_retries_are_bounded(
    gateway: LanguageModelGateway, provider: RecordedProvider
) -> None:
    provider.script("plan.queries", ScriptedResponse(text="", error=ModelUnavailableError("down")))

    with pytest.raises(ModelUnavailableError):
        await gateway.generate(request("hi", purpose="plan.queries"))

    assert len(provider.calls) == 3  # max_attempts


async def test_a_non_transient_failure_is_not_retried(
    gateway: LanguageModelGateway, provider: RecordedProvider
) -> None:
    provider.script(
        "plan.queries", ScriptedResponse(text="", error=InvalidRequestError("bad prompt"))
    )

    with pytest.raises(InvalidRequestError):
        await gateway.generate(request("hi", purpose="plan.queries"))

    assert len(provider.calls) == 1


async def test_backoff_uses_the_injected_clock(
    provider: RecordedProvider, llm_config: LlmConfig
) -> None:
    """Retries must not make the suite slow, which means they must not use real time."""
    clock = FakeClock()
    gateway = LanguageModelGateway(provider, config=llm_config, clock=clock)
    provider.script(
        "plan.queries",
        ScriptedResponse(text="", error=ModelUnavailableError("down")),
        ScriptedResponse(text="ok"),
    )

    await gateway.generate(request("hi", purpose="plan.queries"))

    assert clock.monotonic() > 0  # time advanced without anyone waiting


# -- structured output -----------------------------------------------------


async def test_valid_json_is_parsed_and_returned(
    gateway: LanguageModelGateway, provider: RecordedProvider
) -> None:
    provider.script(
        "extract.capture", json_response({"worth_remembering": True, "confidence": 0.8})
    )

    result = await gateway.generate(
        request("extract", purpose="extract.capture", json_schema=SCHEMA)
    )

    assert result.parsed == {"worth_remembering": True, "confidence": 0.8}


async def test_json_wrapped_in_prose_is_repaired(
    gateway: LanguageModelGateway, provider: RecordedProvider
) -> None:
    """The commonest small-model failure is valid JSON with a helpful sentence around it."""
    provider.script(
        "extract.capture",
        'Sure! Here you go:\n{"worth_remembering": false, "confidence": 0.2}\nHope that helps.',
    )

    result = await gateway.generate(
        request("extract", purpose="extract.capture", json_schema=SCHEMA)
    )

    assert result.parsed == {"worth_remembering": False, "confidence": 0.2}
    assert result.attempts == 1  # repaired without another call


async def test_fenced_json_is_repaired(
    gateway: LanguageModelGateway, provider: RecordedProvider
) -> None:
    provider.script(
        "extract.capture",
        '```json\n{"worth_remembering": true, "confidence": 0.5}\n```',
    )

    result = await gateway.generate(
        request("extract", purpose="extract.capture", json_schema=SCHEMA)
    )

    assert result.parsed is not None
    assert result.parsed["worth_remembering"] is True


async def test_invalid_output_is_retried_with_the_errors_fed_back(
    gateway: LanguageModelGateway, provider: RecordedProvider
) -> None:
    provider.script(
        "extract.capture",
        json_response({"worth_remembering": "yes"}),  # wrong type, missing field
        json_response({"worth_remembering": True, "confidence": 0.9}),
    )

    result = await gateway.generate(
        request("extract", purpose="extract.capture", json_schema=SCHEMA)
    )

    assert result.parsed == {"worth_remembering": True, "confidence": 0.9}
    # The correction names the broken fields; a model told what it got wrong usually fixes it.
    correction = provider.calls[-1].messages[-1].content
    assert "worth_remembering" in correction or "confidence" in correction


async def test_persistent_failure_escalates_to_the_larger_tier(
    gateway: LanguageModelGateway, provider: RecordedProvider
) -> None:
    provider.script(
        "extract.capture",
        ScriptedResponse(text="not json at all"),
        ScriptedResponse(text="still not json"),
        json_response({"worth_remembering": True, "confidence": 0.4}),
    )

    result = await gateway.generate(
        request("extract", purpose="extract.capture", json_schema=SCHEMA)
    )

    assert result.escalated is True
    assert result.tier is ModelTier.CONVERSATIONAL
    assert gateway.metrics.snapshot()["escalations"] == 1


async def test_total_failure_raises_so_the_caller_can_use_its_default(
    gateway: LanguageModelGateway, provider: RecordedProvider
) -> None:
    """docs/14 §6: a structured-output failure must never abort a turn."""
    provider.script("extract.capture", ScriptedResponse(text="never valid"))

    with pytest.raises(SchemaValidationError) as caught:
        await gateway.generate(request("extract", purpose="extract.capture", json_schema=SCHEMA))

    assert caught.value.detail["purpose"] == "extract.capture"
    assert gateway.metrics.snapshot()["schema_failures"] >= 2


async def test_structured_calls_are_deterministic(
    gateway: LanguageModelGateway, provider: RecordedProvider
) -> None:
    provider.script(
        "extract.capture", json_response({"worth_remembering": True, "confidence": 0.1})
    )

    await gateway.generate(request("x", purpose="extract.capture", json_schema=SCHEMA))

    assert provider.calls[0].temperature == 0.0
    assert provider.calls[0].seed is not None


# -- model switching -------------------------------------------------------


async def test_switching_a_tier_changes_which_model_is_called(
    gateway: LanguageModelGateway,
) -> None:
    switch = await gateway.switch_model(ModelTier.UTILITY, "qwen2.5:3b", reason="test")

    assert switch.previous == "qwen2.5:3b"
    assert switch.verified is True
    result = await gateway.generate(request("hi", purpose="plan.queries"))
    assert result.model == "qwen2.5:3b"


async def test_switching_to_an_uninstalled_model_is_refused(
    gateway: LanguageModelGateway,
) -> None:
    """A typo would otherwise turn every later call into a 404."""
    with pytest.raises(NotFoundError, match="ollama pull ghost"):
        await gateway.switch_model(ModelTier.CONVERSATIONAL, "ghost")


async def test_verification_can_be_skipped(gateway: LanguageModelGateway) -> None:
    switch = await gateway.switch_model(ModelTier.CONVERSATIONAL, "not-yet-pulled", verify=False)

    assert switch.verified is False
    assert gateway.models()["conversational"] == "not-yet-pulled"


async def test_an_empty_model_name_is_refused(gateway: LanguageModelGateway) -> None:
    with pytest.raises(InvalidRequestError):
        await gateway.switch_model(ModelTier.UTILITY, "  ")


async def test_a_switch_is_announced_on_the_bus(
    provider: RecordedProvider, llm_config: LlmConfig, clock: FakeClock, bus: InProcessBus
) -> None:
    seen: list[Event] = []
    bus.subscribe("llm.*.*", lambda event: _collect(seen, event), name="llm-watch")
    gateway = LanguageModelGateway(provider, config=llm_config, clock=clock, bus=bus)

    await gateway.switch_model(ModelTier.UTILITY, "qwen2.5:3b", reason="test")
    await bus.drain()

    assert [event.type for event in seen] == ["llm.model.switched"]


# -- health ----------------------------------------------------------------


async def test_health_is_ok_when_everything_is_installed(
    gateway: LanguageModelGateway,
) -> None:
    health = await gateway.health()

    assert health.status is HealthStatus.OK
    assert health.detail["models"]["utility"] == "qwen2.5:3b"


async def test_health_is_down_when_the_backend_is_unreachable(
    gateway: LanguageModelGateway, provider: RecordedProvider
) -> None:
    provider.available = False

    health = await gateway.health()

    assert health.status is HealthStatus.DOWN


async def test_health_is_degraded_when_a_model_is_missing(
    provider: RecordedProvider, clock: FakeClock
) -> None:
    """Precise and actionable beats a generic failure."""
    gateway = LanguageModelGateway(
        provider,
        config=LlmConfig(provider="recorded", conversational="not-installed:70b"),
        clock=clock,
    )

    health = await gateway.health()

    assert health.status is HealthStatus.DEGRADED
    assert "not-installed:70b" in health.message


async def test_a_high_schema_failure_rate_degrades_health(
    gateway: LanguageModelGateway, provider: RecordedProvider
) -> None:
    """The earliest signal that a model or a template has degraded (docs/14 §6)."""
    provider.script("extract.capture", ScriptedResponse(text="never valid"))
    with pytest.raises(SchemaValidationError):
        await gateway.generate(request("x", purpose="extract.capture", json_schema=SCHEMA))

    health = await gateway.health()
    assert health.status is HealthStatus.DEGRADED
    assert "schema failure rate" in health.message


async def test_startup_survives_an_unavailable_backend(
    gateway: LanguageModelGateway, provider: RecordedProvider
) -> None:
    """HEDWIG is more useful running and saying so than refusing to boot."""
    provider.available = False
    await gateway.start()  # must not raise
    await gateway.stop()


# -- metrics ---------------------------------------------------------------


async def test_metrics_count_calls_and_tokens(
    gateway: LanguageModelGateway, provider: RecordedProvider
) -> None:
    provider.script("plan.queries", "one", "two")
    await gateway.generate(request("a", purpose="plan.queries"))
    await gateway.generate(request("b", purpose="plan.queries"))

    snapshot = gateway.metrics.snapshot()
    assert snapshot["requests"] == 2
    assert snapshot["tokens_in"] > 0
    assert snapshot["tokens_out"] > 0


async def test_failures_are_counted_by_code(
    gateway: LanguageModelGateway, provider: RecordedProvider
) -> None:
    provider.script("plan.queries", ScriptedResponse(text="", error=ModelUnavailableError("down")))
    with pytest.raises(ModelUnavailableError):
        await gateway.generate(request("x", purpose="plan.queries"))

    assert gateway.metrics.snapshot()["failures"] == 1


async def test_metrics_render_as_prometheus(
    gateway: LanguageModelGateway, provider: RecordedProvider
) -> None:
    await gateway.generate(request("x", purpose="plan.queries"))

    rendered = gateway.metrics.prometheus()

    assert "hedwig_llm_requests_total" in rendered
    assert 'purpose="plan.queries"' in rendered
    assert "hedwig_llm_call_duration_seconds_bucket" in rendered
    assert rendered.endswith("\n")


async def test_calls_are_persisted_for_later_questions(
    provider: RecordedProvider, llm_config: LlmConfig, clock: FakeClock, database: Database
) -> None:
    """'What spent the token budget last night?' is unanswerable without this."""
    gateway = LanguageModelGateway(provider, config=llm_config, clock=clock, database=database)
    await gateway.generate(request("x", purpose="reflect.summarise"))

    row = database.query_one("SELECT purpose, tier, model, status FROM llm_call")
    assert row is not None
    assert row["purpose"] == "reflect.summarise"
    assert row["tier"] == "utility"
    assert row["status"] == "ok"


async def test_failed_calls_are_persisted_too(
    provider: RecordedProvider, llm_config: LlmConfig, clock: FakeClock, database: Database
) -> None:
    gateway = LanguageModelGateway(provider, config=llm_config, clock=clock, database=database)
    provider.script("plan.queries", ScriptedResponse(text="", error=ModelUnavailableError("down")))
    with pytest.raises(ModelUnavailableError):
        await gateway.generate(request("x", purpose="plan.queries"))

    row = database.query_one("SELECT status, error FROM llm_call")
    assert row is not None
    assert row["status"] == "failed"
    assert "ModelUnavailableError" in row["error"]


# -- embeddings ------------------------------------------------------------


async def test_embedding_uses_the_embedding_tier(
    gateway: LanguageModelGateway,
) -> None:
    vectors = await gateway.embed(["hello", "world"])

    assert len(vectors) == 2
    assert len(vectors[0]) == 8


async def test_the_same_text_embeds_identically(gateway: LanguageModelGateway) -> None:
    first = await gateway.embed(["stable"])
    second = await gateway.embed(["stable"])
    assert first == second


# -- budgets ---------------------------------------------------------------


def test_the_per_request_token_cap_is_enforced(
    provider: RecordedProvider, clock: FakeClock
) -> None:
    from hedwig.llm import BudgetExceededError

    gateway = LanguageModelGateway(
        provider,
        config=LlmConfig(provider="recorded", max_request_tokens=100),
        clock=clock,
    )

    gateway.check_budget(50)  # fine
    with pytest.raises(BudgetExceededError):
        gateway.check_budget(101)


# -- convenience -----------------------------------------------------------


def test_the_request_helper_builds_a_single_turn() -> None:
    built = request("what is this?", purpose="plan.queries", instructions="Be brief.")

    assert [message.role for message in built.messages] == [Role.SYSTEM, Role.USER]
    assert built.messages[0].content == "Be brief."
    assert built.purpose == "plan.queries"


def test_requests_are_immutable() -> None:
    import dataclasses

    built = GenerationRequest(
        messages=[Message(role=Role.USER, content="x")], purpose="plan.queries"
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        built.purpose = "other"  # type: ignore[misc]


async def _collect(sink: list[Event], event: Event) -> None:
    sink.append(event)


# -- retryability is a property of the error, not its class ----------------


async def test_a_permanent_failure_is_not_retried_even_though_its_class_is(
    gateway: LanguageModelGateway, provider: RecordedProvider
) -> None:
    """A 404 for a missing model and a 503 for a warming daemon are the same class; only
    one of them is worth waiting for."""
    permanent = ModelUnavailableError("model 'ghost' is not installed")
    permanent.retryable = False
    provider.script("plan.queries", ScriptedResponse(text="", error=permanent))

    with pytest.raises(ModelUnavailableError):
        await gateway.generate(request("x", purpose="plan.queries"))

    assert len(provider.calls) == 1


async def test_embedding_failures_respect_retryability(
    gateway: LanguageModelGateway, provider: RecordedProvider
) -> None:
    provider.available = False

    with pytest.raises(ModelUnavailableError):
        await gateway.embed(["text"])
