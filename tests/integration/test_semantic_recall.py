"""The semantic channel's behaviour (MEM-04, docs/06 §17.2). How well it recalls is
`tests/evals/test_recall.py`; this is what it does when things change or go wrong."""

from __future__ import annotations

from hedwig.core.ids import new_id
from hedwig.core.ports.memory import Belief, BeliefStatus, ForgetReason
from hedwig.core.ports.registry import HealthStatus
from hedwig.core.types import MemoryId, MemoryKind
from hedwig.llm.recorded import RecordedProvider
from hedwig.memory import EmbeddingIndex
from hedwig.wiring import Container

KINDS = (MemoryKind.EPISODE, MemoryKind.BELIEF)


def _index(container: Container) -> EmbeddingIndex:
    index = container.registry.get("embeddings")
    assert isinstance(index, EmbeddingIndex)
    return index


async def _belief(container: Container, statement: str) -> MemoryId:
    belief_id = MemoryId(new_id("bel"))
    await container.memory.add_belief(
        Belief(
            id=belief_id,
            statement=statement,
            valid_from=container.clock.now(),
            status=BeliefStatus.ACTIVE,
        )
    )
    await container.bus.drain()
    return belief_id


def _rows(container: Container) -> list[tuple[str, str]]:
    return [
        (str(r["memory_id"]), str(r["model"]))
        for r in container.database.query("SELECT memory_id, model FROM memory_embedding")
    ]


async def test_a_new_memory_is_embedded_by_its_event(running_container: Container) -> None:
    vegetarian = await _belief(running_container, "The user is vegetarian.")
    assert _rows(running_container) == [(vegetarian, "nomic-embed-text")]
    assert await _index(running_container).nearest(
        ["The user is vegetarian."], kinds=KINDS, limit=5
    ) == [vegetarian]


async def test_vectors_from_another_model_are_never_searched_and_get_replaced(
    running_container: Container,
) -> None:
    vegetarian = await _belief(running_container, "The user is vegetarian.")
    running_container.database.execute("UPDATE memory_embedding SET model = 'old-model'")

    index = _index(running_container)
    assert await index.nearest(["The user is vegetarian."], kinds=KINDS, limit=5) == []
    assert await index.backfill() == 1
    assert _rows(running_container) == [(vegetarian, "nomic-embed-text")]


async def test_a_forgotten_memory_is_not_recalled_by_meaning(running_container: Container) -> None:
    vegetarian = await _belief(running_container, "The user is vegetarian.")
    await running_container.memory.tombstone(vegetarian, ForgetReason.USER_REQUEST)
    assert (
        await _index(running_container).nearest(["The user is vegetarian."], kinds=KINDS, limit=5)
        == []
    )


async def test_without_the_model_recall_falls_back_to_words_and_health_says_so(
    running_container: Container, llm_provider: RecordedProvider
) -> None:
    await _belief(running_container, "The user is vegetarian.")
    llm_provider.available = False

    working = await running_container.retrieval.search(["vegetarian"])

    assert [item.text for item in working.items] == ["The user is vegetarian."]
    health = await _index(running_container).health()
    assert health.status is HealthStatus.DEGRADED and "unavailable" in health.message
    llm_provider.available = True
    assert await _index(running_container).backfill() == 0, "the event already embedded it"


async def test_without_sqlite_vec_the_channel_is_off_and_says_why(
    running_container: Container,
) -> None:
    database = running_container.database
    database.vector_search, database.vector_problem = False, "sqlite-vec did not load: test"
    try:
        index = _index(running_container)
        assert await index.nearest(["anything"], kinds=KINDS, limit=5) == []
        health = await index.health()
        assert health.status is HealthStatus.DEGRADED and "sqlite-vec" in health.message
    finally:
        database.vector_search = True


async def test_a_memory_stored_during_shutdown_is_left_for_the_repair_task(
    running_container: Container,
) -> None:
    """Found live: the gateway closed under the subscriber and the event was dead-lettered."""
    index = _index(running_container)
    await index.stop()
    await _belief(running_container, "The user is vegetarian.")

    assert _rows(running_container) == []
    assert running_container.database.query("SELECT * FROM event_dead_letter") == []
    await index.start()
    assert await index.backfill() == 1
