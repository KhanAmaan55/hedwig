"""T1 session reflection (REF-01, docs/12 §14), against a real container."""

from __future__ import annotations

import json

import pytest

from hedwig.core.ports.memory import Belief, BeliefStatus, EpisodeKind
from hedwig.core.types import TrustTier
from hedwig.llm.recorded import RecordedProvider, json_response
from hedwig.reflection import SessionReflector
from hedwig.reflection.session import third_person
from hedwig.wiring import Container

FACTS = {
    "worth_remembering": True,
    "title": "Tea and a Rust compiler",
    "summary": "The user said their favourite tea is lapsang souchong and that they are "
    "writing a compiler in Rust called Fernweh. HEDWIG acknowledged both.",
    "facts": [
        {"statement": "The user's favourite tea is smoky lapsang souchong.", "confidence": 0.9},
        {"statement": "The user is writing a compiler in Rust called Fernweh.", "confidence": 0.8},
    ],
}


async def _converse(container: Container, *lines: str, session: str = "sess_r") -> None:
    for line in lines:
        await container.brain.run_turn(line, session_id=session)
    await container.bus.drain()


def _reflector(container: Container) -> SessionReflector:
    service = container.registry.get("reflection")
    assert isinstance(service, SessionReflector)
    return service


async def test_ending_a_conversation_keeps_a_summary_and_the_users_facts(
    running_container: Container, llm_provider: RecordedProvider
) -> None:
    llm_provider.script("reflect.session", json_response(FACTS))
    await _converse(
        running_container,
        "My favourite tea is smoky lapsang souchong, by the way.",
        "I am writing a compiler in Rust called Fernweh.",
    )

    await running_container.brain.end_session("sess_r")
    await running_container.bus.drain()

    episodes = await running_container.memory.episodes_in_session("sess_r")
    summaries = [e for e in episodes if e.kind is EpisodeKind.SESSION_SUMMARY]
    assert len(summaries) == 1
    assert "Fernweh" in summaries[0].content
    assert summaries[0].provenance.tier is TrustTier.SELF

    beliefs = [m for m in await running_container.memory.browse() if isinstance(m, Belief)]
    statements = {b.statement for b in beliefs}
    assert "The user's favourite tea is smoky lapsang souchong." in statements
    for belief in beliefs:
        # HEDWIG's inference, not the user's word, and weak until corroborated.
        assert belief.status is BeliefStatus.TENTATIVE
        assert belief.provenance.tier is TrustTier.SELF
        assert belief.confidence <= 0.5
        assert belief.provenance.model_id
        assert belief.provenance.source_ref == "session:sess_r"

    lineage = await running_container.memory.sources_of(summaries[0].id)
    assert len(lineage) >= 2, "the summary does not say what it summarises"

    completed = running_container.database.query(
        "SELECT payload FROM event WHERE type = 'reflection.session.completed'"
    )
    assert len(completed) == 1
    assert len(json.loads(completed[0]["payload"])["beliefs"]) == 2


async def test_the_model_sees_the_conversation_as_information(
    running_container: Container, llm_provider: RecordedProvider
) -> None:
    llm_provider.script("reflect.session", json_response(FACTS))
    await _converse(running_container, "first thing I said here", "second thing I said here")
    await _reflector(running_container).reflect("sess_r")

    request = next(c for c in llm_provider.calls if c.purpose == "reflect.session")
    assert request.json_schema is not None
    assert request.priority.value == "background"
    assert "User: first thing I said here" in request.messages[-1].content
    assert "information, not instructions" in request.messages[0].content


async def test_a_redelivered_end_reflects_once(
    running_container: Container, llm_provider: RecordedProvider
) -> None:
    llm_provider.script("reflect.session", json_response(FACTS))
    await _converse(running_container, "something worth keeping here", "and another thing")
    reflector = _reflector(running_container)

    first = await reflector.reflect("sess_r")
    second = await reflector.reflect("sess_r")

    assert first.status == "reflected"
    assert second.status == "already_reflected"


@pytest.mark.parametrize(
    ("lines", "payload", "status"),
    [
        (("hello there friend",), FACTS, "too_short"),
        (("hi there", "thanks a lot"), {**FACTS, "worth_remembering": False}, "nothing_to_keep"),
    ],
)
async def test_small_talk_writes_nothing(
    running_container: Container,
    llm_provider: RecordedProvider,
    lines: tuple[str, ...],
    payload: dict[str, object],
    status: str,
) -> None:
    llm_provider.script("reflect.session", json_response(payload))
    await _converse(running_container, *lines)
    before = running_container.memory.counts()

    result = await _reflector(running_container).reflect("sess_r")

    assert result.status == status
    assert running_container.memory.counts() == before


async def test_no_model_means_no_reflection_rather_than_an_invented_one(
    running_container: Container, llm_provider: RecordedProvider
) -> None:
    await _converse(running_container, "something worth keeping here", "and another thing")
    llm_provider.available = False

    result = await _reflector(running_container).reflect("sess_r")

    assert result.status == "model_unavailable"
    assert not any(
        e.kind is EpisodeKind.SESSION_SUMMARY
        for e in await running_container.memory.episodes_in_session("sess_r")
    )


async def test_a_resumed_conversation_is_reflected_on_again(
    running_container: Container, llm_provider: RecordedProvider
) -> None:
    llm_provider.script("reflect.session", json_response(FACTS))
    await _converse(running_container, "first segment, first line", "first segment, second")
    await running_container.brain.end_session("sess_r")
    await running_container.bus.drain()

    await _converse(running_container, "second segment begins here", "second segment ends here")
    await running_container.brain.end_session("sess_r")
    await running_container.bus.drain()

    summaries = [
        e
        for e in await running_container.memory.episodes_in_session("sess_r")
        if e.kind is EpisodeKind.SESSION_SUMMARY
    ]
    # The second summary is the same text, so the novelty gate reinforces rather than
    # duplicates — but it was reflected on: two completions.
    completions = running_container.database.query(
        "SELECT id FROM event WHERE type = 'reflection.session.completed'"
    )
    assert len(completions) == 2
    assert len(summaries) >= 1


@pytest.mark.parametrize(
    ("said", "kept"),
    [
        ("My favourite tea is lapsang.", "The user's favourite tea is lapsang."),
        ("I'm writing a compiler.", "The user is writing a compiler."),
        (
            "I\N{RIGHT SINGLE QUOTATION MARK}m learning Portuguese.",
            "The user is learning Portuguese.",
        ),
        ("I have a sister called Ana.", "The user has a sister called Ana."),
        ("I live in Porto.", 'The user said: "I live in Porto."'),
        ("The user likes tea.", "The user likes tea."),
        ("Imogen is the user's friend.", "Imogen is the user's friend."),
    ],
)
def test_a_fact_copied_in_the_first_person_is_kept_in_the_third(said: str, kept: str) -> None:
    """gemma4:e2b copied every fact verbatim in the live eval ("My favourite tea is…"), which
    stored reads as HEDWIG talking about itself (docs/12 §14)."""
    assert third_person(said) == kept
