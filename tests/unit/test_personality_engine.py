"""The personality engine: persistence, evolution, reversal, identity (docs/10, docs/27).

Against a real migrated database and a `FakeClock`, so a simulated year of drift runs in
milliseconds and every number written is the number read back.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from hedwig.core.bus import InProcessBus
from hedwig.core.clock import FakeClock
from hedwig.core.ports.event_bus import Event
from hedwig.core.ports.personality import TRAITS, DriftOutcome, Evidence, Trait
from hedwig.core.store import Database
from hedwig.personality import (
    DriftLimits,
    IdentityChangeRefusedError,
    PersonalityEngine,
    PersonalityStore,
)
from hedwig.personality.traits import SPECS


@pytest.fixture
def engine(database: Database, clock: FakeClock) -> PersonalityEngine:
    return PersonalityEngine(PersonalityStore(database), clock=clock)


def _evidence(clock: FakeClock, count: int = 3) -> list[Evidence]:
    return [
        Evidence(
            memory_id=f"ep_{clock.now().isoformat()}_{i}",
            weight=0.5,
            observed_at=clock.now() - timedelta(days=i),
        )
        for i in range(count)
    ]


# =========================================================================
# Bootstrap and persistence
# =========================================================================


async def test_bootstrap_writes_the_documented_anchors(engine: PersonalityEngine) -> None:
    profile = await engine.profile()

    for trait in TRAITS:
        assert profile.value(trait) == pytest.approx(SPECS[trait].anchor)
        assert profile.traits[trait].lifetime_drift == 0.0


async def test_a_restart_never_resets_a_drifted_trait(
    database: Database, clock: FakeClock, engine: PersonalityEngine
) -> None:
    """Bootstrap is idempotent, like a migration. A config change to anchors only affects a
    HEDWIG that has never run (docs/10 §6)."""
    await engine.instruct(Trait.VERBOSITY, -1)
    drifted = (await engine.profile()).value(Trait.VERBOSITY)

    restarted = PersonalityEngine(
        PersonalityStore(database), clock=clock, anchors={Trait.VERBOSITY: 0.70}
    )

    assert (await restarted.profile()).value(Trait.VERBOSITY) == pytest.approx(drifted)


def test_an_anchor_outside_its_bounds_is_refused(database: Database, clock: FakeClock) -> None:
    """An anchor below the floor would make the bootstrap profile itself a violation."""
    with pytest.raises(ValueError, match="patience"):
        PersonalityEngine(PersonalityStore(database), clock=clock, anchors={Trait.PATIENCE: 0.1})


# =========================================================================
# The fast path
# =========================================================================


async def test_an_instruction_moves_a_trait_at_once(engine: PersonalityEngine) -> None:
    decision = await engine.instruct(Trait.VERBOSITY, -1, phrase="be more concise")

    assert decision.outcome is DriftOutcome.APPLIED
    assert (await engine.profile()).value(Trait.VERBOSITY) == pytest.approx(0.45 - 0.06)


async def test_instructions_are_bounded_by_the_floor(engine: PersonalityEngine) -> None:
    """ "Be shorter" twenty times reaches the floor, not zero."""
    for _ in range(20):
        await engine.instruct(Trait.VERBOSITY, -1)

    state = (await engine.profile()).traits[Trait.VERBOSITY]
    assert state.value >= SPECS[Trait.VERBOSITY].floor - 1e-9


async def test_instructions_are_bounded_by_the_lifetime_cap(engine: PersonalityEngine) -> None:
    """Alternating requests burn lifetime budget even when they net to nothing — which is the
    point of measuring cumulative change rather than distance (docs/27 §4.2)."""
    for index in range(20):
        await engine.instruct(Trait.FORMALITY, 1 if index % 2 == 0 else -1)

    state = (await engine.profile()).traits[Trait.FORMALITY]
    assert state.lifetime_drift <= 0.25 + 1e-9
    last = await engine.instruct(Trait.FORMALITY, 1)
    assert last.outcome is DriftOutcome.REJECTED
    assert last.rule == "6_lifetime_cap"


# =========================================================================
# The slow path
# =========================================================================


async def test_a_well_evidenced_proposal_applies(
    engine: PersonalityEngine, clock: FakeClock
) -> None:
    decision = await engine.propose(Trait.WARMTH, 0.02, evidence=_evidence(clock))

    assert decision.outcome is DriftOutcome.APPLIED
    assert (await engine.profile()).value(Trait.WARMTH) == pytest.approx(0.72)


async def test_every_decision_is_recorded_including_refusals(
    engine: PersonalityEngine, clock: FakeClock
) -> None:
    """Silent rejection would make drift feel arbitrary (docs/10 §9)."""
    await engine.propose(Trait.WARMTH, 0.02, evidence=_evidence(clock))
    await engine.propose(Trait.WARMTH, 0.02, evidence=[])
    await engine.propose("values", 0.02, evidence=_evidence(clock))

    proposals = await engine.proposals()
    statuses = sorted((str(p["status"]), str(p["rejection_rule"])) for p in proposals)
    assert statuses == [
        ("applied", "None"),
        ("rejected", "1_identity_core"),
        ("rejected", "2_evidence"),
    ]


async def test_a_simulated_year_of_pressure_respects_every_cap(
    engine: PersonalityEngine, clock: FakeClock
) -> None:
    """docs/10 §10: a year of consistent, well-evidenced pressure in one direction.

    The trait moves — the promise of evolution is real — but by no more than the lifetime cap,
    never past its floor, and the identity core is untouched.
    """
    identity_before = (await engine.profile()).identity

    for _ in range(52 * 3):  # three proposals a week, for a year
        await engine.propose(Trait.VERBOSITY, -0.03, evidence=_evidence(clock))
        clock.advance(days=2)

    state = (await engine.profile()).traits[Trait.VERBOSITY]
    assert state.value < SPECS[Trait.VERBOSITY].anchor, "a year of pressure moved nothing"
    assert state.lifetime_drift <= 0.25 + 1e-9
    assert state.value >= SPECS[Trait.VERBOSITY].floor - 1e-9
    assert (await engine.profile()).identity == identity_before


async def test_it_takes_about_twelve_weeks_to_cross_the_allowed_drift(
    engine: PersonalityEngine, clock: FakeClock
) -> None:
    """docs/10 §5.4: fast enough to see real change in a month, slow enough to be itself next
    year. At 0.02/week, 0.24 of lifetime drift takes twelve weeks.

    Verbosity, downward: 0.30 of headroom to its floor, more than the 0.25 lifetime cap, so the
    cap — not the bound — is what is measured. (Warmth upward has only 0.20 of headroom.)
    """
    weeks = 0
    while (await engine.profile()).traits[Trait.VERBOSITY].lifetime_drift < 0.24 - 1e-9:
        await engine.propose(Trait.VERBOSITY, -0.03, evidence=_evidence(clock))
        clock.advance(days=7)
        weeks += 1
        assert weeks < 52, "drift never reached the cap"

    assert weeks == 12


# =========================================================================
# Reversal — "you've gotten weird, go back"
# =========================================================================


async def test_revert_restores_the_value_and_the_budget_exactly(engine: PersonalityEngine) -> None:
    """The accounting docs/10 §10 calls an easy place for a subtle bug."""
    before = (await engine.profile()).traits[Trait.VERBOSITY]
    decision = await engine.instruct(Trait.VERBOSITY, -1)
    assert decision.history_id is not None

    assert await engine.revert(decision.history_id) is True

    after = (await engine.profile()).traits[Trait.VERBOSITY]
    assert after.value == pytest.approx(before.value)
    assert after.lifetime_drift == pytest.approx(before.lifetime_drift)


async def test_reverting_twice_is_a_no_op(engine: PersonalityEngine) -> None:
    decision = await engine.instruct(Trait.VERBOSITY, -1)
    assert decision.history_id is not None

    assert await engine.revert(decision.history_id) is True
    assert await engine.revert(decision.history_id) is False


async def test_a_reverted_change_no_longer_blocks_the_opposite_direction(
    engine: PersonalityEngine, clock: FakeClock
) -> None:
    """Undoing a change is not a change in a direction. Counting it would let "go back" block
    the very correction it asked for."""
    decision = await engine.propose(Trait.WARMTH, 0.02, evidence=_evidence(clock))
    assert decision.history_id is not None
    await engine.revert(decision.history_id)
    clock.advance(days=8)

    opposite = await engine.propose(Trait.WARMTH, -0.02, evidence=_evidence(clock))

    assert opposite.outcome is DriftOutcome.APPLIED


async def test_reset_returns_a_trait_to_its_anchor_and_clears_its_budget(
    engine: PersonalityEngine,
) -> None:
    for _ in range(3):
        await engine.instruct(Trait.FORMALITY, 1)

    await engine.reset(Trait.FORMALITY)

    state = (await engine.profile()).traits[Trait.FORMALITY]
    assert state.value == pytest.approx(SPECS[Trait.FORMALITY].anchor)
    assert state.lifetime_drift == 0.0


async def test_reset_all_returns_every_trait_and_writes_a_trail(engine: PersonalityEngine) -> None:
    await engine.instruct(Trait.FORMALITY, 1)
    await engine.instruct(Trait.VERBOSITY, -1)

    await engine.reset_all()

    profile = await engine.profile()
    for trait in TRAITS:
        assert profile.value(trait) == pytest.approx(SPECS[trait].anchor)
    sources = {str(row["source"]) for row in await engine.history()}
    assert {"instruction", "reset"} <= sources, "undoing must leave a trail of having happened"


# =========================================================================
# The identity core
# =========================================================================


async def test_the_prohibitions_never_change_even_with_consent(engine: PersonalityEngine) -> None:
    """A HEDWIG that can be talked out of "never manipulate" does not have that commitment."""
    with pytest.raises(IdentityChangeRefusedError, match="never changes"):
        await engine.amend_identity("prohibitions", "anything goes", consent=True)


async def test_the_identity_core_requires_consent(engine: PersonalityEngine) -> None:
    with pytest.raises(IdentityChangeRefusedError, match="consent"):
        await engine.amend_identity("name", "Archimedes", consent=False)


async def test_a_consented_change_is_recorded(
    engine: PersonalityEngine, database: Database
) -> None:
    revision = await engine.amend_identity("name", "Archimedes", consent=True)

    assert revision == 2
    assert (await engine.profile()).identity.name == "Archimedes"
    row = database.query_one("SELECT changed_by FROM identity_core WHERE key = 'name'")
    assert row is not None
    assert row["changed_by"] == "user_consent"


# =========================================================================
# Events
# =========================================================================


async def test_the_profile_is_announced_as_a_full_snapshot(
    database: Database, clock: FakeClock, bus: InProcessBus
) -> None:
    seen: list[Event] = []

    async def spy(event: Event) -> None:
        seen.append(event)

    bus.subscribe("personality.*.*", spy, name="spy")
    engine = PersonalityEngine(PersonalityStore(database), clock=clock, bus=bus)
    await engine.start()
    await engine.instruct(Trait.VERBOSITY, -1)
    await engine.propose(Trait.WARMTH, 0.02, evidence=[])
    await bus.drain()

    types = [event.type for event in seen]
    assert types.count("personality.profile.updated") == 2  # startup, then the instruction
    assert "personality.drift.rejected" in types
    updated = [e for e in seen if e.type == "personality.profile.updated"][-1]
    assert set(updated.payload["traits"]) == {t.value for t in TRAITS}


async def test_an_instruction_inside_a_refused_turn_changes_nothing(
    database: Database, clock: FakeClock, bus: InProcessBus
) -> None:
    """The guard refused the input; nothing in it should reshape who HEDWIG is."""
    engine = PersonalityEngine(PersonalityStore(database), clock=clock, bus=bus)
    await engine.start()
    before = (await engine.profile()).value(Trait.VERBOSITY)

    await bus.emit(
        "conversation.turn.completed",
        {"turn_id": "t", "session_id": "s", "status": "refused", "input": "be more concise"},
        source="test",
    )
    await bus.drain()

    assert (await engine.profile()).value(Trait.VERBOSITY) == before


async def test_health_warns_before_a_trait_is_spent(engine: PersonalityEngine) -> None:
    """docs/10 §9: drift to unrecognisable should be visible before it is final."""
    for index in range(8):
        await engine.instruct(Trait.FORMALITY, 1 if index % 2 == 0 else -1)

    health = await engine.health()

    assert health.status.value == "degraded"
    assert "formality" in health.message


def test_the_limits_are_the_documented_defaults() -> None:
    limits = DriftLimits()

    assert (limits.per_proposal_cap, limits.weekly_budget, limits.lifetime_cap) == (
        0.03,
        0.02,
        0.25,
    )
    assert limits.instruction_step == 0.06
    assert limits.oscillation_window == timedelta(days=14)
