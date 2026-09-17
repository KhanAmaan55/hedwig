"""Retrieval (docs/06 §5).

The highest-leverage component in the system, so the tests are about the *properties* the
pipeline promises — recall, ranking, diversity, budget — rather than exact scores, which
are an implementation detail that should be free to improve.
"""

from __future__ import annotations

import pytest

from hedwig.core.clock import FakeClock
from hedwig.core.ports.memory import (
    Belief,
    Entity,
    Episode,
    RetrievalPolicy,
)
from hedwig.core.store import Database
from hedwig.core.types import EntityId, MemoryId, MemoryKind, Provenance, TrustTier
from hedwig.memory import HybridRetrieval, SqliteMemoryStore
from hedwig.memory.retrieval import _fuse, _mmr, _pack, _to_match


@pytest.fixture
def store(database: Database, clock: FakeClock) -> SqliteMemoryStore:
    return SqliteMemoryStore(database, clock=clock)


@pytest.fixture
def retrieval(database: Database, clock: FakeClock) -> HybridRetrieval:
    return HybridRetrieval(database, clock=clock)


async def _seed(store: SqliteMemoryStore, clock: FakeClock) -> None:
    entries = [
        ("ep_rust", "Learning Rust", "The user is writing a compiler in Rust.", 0.8),
        ("ep_lisbon", "Moving to Lisbon", "The user said they moved to Lisbon.", 0.7),
        ("ep_coffee", "Coffee preference", "The user prefers filter coffee to espresso.", 0.3),
        ("ep_trivia", "Small talk", "We exchanged pleasantries about the weather today.", 0.05),
    ]
    for memory_id, title, content, salience in entries:
        await store.add_episode(
            Episode(
                id=MemoryId(memory_id),
                occurred_at=clock.now(),
                title=title,
                content=content,
                salience=salience,
                base_importance=salience,
            )
        )
        clock.advance(hours=1)


# =========================================================================
# End-to-end recall
# =========================================================================


async def test_a_stored_memory_can_be_found_by_its_words(
    store: SqliteMemoryStore, retrieval: HybridRetrieval, clock: FakeClock
) -> None:
    """The whole point: a fact stated once is findable later."""
    await _seed(store, clock)

    result = await retrieval.search(["rust compiler"])

    assert any("Rust" in item.text for item in result.items)


async def test_an_empty_query_returns_nothing(retrieval: HybridRetrieval) -> None:
    result = await retrieval.search(["  ", ""])
    assert result.items == ()


async def test_searching_an_empty_store_is_harmless(retrieval: HybridRetrieval) -> None:
    result = await retrieval.search(["anything"])
    assert result.items == ()
    assert result.candidates == 0


async def test_beliefs_are_retrievable_alongside_episodes(
    store: SqliteMemoryStore, retrieval: HybridRetrieval, clock: FakeClock
) -> None:
    await store.add_belief(
        Belief(
            id=MemoryId("bel_1"),
            statement="The user lives in Lisbon",
            valid_from=clock.now(),
            salience=0.8,
        )
    )

    result = await retrieval.search(["Lisbon"])

    assert any(item.kind is MemoryKind.BELIEF for item in result.items)


async def test_a_superseded_belief_is_not_retrieved(
    store: SqliteMemoryStore, retrieval: HybridRetrieval, clock: FakeClock
) -> None:
    """Retrieval returns what is currently believed, not what used to be."""
    await store.add_belief(
        Belief(id=MemoryId("bel_1"), statement="The user lives in Berlin", valid_from=clock.now())
    )
    await store.supersede_belief(
        MemoryId("bel_1"),
        Belief(id=MemoryId("bel_2"), statement="The user lives in Lisbon", valid_from=clock.now()),
    )

    result = await retrieval.search(["where does the user live"])

    texts = " ".join(item.text for item in result.items)
    assert "Berlin" not in texts


async def test_a_forgotten_memory_is_not_retrieved(
    store: SqliteMemoryStore, retrieval: HybridRetrieval, clock: FakeClock
) -> None:
    from hedwig.core.ports.memory import ForgetReason

    await _seed(store, clock)
    await store.tombstone(MemoryId("ep_rust"), ForgetReason.DECAYED)

    result = await retrieval.search(["rust compiler"])

    assert all("Rust" not in item.text for item in result.items)


# =========================================================================
# Ranking
# =========================================================================


async def test_salience_lifts_a_memory_above_a_mere_word_match(
    store: SqliteMemoryStore, retrieval: HybridRetrieval, clock: FakeClock
) -> None:
    """An important memory should beat a trivial one that happens to share a word."""
    await store.add_episode(
        Episode(
            id=MemoryId("ep_important"),
            occurred_at=clock.now(),
            title="Architecture decision",
            content="We chose reciprocal rank fusion for retrieval.",
            salience=0.95,
            base_importance=0.95,
        )
    )
    await store.add_episode(
        Episode(
            id=MemoryId("ep_trivial"),
            occurred_at=clock.now(),
            title="Passing remark",
            content="Someone mentioned retrieval in passing.",
            salience=0.05,
            base_importance=0.05,
        )
    )

    result = await retrieval.search(["retrieval"])
    ranked = sorted(result.items, key=lambda item: item.score, reverse=True)

    assert ranked[0].memory_id == "ep_important"


async def test_untrusted_material_is_discounted(
    store: SqliteMemoryStore, retrieval: HybridRetrieval, clock: FakeClock
) -> None:
    """The trust penalty follows the content forever (docs/06 §5.4)."""
    for memory_id, tier in (("ep_self", TrustTier.SELF), ("ep_web", TrustTier.UNTRUSTED)):
        await store.add_episode(
            Episode(
                id=MemoryId(memory_id),
                occurred_at=clock.now(),
                title="Fusion notes",
                content="Notes about reciprocal rank fusion in retrieval systems.",
                salience=0.5,
                base_importance=0.5,
                provenance=Provenance(tier=tier),
            )
        )

    result = await retrieval.search(["fusion retrieval"])
    scores = {item.memory_id: item.score for item in result.items}

    assert scores[MemoryId("ep_web")] < scores[MemoryId("ep_self")]


async def test_every_item_explains_its_own_score(
    store: SqliteMemoryStore, retrieval: HybridRetrieval, clock: FakeClock
) -> None:
    """A ranking nobody can explain is a ranking nobody can debug (docs/16 §6)."""
    await _seed(store, clock)

    result = await retrieval.search(["rust"])

    breakdown = result.items[0].score_breakdown
    assert {"fused", "salience", "confidence", "recency", "trust", "age_days"} <= set(breakdown)


async def test_recency_boosts_the_recent(
    store: SqliteMemoryStore, retrieval: HybridRetrieval, clock: FakeClock
) -> None:
    await store.add_episode(
        Episode(
            id=MemoryId("ep_old"),
            occurred_at=clock.now(),
            title="Fusion notes",
            content="An old note about retrieval fusion.",
            salience=0.5,
            base_importance=0.5,
        )
    )
    clock.advance(days=200)
    await store.add_episode(
        Episode(
            id=MemoryId("ep_new"),
            occurred_at=clock.now(),
            title="Fusion notes",
            content="A new note about retrieval fusion.",
            salience=0.5,
            base_importance=0.5,
        )
    )

    result = await retrieval.search(["retrieval fusion"])
    scores = {item.memory_id: item.score for item in result.items}

    assert scores[MemoryId("ep_new")] > scores[MemoryId("ep_old")]


async def test_low_confidence_can_be_filtered_out(
    store: SqliteMemoryStore, retrieval: HybridRetrieval, clock: FakeClock
) -> None:
    await store.add_belief(
        Belief(
            id=MemoryId("bel_shaky"),
            statement="The user might prefer tea",
            valid_from=clock.now(),
            confidence=0.2,
        )
    )

    permissive = await retrieval.search(["tea"], policy=RetrievalPolicy(min_confidence=0.0))
    strict = await retrieval.search(["tea"], policy=RetrievalPolicy(min_confidence=0.5))

    assert len(permissive.items) == 1
    assert strict.items == ()


# =========================================================================
# Channels
# =========================================================================


async def test_the_structural_channel_finds_entity_linked_memories(
    store: SqliteMemoryStore, retrieval: HybridRetrieval, clock: FakeClock
) -> None:
    """Words are not the only way in: things linked to the same entity count too."""
    entity = await store.upsert_entity(Entity(id=EntityId("ent_1"), name="Lisbon", kind="place"))
    await store.add_episode(
        Episode(
            id=MemoryId("ep_linked"),
            occurred_at=clock.now(),
            title="A conversation",
            content="Nothing here shares a single word with the query.",
            salience=0.6,
            base_importance=0.6,
            entities=(entity,),
        )
    )

    result = await retrieval.search(
        ["completely unrelated phrasing"],
        policy=RetrievalPolicy(entity_focus=(entity,)),
    )

    assert [item.memory_id for item in result.items] == ["ep_linked"]


async def test_the_temporal_channel_surfaces_the_recent(
    store: SqliteMemoryStore, retrieval: HybridRetrieval, clock: FakeClock
) -> None:
    """ "What were we just doing?" is a real question no word matching answers."""
    await store.add_episode(
        Episode(
            id=MemoryId("ep_recent"),
            occurred_at=clock.now(),
            title="Just now",
            content="Something with no overlapping vocabulary whatsoever.",
            salience=0.5,
            base_importance=0.5,
        )
    )

    result = await retrieval.search(["zzzz nonmatching"])

    assert [item.memory_id for item in result.items] == ["ep_recent"]


# =========================================================================
# Output shape
# =========================================================================


async def test_items_come_back_in_chronological_order(
    store: SqliteMemoryStore, retrieval: HybridRetrieval, clock: FakeClock
) -> None:
    """A chronological context reads as a history; a score-ordered one reads as rubble."""
    await _seed(store, clock)

    result = await retrieval.search(["the user"])

    times = [item.occurred_at for item in result.items]
    assert times == sorted(times)


async def test_the_token_budget_is_respected_and_drops_are_counted(
    store: SqliteMemoryStore, retrieval: HybridRetrieval, clock: FakeClock
) -> None:
    """Starvation must be visible rather than silent (docs/06 §5.5)."""
    for index in range(12):
        await store.add_episode(
            Episode(
                id=MemoryId(f"ep_{index}"),
                occurred_at=clock.now(),
                title=f"Long memory {index}",
                content="retrieval " * 200,
                salience=0.5,
                base_importance=0.5,
            )
        )
        clock.advance(minutes=1)

    result = await retrieval.search(["retrieval"], policy=RetrievalPolicy(token_budget=300))

    assert result.token_count <= 300
    assert result.dropped > 0


async def test_the_working_set_reports_what_it_considered(
    store: SqliteMemoryStore, retrieval: HybridRetrieval, clock: FakeClock
) -> None:
    await _seed(store, clock)

    result = await retrieval.search(["the user"])

    assert result.candidates >= len(result.items)
    assert result.queries == ("the user",)


# =========================================================================
# Pure pipeline stages
# =========================================================================


def test_fusion_needs_only_ranks() -> None:
    """RRF is used precisely so channels never have to agree what a score means."""
    fused = _fuse(
        {"lexical": [MemoryId("a"), MemoryId("b")], "temporal": [MemoryId("b")]},
        {"lexical": 1.0, "temporal": 1.0},
    )
    # `b` appears in both channels, so it beats `a` which ranks first in only one.
    assert fused[MemoryId("b")] > fused[MemoryId("a")]


def test_a_zero_weighted_channel_is_ignored() -> None:
    fused = _fuse({"lexical": [MemoryId("a")]}, {"lexical": 0.0})
    assert fused == {}


def test_fusion_is_invariant_to_score_scaling() -> None:
    """The property that makes RRF robust as the corpus grows."""
    ranks = {"lexical": [MemoryId("a"), MemoryId("b"), MemoryId("c")]}
    first = _fuse(ranks, {"lexical": 1.0})
    second = _fuse(ranks, {"lexical": 1000.0})

    assert sorted(first, key=lambda k: -first[k]) == sorted(second, key=lambda k: -second[k])


def test_mmr_breaks_up_near_duplicates() -> None:
    """Without it the top-N is often N paraphrases of one memory."""
    from datetime import UTC, datetime

    from hedwig.core.ports.memory import RetrievedItem

    def item(memory_id: str, text: str, score: float) -> RetrievedItem:
        return RetrievedItem(
            memory_id=MemoryId(memory_id),
            kind=MemoryKind.EPISODE,
            text=text,
            score=score,
            occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
            provenance=Provenance(tier=TrustTier.SELF),
        )

    items = [
        item("a", "the user is writing a compiler in rust", 1.0),
        item("b", "the user is writing a compiler in rust language", 0.99),
        item("c", "the user prefers filter coffee to espresso", 0.5),
    ]

    diverse = _mmr(items, lambda_diversity=0.8, limit=2)

    assert [chosen.memory_id for chosen in diverse] == ["a", "c"]


def test_zero_diversity_keeps_pure_relevance_order() -> None:
    from datetime import UTC, datetime

    from hedwig.core.ports.memory import RetrievedItem

    items = [
        RetrievedItem(
            memory_id=MemoryId(str(index)),
            kind=MemoryKind.EPISODE,
            text=f"text {index}",
            score=1.0 - index / 10,
            occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
            provenance=Provenance(tier=TrustTier.SELF),
        )
        for index in range(5)
    ]

    assert [item.memory_id for item in _mmr(items, lambda_diversity=0.0, limit=3)] == [
        "0",
        "1",
        "2",
    ]


def test_packing_never_exceeds_the_budget() -> None:
    from datetime import UTC, datetime

    from hedwig.core.ports.memory import RetrievedItem

    items = [
        RetrievedItem(
            memory_id=MemoryId(str(index)),
            kind=MemoryKind.EPISODE,
            text="x",
            score=1.0,
            occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
            provenance=Provenance(tier=TrustTier.SELF),
            token_estimate=40,
        )
        for index in range(10)
    ]

    packed, dropped = _pack(items, budget=100)

    assert sum(item.token_estimate for item in packed) <= 100
    assert len(packed) + dropped == len(items)


@pytest.mark.parametrize(
    "query",
    ['a "quoted" phrase', "hyphen-ated words", "parens (here)", "star* and colon:", "^caret"],
)
def test_punctuation_cannot_break_the_search(query: str) -> None:
    """User text reaches FTS5 directly, where punctuation would otherwise be syntax."""
    match = _to_match(query)
    assert '"' in match or match == ""
    assert "(" not in match
    assert "*" not in match


def test_a_query_of_only_punctuation_yields_no_match_expression() -> None:
    assert _to_match("?!...") == ""
