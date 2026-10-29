"""Personality, relationship and the prompt, through a real turn (docs/27).

A real graph, bus, database and scheduler-free container, with the recorded provider standing
in for the model. `llm_provider.calls` is the evidence: it holds exactly what the model was
sent, so "the LLM receives personality context from this engine" is a statement about bytes,
not about intent.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from hedwig.core.clock import FakeClock
from hedwig.core.ports.llm import Role
from hedwig.core.ports.personality import Trait
from hedwig.llm.errors import ModelUnavailableError
from hedwig.llm.recorded import RecordedProvider, ScriptedResponse
from hedwig.llm.responder import PURPOSE
from hedwig.wiring import Container


@pytest.fixture
async def hedwig(running_container: Container) -> Container:
    return running_container


def _personality(container: Container) -> Any:
    assert container.personality is not None
    return container.personality


def _system(provider: RecordedProvider, index: int = -1) -> str:
    request = [r for r in provider.calls if r.purpose == PURPOSE][index]
    assert request.messages[0].role is Role.SYSTEM
    return request.messages[0].content


# =========================================================================
# The requirement
# =========================================================================


async def test_the_model_receives_the_identity_core(
    hedwig: Container, llm_provider: RecordedProvider
) -> None:
    await hedwig.brain.run_turn("hello", session_id="s1")

    system = _system(llm_provider)
    assert "You are HEDWIG" in system
    assert "Never claim feelings are real" in system


async def test_the_model_receives_the_rendered_personality(
    hedwig: Container, llm_provider: RecordedProvider
) -> None:
    """The anchors render to known directives; those exact sentences must reach the model."""
    from hedwig.personality import trait_directives

    await hedwig.brain.run_turn("hello", session_id="s2")

    system = _system(llm_provider)
    for directive in trait_directives(await _personality(hedwig).profile()):
        assert directive in system, f"{directive!r} never reached the model"


async def test_the_personality_sets_the_generation_parameters(
    hedwig: Container, llm_provider: RecordedProvider
) -> None:
    """temperature = 0.5 + 0.3 * playfulness; max_tokens scaled by verbosity (docs/10 §4)."""
    await hedwig.brain.run_turn("hello", session_id="s3")

    request = [r for r in llm_provider.calls if r.purpose == PURPOSE][-1]
    assert request.temperature == pytest.approx(0.5 + 0.3 * 0.40)
    assert request.max_tokens < 1024 * (0.6 + 0.8 * 0.45) + 1


async def test_a_changed_trait_changes_what_the_model_is_told(
    hedwig: Container, llm_provider: RecordedProvider
) -> None:
    """The point of persistence: the next turn speaks differently."""
    await hedwig.brain.run_turn("hello", session_id="s4")
    before = _system(llm_provider)

    for _ in range(3):
        await _personality(hedwig).instruct(Trait.FORMALITY, +1)
    await hedwig.brain.run_turn("hello again", session_id="s4")
    after = _system(llm_provider)

    # Three steps (0.35 -> 0.53) cross no band; the lean is what makes them visible.
    assert "Be a little more formal than usual." not in before
    assert "Be a little more formal than usual." in after


async def test_the_reply_is_the_models_reply(
    hedwig: Container, llm_provider: RecordedProvider
) -> None:
    llm_provider.script(PURPOSE, "Hello — good to see you.")

    final = await hedwig.brain.run_turn("hello", session_id="s5")

    assert final["reply"] == "Hello — good to see you."
    assert final["status"] == "completed"


# =========================================================================
# Trait evolution through an actual conversation
# =========================================================================


async def test_an_instruction_is_honoured_in_the_reply_that_hears_it(
    hedwig: Container, llm_provider: RecordedProvider
) -> None:
    """docs/27 §4.3: the acknowledgement is in *this* turn's prompt."""
    await hedwig.brain.run_turn("can you be more concise?", session_id="s6")

    system = _system(llm_provider)
    assert "They just asked you to be more concise" in system


async def test_an_instruction_persists_into_later_turns(
    hedwig: Container, llm_provider: RecordedProvider
) -> None:
    before = (await _personality(hedwig).profile()).value(Trait.VERBOSITY)

    await hedwig.brain.run_turn("can you be more concise?", session_id="s7")
    await hedwig.bus.drain()

    after = (await _personality(hedwig).profile()).value(Trait.VERBOSITY)
    assert after == pytest.approx(before - 0.06)


async def test_an_ordinary_message_changes_nothing(hedwig: Container) -> None:
    before = (await _personality(hedwig).profile()).values()

    await hedwig.brain.run_turn("the report was too long and very formal", session_id="s8")
    await hedwig.bus.drain()

    assert (await _personality(hedwig).profile()).values() == before


async def test_the_personality_survives_a_restart(
    hedwig: Container, config: Any, clock: FakeClock, database: Any, llm_provider: RecordedProvider
) -> None:
    from hedwig.wiring import build_container

    await _personality(hedwig).instruct(Trait.VERBOSITY, -1)
    moved = (await _personality(hedwig).profile()).value(Trait.VERBOSITY)

    again = build_container(
        config, clock=clock, database=database, migrate=False, llm_provider=llm_provider
    )

    assert again.personality is not None
    assert (await again.personality.profile()).value(Trait.VERBOSITY) == pytest.approx(moved)


# =========================================================================
# Emotion follows personality (docs/09 §3)
# =========================================================================


async def test_emotion_idles_where_the_personality_says_it_should(hedwig: Container) -> None:
    """The trait projection, finally live: curiosity 0.80 makes emotion's curiosity baseline
    0.3 + 0.5 * 0.80 = 0.70, not the neutral 0.55."""
    await hedwig.bus.drain()

    engine: Any = hedwig.emotion
    assert engine.baselines.curiosity == pytest.approx(0.70)


# =========================================================================
# The relationship
# =========================================================================


async def test_a_session_ending_moves_the_relationship(hedwig: Container, clock: FakeClock) -> None:
    for text in ("hello", "how are things", "thanks, that helped"):
        await hedwig.brain.run_turn(text, session_id="s9")
    await hedwig.bus.drain()
    before = await hedwig.relationship.with_user()

    clock.advance(minutes=45)
    sessions: Any = hedwig.sessions
    await sessions.end_idle(idle_for=timedelta(minutes=30))
    await hedwig.bus.drain()

    after = await hedwig.relationship.with_user()
    assert after.familiarity > before.familiarity
    assert after.interaction_count == 3


async def test_the_relationship_does_not_move_mid_session(hedwig: Container) -> None:
    """Relationships do not lurch (docs/06 §3.3)."""
    before = await hedwig.relationship.with_user()
    for _ in range(5):
        await hedwig.brain.run_turn("another message", session_id="s10")
    await hedwig.bus.drain()

    assert await hedwig.relationship.with_user() == before


async def test_the_model_is_told_it_is_still_getting_to_know_the_user(
    hedwig: Container, llm_provider: RecordedProvider
) -> None:
    await hedwig.brain.run_turn("hello", session_id="s11")

    assert "still getting to know this person" in _system(llm_provider)


async def test_the_two_trusts_stay_apart(hedwig: Container) -> None:
    """ADR-0020: the mood's trust and the relationship's trust are different fields."""
    final = await hedwig.brain.run_turn("hello", session_id="s12")
    mind = final["mind"]

    assert "trust" in mind.mood and "trust" in mind.relationship
    assert set(mind.mood) != set(mind.relationship)


# =========================================================================
# When the model is not there (docs/07 §8)
# =========================================================================


async def test_an_unreachable_model_ends_the_turn_honestly(
    hedwig: Container, llm_provider: RecordedProvider
) -> None:
    llm_provider.script(PURPOSE, ScriptedResponse(text="", error=ModelUnavailableError("down")))

    final = await hedwig.brain.run_turn("are you there?", session_id="s13")

    assert final["status"] == "failed"
    assert "can't reach my language model" in final["reply"]
    assert final["failure_reason"]


async def test_a_failed_turn_is_recorded_but_not_remembered(
    hedwig: Container, llm_provider: RecordedProvider
) -> None:
    """Memory does not capture a failed turn — already true, so the failure is coherent across
    the system without anyone special-casing it."""
    llm_provider.script(PURPOSE, ScriptedResponse(text="", error=ModelUnavailableError("down")))

    await hedwig.brain.run_turn("Please remember my passport expires in May.", session_id="s14")
    await hedwig.bus.drain()

    turn = hedwig.database.query_one("SELECT status FROM turn WHERE session_id = 's14'")
    assert turn is not None
    assert turn["status"] == "failed"
    assert hedwig.memory.counts()["episodes"] == 0
