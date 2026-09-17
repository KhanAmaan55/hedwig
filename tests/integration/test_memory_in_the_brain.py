"""Memory reaching the turn graph (docs/07 §2, docs/06).

This is the Milestone-4 claim being tested rather than asserted: the brain's nodes were
written against a narrow `Recaller` port with a stub behind it, and Milestone 5 replaced the
stub with real retrieval. If `nodes.py` had needed to change, the seam was decoration.

Also here: the long-horizon behaviour that only a `FakeClock` makes testable — an important
fact stated once and still recalled ninety days later, while the day's small talk is gone
(docs/20 §6).
"""

from __future__ import annotations

import pytest

from hedwig.core.clock import FakeClock
from hedwig.core.ports.memory import Episode
from hedwig.core.store import Database
from hedwig.core.types import MemoryId, SalienceInputs
from hedwig.memory import (
    CaptureService,
    EpisodeCandidate,
    HybridRetrieval,
    MemoryMaintenance,
    MemoryRecaller,
    SqliteMemoryStore,
)
from hedwig.wiring import Container


@pytest.fixture
def memory_stack(
    database: Database, clock: FakeClock
) -> tuple[SqliteMemoryStore, HybridRetrieval, CaptureService, MemoryMaintenance]:
    store = SqliteMemoryStore(database, clock=clock)
    retrieval = HybridRetrieval(database, clock=clock)
    capture = CaptureService(store, clock=clock)
    maintenance = MemoryMaintenance(database, store, clock=clock, half_life_days=20.0)
    return store, retrieval, capture, maintenance


# =========================================================================
# The seam
# =========================================================================


async def test_the_brain_recalls_from_real_memory_without_node_changes(
    container: Container, clock: FakeClock
) -> None:
    """The whole point of Milestone 4's `Recaller` port."""
    await container.capture.capture(
        episodes=[
            EpisodeCandidate(
                title="Compiler project",
                content="The user is writing a compiler in Rust and enjoys the borrow checker.",
                salience_inputs=SalienceInputs(user_flagged=True),
            )
        ]
    )

    final = await container.brain.run_turn("remind me about the compiler project")

    assert final["context"].items, "the brain recalled nothing from a populated store"
    assert any("Rust" in item.text for item in final["context"].items)
    assert final["status"] == "completed"


async def test_the_wired_recaller_is_not_a_stub(container: Container) -> None:
    """`MemoryRecaller` and `StubRecaller` are unrelated types, so this one assertion is
    the whole claim: the container wires real retrieval, not the Milestone-4 placeholder."""
    assert isinstance(container.brain.collaborators.recaller, MemoryRecaller)


async def test_the_brain_still_works_with_an_empty_store(container: Container) -> None:
    """No memories is a normal state, not an error — it is every first conversation."""
    final = await container.brain.run_turn("hello, we have never spoken before")

    assert final["status"] == "completed"
    assert final["context"].items == ()


async def test_recall_respects_the_turn_token_budget(database: Database, clock: FakeClock) -> None:
    """Retrieval cannot overrun what the turn allowed (docs/06 §5.1)."""
    from hedwig.core.ports.brain import TurnPolicy

    store = SqliteMemoryStore(database, clock=clock)
    for index in range(10):
        await store.add_episode(
            Episode(
                id=MemoryId(f"ep_{index}"),
                occurred_at=clock.now(),
                title=f"Verbose memory {index}",
                content="retrieval " * 150,
                salience=0.6,
                base_importance=0.6,
            )
        )
        clock.advance(minutes=1)

    recaller = MemoryRecaller(HybridRetrieval(database, clock=clock))
    context = await recaller.recall(["retrieval"], policy=TurnPolicy(token_budget=200))

    assert context.token_count <= 200
    assert context.dropped > 0


async def test_trust_tiers_survive_the_translation_into_the_graph(
    database: Database, clock: FakeClock
) -> None:
    """Untrusted material must still be marked untrusted once it reaches the brain."""
    from hedwig.core.ports.brain import TrustTier as BrainTrust
    from hedwig.core.ports.brain import TurnPolicy
    from hedwig.core.types import Provenance, TrustTier

    store = SqliteMemoryStore(database, clock=clock)
    await store.add_episode(
        Episode(
            id=MemoryId("ep_web"),
            occurred_at=clock.now(),
            title="Read on a page",
            content="Something asserted by a website about retrieval systems.",
            salience=0.7,
            base_importance=0.7,
            provenance=Provenance(tier=TrustTier.UNTRUSTED, source_ref="https://example.test"),
        )
    )

    recaller = MemoryRecaller(HybridRetrieval(database, clock=clock))
    context = await recaller.recall(["retrieval systems"], policy=TurnPolicy())

    assert context.items
    assert context.items[0].trust is BrainTrust.UNTRUSTED
    assert context.items[0].source == "https://example.test"


# =========================================================================
# Long-horizon behaviour
# =========================================================================


async def test_an_important_fact_survives_ninety_days_while_trivia_does_not(
    memory_stack: tuple[SqliteMemoryStore, HybridRetrieval, CaptureService, MemoryMaintenance],
    clock: FakeClock,
) -> None:
    """docs/20 §6: the scenario that makes the whole design worth having.

    A simulated quarter in milliseconds, which is only possible because nothing in HEDWIG
    reads the wall clock directly (docs/03 §5.6).
    """
    store, retrieval, capture, maintenance = memory_stack

    important = await capture.capture(
        episodes=[
            EpisodeCandidate(
                title="Sister's name",
                content="Please remember my sister is called Ana and lives in Porto.",
                salience_inputs=SalienceInputs(user_flagged=True, entity_centrality=1.0),
            )
        ]
    )
    trivial = await capture.capture(
        episodes=[
            EpisodeCandidate(
                title="Weather chat",
                content="We agreed the weather has been unusually mild for the season.",
                salience_inputs=SalienceInputs(redundancy=0.8),
            )
        ]
    )
    assert important.stored and trivial.stored

    # Ninety nightly passes.
    for _ in range(90):
        clock.advance(days=1)
        await maintenance.run()

    surviving = await store.get_episode(important.stored[0])
    faded = await store.get_episode(trivial.stored[0])

    assert surviving is not None, "an important, user-flagged memory was forgotten"
    assert surviving.salience > (faded.salience if faded else 0.0)

    result = await retrieval.search(["what is my sister called"])
    assert any("Ana" in item.text for item in result.items)


async def test_a_recalled_memory_resists_decay(
    memory_stack: tuple[SqliteMemoryStore, HybridRetrieval, CaptureService, MemoryMaintenance],
    clock: FakeClock,
) -> None:
    """Use is what keeps a memory alive (docs/06 §7.1-7.2)."""
    store, retrieval, capture, maintenance = memory_stack

    for name, content in (
        ("used", "The user is writing a compiler in Rust for fun."),
        ("unused", "A passing note about bicycle maintenance schedules."),
    ):
        await capture.capture(episodes=[EpisodeCandidate(title=name, content=content)])

    used = await store.recent_episodes(limit=10)
    used_id = next(episode.id for episode in used if episode.title == "used")
    unused_id = next(episode.id for episode in used if episode.title == "unused")

    for _ in range(30):
        clock.advance(days=1)
        # The "used" memory is retrieved and cited each day.
        result = await retrieval.search(["rust compiler"])
        cited = [item for item in result.items if item.memory_id == used_id]
        if cited:
            await store.record_access(cited, used_in_reply=True, turn_id=None)
        await maintenance.run()

    still_used = await store.get_episode(used_id)
    forgotten_shaped = await store.get_episode(unused_id)

    assert still_used is not None
    assert still_used.access_count > 0
    if forgotten_shaped is not None:
        assert still_used.salience > forgotten_shaped.salience


async def test_the_store_stays_bounded_over_a_simulated_quarter(
    memory_stack: tuple[SqliteMemoryStore, HybridRetrieval, CaptureService, MemoryMaintenance],
    clock: FakeClock,
) -> None:
    """Decay plus forgetting is what keeps the *active* set bounded (docs/05 §7).

    The daily content is deliberately *distinct* — otherwise the novelty gate collapses it
    all into a handful of reinforced memories and nothing ever reaches the forget pass,
    which is correct behaviour but tests a different mechanism.
    """
    store, _retrieval, capture, maintenance = memory_stack

    subjects = [
        "bicycle gears",
        "sourdough starters",
        "harbour cranes",
        "tax deadlines",
        "orchid repotting",
        "diesel generators",
        "violin rosin",
        "kite string",
        "roof insulation",
        "ferry timetables",
    ]
    verbs = ["puzzled over", "abandoned", "measured", "catalogued", "repaired"]

    for day in range(90):
        clock.advance(days=1)
        subject = subjects[day % len(subjects)]
        verb = verbs[(day // len(subjects)) % len(verbs)]
        await capture.capture(
            episodes=[
                EpisodeCandidate(
                    title=f"Day {day}",
                    content=f"Somebody {verb} the {subject} without much enthusiasm, {day}.",
                    salience_inputs=SalienceInputs(redundancy=0.9),
                )
            ]
        )
        await maintenance.run()

    counts = store.counts()
    # Ninety low-importance captures must not become ninety permanent memories.
    assert counts["episodes"] < 90
    assert counts["forgotten"] > 0, "nothing ever reached the forget pass"
    # And the rate cap must have held throughout: at most ~2% per pass, so a quarter of
    # nightly passes cannot have emptied the store.
    assert counts["episodes"] > 0
