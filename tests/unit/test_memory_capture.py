"""Capture and the decay pass (docs/06 §4, §7).

The short-term window moved to `test_sessions.py` with the query itself (docs/26 §4)."""

from __future__ import annotations

import pytest

from hedwig.core.clock import FakeClock
from hedwig.core.ports.memory import Belief, BeliefStatus, Episode, ForgetReason
from hedwig.core.store import Database
from hedwig.core.types import MemoryId, Provenance, SalienceInputs, TrustTier
from hedwig.memory import (
    BeliefCandidate,
    CaptureService,
    EpisodeCandidate,
    MemoryMaintenance,
    SqliteMemoryStore,
    Verdict,
    has_substance,
    similarity,
)


@pytest.fixture
def store(database: Database, clock: FakeClock) -> SqliteMemoryStore:
    return SqliteMemoryStore(database, clock=clock)


@pytest.fixture
def capture(store: SqliteMemoryStore, clock: FakeClock) -> CaptureService:
    return CaptureService(store, clock=clock)


# =========================================================================
# The substance gate
# =========================================================================


@pytest.mark.parametrize("text", ["ok", "thanks", "Thanks!", "sure", "hi", "  ", "yes."])
def test_pure_acknowledgement_is_not_remembered(text: str) -> None:
    """A memory system that stores everything retrieves nothing (docs/06 §4.1)."""
    assert has_substance(text) is False


@pytest.mark.parametrize(
    "text",
    [
        "The user is writing a compiler in Rust",
        "We decided to use reciprocal rank fusion",
        "The user moved to Lisbon last month",
    ],
)
def test_facts_and_decisions_are_remembered(text: str) -> None:
    assert has_substance(text) is True


async def test_an_acknowledgement_candidate_is_dropped(capture: CaptureService) -> None:
    report = await capture.capture(episodes=[EpisodeCandidate(title="ok", content="")])
    assert report.outcomes[0].verdict is Verdict.DROPPED_SUBSTANCE
    assert report.stored == ()


# =========================================================================
# The confidence gate
# =========================================================================


async def test_a_low_confidence_candidate_is_dropped_not_stored_tentatively(
    capture: CaptureService,
) -> None:
    """Garbage tentative beliefs are worse than no beliefs (docs/06 §4.1)."""
    report = await capture.capture(
        beliefs=[BeliefCandidate(statement="The user might possibly like tea", confidence=0.1)]
    )
    assert report.outcomes[0].verdict is Verdict.DROPPED_CONFIDENCE


async def test_a_confident_belief_is_stored_but_still_tentative(
    capture: CaptureService, store: SqliteMemoryStore
) -> None:
    """Everything enters tentative regardless of how sure the extraction was."""
    report = await capture.capture(
        beliefs=[BeliefCandidate(statement="The user lives in Lisbon", confidence=0.95)]
    )

    stored = await store.get_belief(report.stored[0])
    assert stored is not None
    assert stored.status is BeliefStatus.TENTATIVE
    assert stored.confidence <= 0.5  # capped until corroborated


# =========================================================================
# The novelty gate
# =========================================================================


async def test_a_near_duplicate_reinforces_rather_than_duplicating(
    capture: CaptureService, store: SqliteMemoryStore
) -> None:
    """Reinforcing rather than duplicating is what keeps the store clean."""
    first = await capture.capture(
        episodes=[
            EpisodeCandidate(
                title="Compiler work", content="The user is writing a compiler in Rust."
            )
        ]
    )
    original = await store.get_episode(first.stored[0])
    assert original is not None

    second = await capture.capture(
        episodes=[
            EpisodeCandidate(
                title="Compiler work", content="The user is writing a compiler in Rust."
            )
        ]
    )

    assert second.outcomes[0].verdict is Verdict.REINFORCED
    assert second.outcomes[0].memory_id == original.id
    assert store.counts()["episodes"] == 1

    strengthened = await store.get_episode(original.id)
    assert strengthened is not None
    assert strengthened.salience > original.salience


async def test_a_genuinely_different_memory_is_stored(capture: CaptureService) -> None:
    await capture.capture(
        episodes=[EpisodeCandidate(title="A", content="The user is writing a compiler in Rust.")]
    )
    report = await capture.capture(
        episodes=[EpisodeCandidate(title="B", content="The user prefers filter coffee.")]
    )

    assert report.outcomes[0].verdict is Verdict.STORED


def test_similarity_recognises_paraphrase_and_difference() -> None:
    assert similarity("the user writes rust", "the user writes rust") == 1.0
    assert similarity("the user writes rust", "coffee brewing methods") < 0.2
    assert similarity("", "anything") == 0.0


# =========================================================================
# The budget gate
# =========================================================================


async def test_a_turn_cannot_produce_unlimited_memories(
    store: SqliteMemoryStore, clock: FakeClock
) -> None:
    """A turn that seems to yield twelve memories has usually had a bad extraction."""
    capture = CaptureService(store, clock=clock, max_per_turn=2)

    # Genuinely unrelated content, so the novelty gate does not fire first and the budget
    # gate is what is actually under test.
    contents = [
        "The user is writing a compiler in Rust for a hobby project.",
        "Filter coffee is preferred over espresso in the mornings.",
        "A sister named Ana has a birthday sometime in March.",
        "Cycling to work takes roughly twenty five minutes each way.",
        "The garden needs replanting before the autumn rains arrive.",
    ]
    report = await capture.capture(
        episodes=[
            EpisodeCandidate(title=f"Topic {index}", content=content)
            for index, content in enumerate(contents)
        ]
    )

    assert report.count(Verdict.STORED) == 2
    assert report.count(Verdict.DROPPED_BUDGET) == 3


# =========================================================================
# Importance at capture
# =========================================================================


async def test_a_user_flagged_memory_starts_more_important(
    capture: CaptureService, store: SqliteMemoryStore
) -> None:
    plain = await capture.capture(
        episodes=[EpisodeCandidate(title="Ordinary", content="An ordinary thing was said here.")]
    )
    flagged = await capture.capture(
        episodes=[
            EpisodeCandidate(
                title="Important",
                content="Please remember that my sister is called Ana.",
                salience_inputs=SalienceInputs(user_flagged=True),
            )
        ]
    )

    ordinary = await store.get_episode(plain.stored[0])
    important = await store.get_episode(flagged.stored[0])
    assert ordinary is not None and important is not None
    assert important.base_importance > ordinary.base_importance


async def test_salience_starts_at_base_importance(
    capture: CaptureService, store: SqliteMemoryStore
) -> None:
    """They start equal and diverge thereafter (docs/06 §4.2)."""
    report = await capture.capture(
        episodes=[EpisodeCandidate(title="A thing", content="Something worth remembering here.")]
    )
    stored = await store.get_episode(report.stored[0])

    assert stored is not None
    assert stored.salience == stored.base_importance


async def test_provenance_is_carried_from_the_candidate(
    capture: CaptureService, store: SqliteMemoryStore
) -> None:
    report = await capture.capture(
        episodes=[
            EpisodeCandidate(
                title="From the web",
                content="Something read on a page somewhere online.",
                trust=TrustTier.UNTRUSTED,
                source_ref="https://example.test",
            )
        ]
    )
    stored = await store.get_episode(report.stored[0])

    assert stored is not None
    assert stored.provenance.tier is TrustTier.UNTRUSTED


# =========================================================================
# The decay pass (docs/06 §7)
# =========================================================================


@pytest.fixture
def maintenance(
    database: Database, store: SqliteMemoryStore, clock: FakeClock
) -> MemoryMaintenance:
    return MemoryMaintenance(database, store, clock=clock, half_life_days=10.0)


async def test_decay_lowers_salience_over_time(
    store: SqliteMemoryStore, maintenance: MemoryMaintenance, clock: FakeClock
) -> None:
    await store.add_episode(
        Episode(
            id=MemoryId("ep_1"),
            occurred_at=clock.now(),
            title="A fading memory",
            content="Something mildly interesting was said.",
            salience=0.6,
            base_importance=0.3,
        )
    )

    clock.advance(days=5)
    touched = await maintenance.decay_pass()

    faded = await store.get_episode(MemoryId("ep_1"))
    assert touched == 1
    assert faded is not None
    assert faded.salience < 0.6


async def test_decay_is_not_applied_twice_for_the_same_period(
    store: SqliteMemoryStore, maintenance: MemoryMaintenance, clock: FakeClock
) -> None:
    """Double-applied decay silently halves every memory's lifetime."""
    await store.add_episode(
        Episode(
            id=MemoryId("ep_1"),
            occurred_at=clock.now(),
            title="A memory",
            content="Something worth keeping around for now.",
            salience=0.6,
            base_importance=0.3,
        )
    )

    clock.advance(days=5)
    await maintenance.decay_pass()
    once = await store.get_episode(MemoryId("ep_1"))

    await maintenance.decay_pass()  # immediately again, no time passed
    twice = await store.get_episode(MemoryId("ep_1"))

    assert once is not None and twice is not None
    assert once.salience == twice.salience


async def test_a_pinned_memory_survives_the_decay_pass(
    store: SqliteMemoryStore, maintenance: MemoryMaintenance, clock: FakeClock
) -> None:
    await store.add_episode(
        Episode(
            id=MemoryId("ep_pinned"),
            occurred_at=clock.now(),
            title="Never forget",
            content="My sister is called Ana and her birthday is in March.",
            salience=0.6,
            base_importance=0.3,
            pinned=True,
        )
    )

    clock.advance(days=365)
    await maintenance.run()

    kept = await store.get_episode(MemoryId("ep_pinned"))
    assert kept is not None
    assert kept.salience == 0.6


async def test_a_faded_old_memory_is_forgotten(
    store: SqliteMemoryStore, maintenance: MemoryMaintenance, clock: FakeClock
) -> None:
    for index in range(60):
        await store.add_episode(
            Episode(
                id=MemoryId(f"ep_{index}"),
                occurred_at=clock.now(),
                title=f"Trivial exchange {index}",
                content=f"Some passing remark number {index} that nobody needs.",
                salience=0.01,
                base_importance=0.01,
            )
        )

    clock.advance(days=200)
    forgotten, _protected, budget, _considered = await maintenance.forget_pass()

    assert budget == 1  # 2% of 60, floored, at least 1
    assert forgotten == budget  # never more than the cap


async def test_nothing_recent_is_forgotten(
    store: SqliteMemoryStore, maintenance: MemoryMaintenance, clock: FakeClock
) -> None:
    await store.add_episode(
        Episode(
            id=MemoryId("ep_new"),
            occurred_at=clock.now(),
            title="Just happened",
            content="Something that only just occurred and has low salience.",
            salience=0.001,
            base_importance=0.001,
        )
    )

    forgotten, protected, _budget, _considered = await maintenance.forget_pass()

    assert forgotten == 0
    assert protected == 1
    assert await store.get_episode(MemoryId("ep_new")) is not None


async def test_the_sole_source_of_a_belief_is_protected_from_forgetting(
    store: SqliteMemoryStore, maintenance: MemoryMaintenance, clock: FakeClock
) -> None:
    """Otherwise HEDWIG keeps an opinion it cannot account for (docs/06 §7.3)."""
    await store.add_episode(
        Episode(
            id=MemoryId("ep_source"),
            occurred_at=clock.now(),
            title="The conversation",
            content="The user mentioned in passing that they had moved to Lisbon.",
            salience=0.001,
            base_importance=0.001,
        )
    )
    await store.add_belief(
        Belief(
            id=MemoryId("bel_1"),
            statement="The user lives in Lisbon",
            valid_from=clock.now(),
            salience=0.9,
            provenance=Provenance(tier=TrustTier.SELF, derived_from=(MemoryId("ep_source"),)),
        )
    )

    clock.advance(days=300)
    _forgotten, protected, _budget, _considered = await maintenance.forget_pass()

    assert protected >= 1
    assert await store.get_episode(MemoryId("ep_source")) is not None


async def test_the_maintenance_report_says_what_happened(
    store: SqliteMemoryStore, maintenance: MemoryMaintenance, clock: FakeClock
) -> None:
    await store.add_episode(
        Episode(
            id=MemoryId("ep_1"),
            occurred_at=clock.now(),
            title="A memory",
            content="Something that will fade over the coming weeks.",
            salience=0.5,
            base_importance=0.2,
        )
    )
    clock.advance(days=6)

    report = await maintenance.run()

    assert report.decayed == 1
    assert set(report.as_stats()) == {
        "decayed",
        "forgotten",
        "protected",
        "budget",
        "considered",
    }


async def test_maintenance_health_reports_the_cap(
    maintenance: MemoryMaintenance, store: SqliteMemoryStore, clock: FakeClock
) -> None:
    from hedwig.core.ports import HealthStatus

    await store.add_episode(
        Episode(
            id=MemoryId("ep_1"),
            occurred_at=clock.now(),
            title="A memory",
            content="Something to make the store non-empty for the health probe.",
        )
    )

    health = await maintenance.health()

    assert health.status is HealthStatus.OK
    assert health.detail["active_memories"] == 1
    assert health.detail["forget_budget_per_pass"] == 1


async def test_a_forgotten_memory_can_still_be_restored(
    store: SqliteMemoryStore, clock: FakeClock
) -> None:
    """Forgetting is reversible by construction (docs/06 §7.3)."""
    await store.add_episode(
        Episode(
            id=MemoryId("ep_1"),
            occurred_at=clock.now(),
            title="A memory",
            content="Something that was forgotten and then wanted back.",
        )
    )
    await store.tombstone(MemoryId("ep_1"), ForgetReason.DECAYED)

    assert await store.restore(MemoryId("ep_1")) is True
    assert await store.get_episode(MemoryId("ep_1")) is not None
