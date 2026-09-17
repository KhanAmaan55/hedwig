"""Episodic and semantic persistence (docs/06 §3, docs/05 §5.2)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from hedwig.core.bus import InProcessBus
from hedwig.core.clock import FakeClock
from hedwig.core.ports import Event
from hedwig.core.ports.memory import (
    Belief,
    BeliefStatus,
    DerivationRelation,
    Entity,
    Episode,
    EpisodeKind,
    ForgetReason,
    RetrievedItem,
)
from hedwig.core.store import Database
from hedwig.core.types import EntityId, MemoryId, MemoryKind, Provenance, TrustTier
from hedwig.memory import SqliteMemoryStore


@pytest.fixture
def store(database: Database, clock: FakeClock) -> SqliteMemoryStore:
    return SqliteMemoryStore(database, clock=clock)


def _episode(clock: FakeClock, **overrides: object) -> Episode:
    defaults: dict[str, object] = {
        "id": MemoryId("ep_1"),
        "occurred_at": clock.now(),
        "title": "A decision about retrieval",
        "content": "We settled on reciprocal rank fusion over score normalisation.",
    }
    return Episode(**{**defaults, **overrides})  # type: ignore[arg-type]


def _belief(clock: FakeClock, **overrides: object) -> Belief:
    defaults: dict[str, object] = {
        "id": MemoryId("bel_1"),
        "statement": "The user lives in Lisbon",
        "valid_from": clock.now(),
    }
    return Belief(**{**defaults, **overrides})  # type: ignore[arg-type]


# =========================================================================
# Episodic memory
# =========================================================================


async def test_an_episode_round_trips(store: SqliteMemoryStore, clock: FakeClock) -> None:
    await store.add_episode(_episode(clock, salience=0.7, base_importance=0.7))

    read = await store.get_episode(MemoryId("ep_1"))

    assert read is not None
    assert read.title == "A decision about retrieval"
    assert read.salience == 0.7
    assert read.kind is EpisodeKind.INTERACTION
    assert read.occurred_at.tzinfo is not None


async def test_base_importance_is_stored_separately_from_salience(
    store: SqliteMemoryStore, clock: FakeClock
) -> None:
    """Without the split, an important untouched memory looks like noise (docs/05 §5.2)."""
    await store.add_episode(_episode(clock, salience=0.9, base_importance=0.9))
    await store.set_salience(MemoryId("ep_1"), 0.1)

    read = await store.get_episode(MemoryId("ep_1"))
    assert read is not None
    assert read.salience == 0.1
    assert read.base_importance == 0.9  # unchanged


async def test_recent_episodes_are_newest_first(store: SqliteMemoryStore, clock: FakeClock) -> None:
    for index in range(3):
        await store.add_episode(
            _episode(clock, id=MemoryId(f"ep_{index}"), title=f"episode {index}")
        )
        clock.advance(hours=1)

    recent = await store.recent_episodes(limit=2)

    assert [episode.title for episode in recent] == ["episode 2", "episode 1"]


async def test_provenance_survives_the_round_trip(
    store: SqliteMemoryStore, clock: FakeClock
) -> None:
    """The tier travels with the content for its whole life (docs/13 §3.1)."""
    await store.add_episode(
        _episode(
            clock,
            provenance=Provenance(tier=TrustTier.UNTRUSTED, source_ref="https://example.test"),
        )
    )

    read = await store.get_episode(MemoryId("ep_1"))
    assert read is not None
    assert read.provenance.tier is TrustTier.UNTRUSTED
    assert read.provenance.source_ref == "https://example.test"
    assert read.provenance.may_instruct is False


# =========================================================================
# Semantic memory
# =========================================================================


async def test_a_belief_enters_tentative(store: SqliteMemoryStore, clock: FakeClock) -> None:
    """The guard against confabulation hardening into fact (docs/06 §3.2)."""
    await store.add_belief(_belief(clock))

    read = await store.get_belief(MemoryId("bel_1"))
    assert read is not None
    assert read.status is BeliefStatus.TENTATIVE
    assert read.is_current is True


async def test_promotion_requires_a_tentative_belief(
    store: SqliteMemoryStore, clock: FakeClock
) -> None:
    await store.add_belief(_belief(clock))
    await store.promote_belief(MemoryId("bel_1"), confidence=0.85)

    read = await store.get_belief(MemoryId("bel_1"))
    assert read is not None
    assert read.status is BeliefStatus.ACTIVE
    assert read.confidence == 0.85


async def test_supersession_keeps_the_old_belief_as_history(
    store: SqliteMemoryStore, clock: FakeClock
) -> None:
    """This is what lets HEDWIG say "I used to think you were in Berlin"."""
    berlin = _belief(clock, statement="The user lives in Berlin")
    await store.add_belief(berlin)

    clock.advance(days=30)
    lisbon = _belief(clock, id=MemoryId("bel_2"), statement="The user lives in Lisbon")
    await store.supersede_belief(MemoryId("bel_1"), lisbon)

    old = await store.get_belief(MemoryId("bel_1"))
    new = await store.get_belief(MemoryId("bel_2"))

    assert old is not None
    assert old.status is BeliefStatus.SUPERSEDED
    assert old.statement == "The user lives in Berlin"  # not overwritten
    assert old.valid_to is not None
    assert old.superseded_by == "bel_2"
    assert new is not None
    assert new.is_current


async def test_superseding_something_absent_is_an_error(
    store: SqliteMemoryStore, clock: FakeClock
) -> None:
    with pytest.raises(LookupError):
        await store.supersede_belief(MemoryId("nope"), _belief(clock, id=MemoryId("bel_9")))


async def test_beliefs_about_an_entity_exclude_history_by_default(
    store: SqliteMemoryStore, clock: FakeClock
) -> None:
    entity = await store.upsert_entity(Entity(id=EntityId("ent_1"), name="the user", kind="person"))
    await store.add_belief(_belief(clock, subject_id=entity))
    await store.supersede_belief(
        MemoryId("bel_1"),
        _belief(clock, id=MemoryId("bel_2"), statement="Moved to Porto", subject_id=entity),
    )

    current = await store.beliefs_about(entity)
    everything = await store.beliefs_about(entity, include_historical=True)

    assert len(current) == 1
    assert len(everything) == 2


# =========================================================================
# Entities
# =========================================================================


async def test_entity_names_are_normalised_for_deduplication(
    store: SqliteMemoryStore,
) -> None:
    """ "Amaan", "amaan" and " Amaan " must not become three entities (docs/06 §10)."""
    first = await store.upsert_entity(Entity(id=EntityId("ent_1"), name="Rust", kind="topic"))
    second = await store.upsert_entity(Entity(id=EntityId("ent_2"), name="  rust ", kind="topic"))

    assert first == second
    resolved = await store.resolve_entity("RUST")
    assert resolved is not None
    assert resolved.mention_count == 2


async def test_an_unknown_entity_resolves_to_nothing(store: SqliteMemoryStore) -> None:
    assert await store.resolve_entity("never mentioned") is None


async def test_episodes_link_to_their_entities(store: SqliteMemoryStore, clock: FakeClock) -> None:
    entity = await store.upsert_entity(Entity(id=EntityId("ent_1"), name="Lisbon", kind="place"))
    await store.add_episode(_episode(clock, entities=(entity,)))

    read = await store.get_episode(MemoryId("ep_1"))
    assert read is not None
    assert read.entities == (entity,)


# =========================================================================
# Lineage
# =========================================================================


async def test_lineage_records_where_a_belief_came_from(
    store: SqliteMemoryStore, clock: FakeClock
) -> None:
    """What makes "why do you believe that?" answerable."""
    await store.add_episode(_episode(clock))
    await store.add_belief(_belief(clock))
    await store.record_derivation(
        MemoryId("bel_1"), MemoryId("ep_1"), DerivationRelation.EXTRACTED_FROM
    )

    assert await store.sources_of(MemoryId("bel_1")) == ("ep_1",)
    assert await store.derived_from(MemoryId("ep_1")) == ("bel_1",)


async def test_derived_from_is_recorded_at_write_time(
    store: SqliteMemoryStore, clock: FakeClock
) -> None:
    await store.add_episode(_episode(clock))
    await store.add_belief(
        _belief(
            clock,
            provenance=Provenance(tier=TrustTier.SELF, derived_from=(MemoryId("ep_1"),)),
        )
    )

    assert await store.sources_of(MemoryId("bel_1")) == ("ep_1",)


# =========================================================================
# Reinforcement and access
# =========================================================================


async def test_reinforcement_raises_salience(store: SqliteMemoryStore, clock: FakeClock) -> None:
    await store.add_episode(_episode(clock, salience=0.5))
    await store.reinforce([MemoryId("ep_1")], 0.1, cause="test")

    read = await store.get_episode(MemoryId("ep_1"))
    assert read is not None
    assert read.salience == pytest.approx(0.6)


async def test_recording_access_counts_and_reinforces(
    store: SqliteMemoryStore, clock: FakeClock
) -> None:
    await store.add_episode(_episode(clock, salience=0.5))
    item = RetrievedItem(
        memory_id=MemoryId("ep_1"),
        kind=MemoryKind.EPISODE,
        text="x",
        score=0.9,
        occurred_at=clock.now(),
        provenance=Provenance(tier=TrustTier.SELF),
    )

    await store.record_access([item], used_in_reply=True, turn_id="turn_1")

    read = await store.get_episode(MemoryId("ep_1"))
    assert read is not None
    assert read.access_count == 1
    assert read.last_accessed_at is not None
    # Retrieved + cited, per docs/06 §7.2.
    assert read.salience == pytest.approx(0.57)


async def test_being_cited_reinforces_more_than_being_merely_retrieved(
    store: SqliteMemoryStore, clock: FakeClock
) -> None:
    for memory_id in ("ep_1", "ep_2"):
        await store.add_episode(_episode(clock, id=MemoryId(memory_id), salience=0.5))

    def item(memory_id: str) -> RetrievedItem:
        return RetrievedItem(
            memory_id=MemoryId(memory_id),
            kind=MemoryKind.EPISODE,
            text="x",
            score=0.5,
            occurred_at=clock.now(),
            provenance=Provenance(tier=TrustTier.SELF),
        )

    await store.record_access([item("ep_1")], used_in_reply=False, turn_id=None)
    await store.record_access([item("ep_2")], used_in_reply=True, turn_id=None)

    retrieved = await store.get_episode(MemoryId("ep_1"))
    cited = await store.get_episode(MemoryId("ep_2"))
    assert retrieved is not None and cited is not None
    assert cited.salience > retrieved.salience


# =========================================================================
# Forgetting
# =========================================================================


async def test_tombstoning_hides_a_memory_without_destroying_it(
    store: SqliteMemoryStore, clock: FakeClock, database: Database
) -> None:
    await store.add_episode(_episode(clock))

    assert await store.tombstone(MemoryId("ep_1"), ForgetReason.DECAYED) is True
    assert await store.get_episode(MemoryId("ep_1")) is None

    archived = database.query_one("SELECT archived_body, reason FROM tombstone")
    assert archived is not None
    assert archived["reason"] == "decayed"
    assert "retrieval" in archived["archived_body"]


async def test_forgetting_is_reversible(store: SqliteMemoryStore, clock: FakeClock) -> None:
    await store.add_episode(_episode(clock))
    await store.tombstone(MemoryId("ep_1"), ForgetReason.DECAYED)

    assert await store.restore(MemoryId("ep_1")) is True
    assert await store.get_episode(MemoryId("ep_1")) is not None


async def test_a_pinned_memory_refuses_to_be_forgotten(
    store: SqliteMemoryStore, clock: FakeClock
) -> None:
    """The one unforgivable failure is forgetting what the user asked you to keep."""
    await store.add_episode(_episode(clock, pinned=True))

    assert await store.tombstone(MemoryId("ep_1"), ForgetReason.DECAYED) is False
    assert await store.get_episode(MemoryId("ep_1")) is not None


async def test_a_privacy_purge_overrules_pinning(
    store: SqliteMemoryStore, clock: FakeClock
) -> None:
    """The user asking to forget beats the user having asked to remember."""
    await store.add_episode(_episode(clock, pinned=True))
    assert await store.tombstone(MemoryId("ep_1"), ForgetReason.PRIVACY_PURGE) is True


async def test_pinning_can_be_set_after_the_fact(
    store: SqliteMemoryStore, clock: FakeClock
) -> None:
    await store.add_episode(_episode(clock))
    await store.set_pinned(MemoryId("ep_1"), True)

    assert await store.tombstone(MemoryId("ep_1"), ForgetReason.DECAYED) is False


async def test_tombstoning_something_absent_reports_false(store: SqliteMemoryStore) -> None:
    assert await store.tombstone(MemoryId("nope"), ForgetReason.DECAYED) is False


# =========================================================================
# Consolidation and statistics
# =========================================================================


async def test_consolidation_iterates_what_changed_since(
    store: SqliteMemoryStore, clock: FakeClock
) -> None:
    await store.add_episode(_episode(clock))
    cutoff = clock.now() + timedelta(seconds=1)
    clock.advance(hours=2)
    await store.add_episode(_episode(clock, id=MemoryId("ep_2"), title="later"))

    seen = [item async for item in store.iter_for_consolidation(since=cutoff)]

    assert [item.id for item in seen] == ["ep_2"]


async def test_counts_and_health(store: SqliteMemoryStore, clock: FakeClock) -> None:
    from hedwig.core.ports import HealthStatus

    await store.add_episode(_episode(clock))
    await store.add_belief(_belief(clock))

    counts = store.counts()
    assert counts["episodes"] == 1
    assert counts["beliefs"] == 1
    assert counts["tentative_beliefs"] == 1

    health = await store.health()
    assert health.status is HealthStatus.OK


async def test_health_notices_when_beliefs_are_mostly_unverified(
    store: SqliteMemoryStore, clock: FakeClock
) -> None:
    """Unverified beliefs accumulating is the confabulation-pressure signal (docs/19 §6.2)."""
    from hedwig.core.ports import HealthStatus

    for index in range(25):
        await store.add_belief(
            _belief(clock, id=MemoryId(f"bel_{index}"), statement=f"claim {index}")
        )

    health = await store.health()
    assert health.status is HealthStatus.DEGRADED
    assert "unverified" in health.message


async def test_memory_writes_are_announced_on_the_bus(
    database: Database, clock: FakeClock, bus: InProcessBus
) -> None:
    seen: list[Event] = []
    bus.subscribe("memory.*.*", lambda event: _collect(seen, event), name="memory-watch")
    store = SqliteMemoryStore(database, clock=clock, bus=bus)

    await store.add_episode(_episode(clock))
    await store.add_belief(_belief(clock))
    await bus.drain()

    assert {event.type for event in seen} == {
        "memory.episode.stored",
        "memory.belief.formed",
    }


async def _collect(sink: list[Event], event: Event) -> None:
    sink.append(event)
