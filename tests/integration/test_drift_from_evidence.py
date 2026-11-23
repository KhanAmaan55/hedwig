"""Personality drift from the user's reactions (PER-02, docs/10 §13), through the nightly run."""

from __future__ import annotations

from hedwig.core.clock import FakeClock
from hedwig.core.ports.personality import Trait
from hedwig.wiring import Container


async def _say(container: Container, clock: FakeClock, session: str, *lines: str) -> None:
    for line in lines:
        await container.brain.run_turn(line, session_id=session)
        clock.advance(minutes=5)
    await container.bus.drain()


async def _value(container: Container, trait: Trait) -> float:
    assert container.personality is not None
    return (await container.personality.profile()).value(trait)


async def test_reactions_over_two_days_move_a_trait_a_little_and_say_why(
    running_container: Container, clock: FakeClock
) -> None:
    before = await _value(running_container, Trait.VERBOSITY)
    await _say(
        running_container, clock, "a", "Tell me about tides.", "Wow, that was a wall of text."
    )
    clock.advance(days=1)
    await _say(
        running_container,
        clock,
        "b",
        "And the moon?",
        "Honestly you gave me way more than I needed.",
        "Your answers are getting long.",
    )

    run = await running_container.nightly.run(force=True)

    drift = run["stats"]["drift"]
    assert drift["observed"] == 3 and drift["changed"] == ["verbosity-0.020"]
    after = await _value(running_container, Trait.VERBOSITY)
    assert abs(after - (before - 0.02)) < 1e-9, "the weekly budget, not the per-change cap"
    engine = running_container.persona_control
    assert engine is not None
    assert len(await engine.history(trait=Trait.VERBOSITY)) == 1, "one change, revertable"
    (proposal,) = [
        p
        for p in await running_container.personality.proposals()  # type: ignore[union-attr]
        if p["source"] == "inferred"
    ]
    assert "wall of text" in str(proposal["rationale"]) and proposal["evidence_count"] == 3

    again = await running_container.nightly.run(force=True)
    assert again["stats"]["drift"]["scanned"] == 0, "messages were read twice"
    assert again["stats"]["drift"]["changed"] == [], "evidence was spent twice"


async def test_one_days_grumbling_is_not_enough(
    running_container: Container, clock: FakeClock
) -> None:
    before = await _value(running_container, Trait.VERBOSITY)
    await _say(
        running_container,
        clock,
        "a",
        "That was a wall of text.",
        "You ramble.",
        "You wrote me an essay again.",
    )
    run = await running_container.nightly.run(force=True)
    assert run["stats"]["drift"]["refused"] == ["verbosity:2_evidence"]
    assert await _value(running_container, Trait.VERBOSITY) == before


async def test_what_is_not_about_hedwig_is_not_evidence(
    running_container: Container, clock: FakeClock
) -> None:
    await _say(
        running_container,
        clock,
        "a",
        "My sister is hilarious.",
        "Why are you so formal?",
        "Please be more concise.",  # an instruction: the fast path, not counted again
    )
    run = await running_container.nightly.run(force=True)
    assert run["stats"]["drift"]["observed"] == 0


async def test_mixed_signals_propose_nothing(
    running_container: Container, clock: FakeClock
) -> None:
    for day in range(2):
        await _say(
            running_container,
            clock,
            f"s{day}",
            "You're hilarious.",
            "Your jokes are getting old.",
        )
        clock.advance(days=1)
    run = await running_container.nightly.run(force=True)
    assert run["stats"]["drift"]["changed"] == [] and run["stats"]["drift"]["refused"] == []
