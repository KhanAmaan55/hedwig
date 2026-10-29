"""Relationship memory (docs/06 §3.3, docs/27 §5).

The update rule is pure and gets pure tests. The memory gets a real database, because what it
is for is accumulating durably across a session and folding in exactly once.
"""

from __future__ import annotations

import pytest

from hedwig.core.clock import FakeClock
from hedwig.core.ports.emotion import EmotionState
from hedwig.core.ports.personality import RelationshipState
from hedwig.core.store import Database
from hedwig.memory import USER_ENTITY_ID, RelationshipMemory, SessionTally, updated
from hedwig.sessions import SqliteSessionStore

STRANGER = RelationshipState(entity_id="e")


# =========================================================================
# The update rule
# =========================================================================


def test_familiarity_grows_with_interaction() -> None:
    after = updated(STRANGER, tally=SessionTally(turns=5))

    assert after.familiarity == pytest.approx(0.05)


def test_one_exchange_is_acquaintance_not_familiarity() -> None:
    """A single turn counts for a fifth of a full session."""
    short = updated(STRANGER, tally=SessionTally(turns=1))
    full = updated(STRANGER, tally=SessionTally(turns=5))

    assert short.familiarity == pytest.approx(full.familiarity / 5)


def test_familiarity_approaches_one_but_never_passes_it() -> None:
    state = STRANGER
    for _ in range(500):
        state = updated(state, tally=SessionTally(turns=10))

    assert 0.99 < state.familiarity <= 1.0


def test_affinity_moves_toward_the_sessions_valence() -> None:
    warm = updated(STRANGER, tally=SessionTally(turns=3, valence_sum=1.5, valence_count=3))
    cold = updated(STRANGER, tally=SessionTally(turns=3, valence_sum=-1.5, valence_count=3))

    assert warm.affinity == pytest.approx(0.08 * 0.5)
    assert cold.affinity == pytest.approx(-0.08 * 0.5)


def test_a_session_with_no_recorded_mood_leaves_affinity_alone() -> None:
    """Absent is not neutral. Feeding 0 for a missing mood would drag affinity toward a number
    nobody measured."""
    state = RelationshipState(entity_id="e", affinity=0.6)

    assert updated(state, tally=SessionTally(turns=4)).affinity == 0.6


def test_trust_does_not_move_without_a_reliability_signal() -> None:
    """Its documented input — corroboration — does not exist yet (docs/27 §5)."""
    state = RelationshipState(entity_id="e", trust=0.5)

    for _ in range(100):
        state = updated(state, tally=SessionTally(turns=10, valence_sum=10, valence_count=10))

    assert state.trust == 0.5


def test_trust_moves_slowest_when_a_signal_exists() -> None:
    """k_t = 0.03, the smallest rate: trust is the dimension that should be hardest to move."""
    after = updated(
        STRANGER, tally=SessionTally(turns=5, valence_sum=5, valence_count=5), reliability=1.0
    )

    assert after.trust - STRANGER.trust == pytest.approx(0.03 * 0.5)
    assert after.trust - STRANGER.trust < after.familiarity - STRANGER.familiarity


def test_an_empty_session_changes_nothing() -> None:
    assert updated(STRANGER, tally=SessionTally(turns=0)) == STRANGER


def test_a_long_bad_run_is_bounded() -> None:
    state = STRANGER
    for _ in range(1000):
        state = updated(
            state, tally=SessionTally(turns=10, valence_sum=-10, valence_count=10), reliability=0.0
        )

    assert state.affinity >= -1.0 and state.trust >= 0.0


# =========================================================================
# The memory
# =========================================================================


@pytest.fixture
async def relationship(database: Database, clock: FakeClock) -> RelationshipMemory:
    await SqliteSessionStore(database, clock=clock).open("sess_1")
    return RelationshipMemory(database, clock=clock)


async def test_the_user_exists_as_an_entity_from_the_start(
    relationship: RelationshipMemory, database: Database
) -> None:
    row = database.query_one("SELECT kind FROM entity WHERE id = ?", (USER_ENTITY_ID,))

    assert row is not None
    assert row["kind"] == "person"


async def test_a_new_relationship_starts_as_strangers(relationship: RelationshipMemory) -> None:
    state = await relationship.with_user()

    assert (state.familiarity, state.affinity, state.trust) == (0.0, 0.0, 0.5)


async def test_turns_accumulate_without_changing_the_relationship(
    relationship: RelationshipMemory,
) -> None:
    """Relationships do not lurch: nothing moves until the session ends."""
    for _ in range(4):
        await relationship.record_turn("sess_1", mood=EmotionState().as_dict())

    assert relationship.tally("sess_1").turns == 4
    assert (await relationship.with_user()).familiarity == 0.0


async def test_closing_a_session_folds_it_in(relationship: RelationshipMemory) -> None:
    for _ in range(5):
        await relationship.record_turn(
            "sess_1", mood=EmotionState(happiness=0.9, stress=0.0).as_dict()
        )

    after = await relationship.close_session("sess_1")

    assert after is not None
    assert after.familiarity > 0
    assert after.affinity > 0
    assert after.interaction_count == 5


async def test_a_session_is_folded_in_exactly_once(relationship: RelationshipMemory) -> None:
    """A redelivered `session.ended` must not count the conversation twice."""
    await relationship.record_turn("sess_1", mood=None)
    once = await relationship.close_session("sess_1")
    twice = await relationship.close_session("sess_1")

    assert once is not None
    assert twice is None
    assert (await relationship.with_user()).interaction_count == 1


async def test_valence_comes_from_the_one_definition_of_valence(
    relationship: RelationshipMemory,
) -> None:
    """Relationship and emotion must not disagree about what a mood was (docs/09 §3.2)."""
    mood = EmotionState(happiness=0.8, stress=0.1)
    await relationship.record_turn("sess_1", mood=mood.as_dict())

    tally = relationship.tally("sess_1")
    assert tally.mean_valence == pytest.approx(mood.valence)


async def test_the_tally_survives_a_restart(database: Database, clock: FakeClock) -> None:
    await SqliteSessionStore(database, clock=clock).open("sess_1")
    first = RelationshipMemory(database, clock=clock)
    await first.record_turn("sess_1", mood=None)

    second = RelationshipMemory(database, clock=clock)

    assert second.tally("sess_1").turns == 1


# =========================================================================
# Sessions end — the update's trigger
# =========================================================================


async def test_an_idle_session_ends(database: Database, clock: FakeClock) -> None:
    from datetime import timedelta

    sessions = SqliteSessionStore(database, clock=clock)
    await sessions.open("sess_idle")
    clock.advance(minutes=31)

    ended = await sessions.end_idle(idle_for=timedelta(minutes=30))

    assert ended == ["sess_idle"]
    row = database.query_one("SELECT end_reason FROM session WHERE id = 'sess_idle'")
    assert row is not None
    assert row["end_reason"] == "timeout"


async def test_an_active_session_does_not_end(database: Database, clock: FakeClock) -> None:
    from datetime import timedelta

    sessions = SqliteSessionStore(database, clock=clock)
    await sessions.open("sess_busy")
    clock.advance(minutes=10)

    assert await sessions.end_idle(idle_for=timedelta(minutes=30)) == []


async def test_a_resumed_session_is_a_new_conversation(
    database: Database, clock: FakeClock
) -> None:
    """Otherwise a long-lived session id would update the relationship once and never again."""
    from datetime import timedelta

    sessions = SqliteSessionStore(database, clock=clock)
    assert await sessions.open("sess_1") is True
    clock.advance(hours=1)
    await sessions.end_idle(idle_for=timedelta(minutes=30))

    assert await sessions.open("sess_1") is True
    assert await sessions.open("sess_1") is False
