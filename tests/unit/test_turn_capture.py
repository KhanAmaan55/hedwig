"""Learning from a finished turn (docs/26 §6).

The candidate builder is a pure function and gets pure tests. The subscriber gets a real
store, because what it is for is writing — a subscriber tested against a fake store would
verify that it called a method, not that anything was remembered.
"""

from __future__ import annotations

import pytest

from hedwig.core.clock import FakeClock
from hedwig.core.ids import new_id
from hedwig.core.ports import Event
from hedwig.core.ports.memory import Episode, EpisodeKind
from hedwig.core.store import Database
from hedwig.core.types import MemoryId, TrustTier
from hedwig.memory import CaptureService, SqliteMemoryStore, TurnCaptureSubscriber
from hedwig.memory.turn_capture import candidates_from_turn
from hedwig.sessions import SqliteSessionStore


def _event(**payload: object) -> Event:
    defaults: dict[str, object] = {
        "turn_id": "turn_1",
        "session_id": "sess_1",
        "status": "completed",
        "input": "I am writing a compiler in Rust and enjoying the borrow checker.",
        "reply": "That sounds like a good way to learn lifetimes.",
        "intent": "answer",
        "recalled_memory_ids": [],
        "tool_calls": 0,
        "latency_ms": 12.0,
    }
    defaults.update(payload)
    return Event(
        id=new_id("evt"),
        type="conversation.turn.completed",
        occurred_at=FakeClock("2026-01-01T09:00:00+00:00").now(),
        source="brain",
        correlation_id="turn_1",
        payload=defaults,
    )


@pytest.fixture
def store(database: Database, clock: FakeClock) -> SqliteMemoryStore:
    return SqliteMemoryStore(database, clock=clock)


@pytest.fixture
async def subscriber(
    store: SqliteMemoryStore, clock: FakeClock, database: Database
) -> TurnCaptureSubscriber:
    # The session row is capture's one database prerequisite (ADR-0018). In a running
    # system `ingest` writes it at the start of the turn; here it is set up explicitly,
    # because an episode pointing at a session that does not exist is a false memory and
    # the foreign key is there to say so (docs/05 §3).
    await SqliteSessionStore(database, clock=clock).open("sess_1")
    return TurnCaptureSubscriber(CaptureService(store, clock=clock), store)


# =========================================================================
# What a turn offers
# =========================================================================


def test_a_turn_offers_one_episode_of_the_exchange() -> None:
    candidates = candidates_from_turn(input_text="Where is Lisbon?", reply="In Portugal.")

    assert len(candidates) == 1
    assert candidates[0].kind is EpisodeKind.INTERACTION
    assert "Where is Lisbon?" in candidates[0].content
    assert "In Portugal." in candidates[0].content


def test_an_empty_input_offers_nothing() -> None:
    assert candidates_from_turn(input_text="   ", reply="anything") == ()


def test_a_turn_with_no_reply_still_records_what_was_said() -> None:
    """The message log is the raw material for every later extraction (docs/05 §7)."""
    candidates = candidates_from_turn(input_text="My sister is called Ana.", reply="")

    assert candidates[0].content == "User: My sister is called Ana."


@pytest.mark.parametrize(
    "text",
    [
        "Remember that my sister is called Ana",
        "Please don't forget I am allergic to peanuts",
        "Keep in mind that I work nights",
        "Note that the deploy is on Friday",
        "For future reference, my timezone is WEST",
    ],
)
def test_an_explicit_request_to_remember_is_flagged(text: str) -> None:
    """The strongest capture signal there is, and the only one readable without a model
    (docs/06 §4.2)."""
    assert candidates_from_turn(input_text=text, reply="ok")[0].salience_inputs.user_flagged


def test_an_ordinary_message_is_not_flagged() -> None:
    candidate = candidates_from_turn(input_text="What time is the train?", reply="Six.")[0]

    assert candidate.salience_inputs.user_flagged is False


def test_the_content_carries_the_users_trust_tier() -> None:
    """A past user message is still the user speaking (docs/13 §3)."""
    assert candidates_from_turn(input_text="a fact about me", reply="noted")[0].trust is (
        TrustTier.USER
    )


def test_a_long_input_gets_a_readable_title_and_a_complete_body() -> None:
    """The title is for a human scanning a list; nothing is lost from the content."""
    text = "I have been thinking about " + "the retrieval subsystem " * 10
    candidate = candidates_from_turn(input_text=text, reply="ok")[0]

    assert len(candidate.title) <= 72
    assert candidate.title.endswith("…")
    assert text.strip() in candidate.content


def test_nothing_a_cognitive_engine_would_supply_is_invented() -> None:
    """Emotion, goals and entities do not exist yet, and a fabricated signal is worse than
    an absent one (docs/26 §6.1)."""
    inputs = candidates_from_turn(input_text="Remember I like jazz", reply="ok")[0].salience_inputs

    assert inputs.emotional_charge == 0.0
    assert inputs.goal_relevance == 0.0
    assert inputs.entity_centrality == 0.0


# =========================================================================
# The subscriber
# =========================================================================


async def test_a_completed_turn_becomes_a_memory(
    subscriber: TurnCaptureSubscriber, store: SqliteMemoryStore
) -> None:
    await subscriber.handle(_event())

    assert store.counts()["episodes"] == 1


@pytest.mark.parametrize("status", ["refused", "failed", "running"])
async def test_a_turn_that_did_not_complete_teaches_nothing(
    subscriber: TurnCaptureSubscriber, store: SqliteMemoryStore, status: str
) -> None:
    """Capturing a refusal would store HEDWIG's own decline as though the user had said
    it."""
    await subscriber.handle(_event(status=status))

    assert store.counts()["episodes"] == 0


async def test_a_truncated_turn_still_teaches(
    subscriber: TurnCaptureSubscriber, store: SqliteMemoryStore
) -> None:
    """A cap was hit, but the exchange still happened (docs/07 §3.2)."""
    await subscriber.handle(_event(status="truncated"))

    assert store.counts()["episodes"] == 1


async def test_the_same_turn_twice_reinforces_rather_than_duplicates(
    subscriber: TurnCaptureSubscriber, store: SqliteMemoryStore
) -> None:
    """Delivery is at-least-once, so a handler must be idempotent (docs/04 §2). The bus
    dedupes by event id; the novelty gate is the second line of defence."""
    event = _event()
    await subscriber.handle(event)
    await subscriber.handle(event)

    assert store.counts()["episodes"] == 1


async def test_an_empty_turn_writes_nothing(
    subscriber: TurnCaptureSubscriber, store: SqliteMemoryStore
) -> None:
    await subscriber.handle(_event(input="", reply=""))

    assert store.counts()["episodes"] == 0


# =========================================================================
# Reinforcement
# =========================================================================


async def test_a_memory_shown_in_a_reply_is_reinforced(
    subscriber: TurnCaptureSubscriber, store: SqliteMemoryStore, clock: FakeClock
) -> None:
    """docs/06 §7.2: use is what keeps a memory alive."""
    memory_id = await store.add_episode(
        Episode(
            id=MemoryId("ep_seed"),
            occurred_at=clock.now(),
            title="Seed",
            content="Something previously remembered about compilers.",
            salience=0.5,
            base_importance=0.5,
        )
    )

    await subscriber.handle(_event(recalled_memory_ids=[str(memory_id)]))

    after = await store.get_episode(memory_id)
    assert after is not None
    assert after.salience > 0.5


async def test_nothing_is_reinforced_when_no_reply_was_produced(
    subscriber: TurnCaptureSubscriber, store: SqliteMemoryStore, clock: FakeClock
) -> None:
    """ "Cited" means shown *in a reply*. No reply, no evidence of use."""
    memory_id = await store.add_episode(
        Episode(
            id=MemoryId("ep_seed"),
            occurred_at=clock.now(),
            title="Seed",
            content="Something previously remembered about compilers.",
            salience=0.5,
            base_importance=0.5,
        )
    )

    await subscriber.handle(_event(reply="", recalled_memory_ids=[str(memory_id)]))

    after = await store.get_episode(memory_id)
    assert after is not None
    assert after.salience == 0.5


async def test_a_malformed_recalled_list_does_not_break_the_handler(
    subscriber: TurnCaptureSubscriber, store: SqliteMemoryStore
) -> None:
    """Payloads come off a bus. A handler that trusts their shape is a handler that
    dead-letters the first time something changes."""
    await subscriber.handle(_event(recalled_memory_ids="ep_1"))

    assert store.counts()["episodes"] == 1
