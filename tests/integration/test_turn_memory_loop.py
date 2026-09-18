"""The turn/memory loop, end to end (docs/26).

Observe → Retrieve → Inject → Generate response context → Store → Persist.

Everything here runs against a real container: a real graph, a real event bus, a real
database, a `FakeClock` and a scripted model provider. Nothing is patched. That is what the
composition root is for (docs/20 §3.4), and it is the only way to test a loop whose most
important property — *that a second turn knows what the first one said* — is invisible from
inside any single component.

`await container.bus.drain()` appears throughout because capture is a subscriber, not a
node (docs/07 §3.1). Draining is the test's way of saying "and then the background work
finished"; in production nobody waits.
"""

from __future__ import annotations

from typing import Any

import pytest

from hedwig.core.store import Database
from hedwig.wiring import Container


@pytest.fixture
async def hedwig(running_container: Container) -> Container:
    return running_container


def _rows(
    database: Database, sql: str, parameters: tuple[object, ...] = ()
) -> list[dict[str, Any]]:
    return [dict(row) for row in database.query(sql, parameters)]


# =========================================================================
# The loop
# =========================================================================


async def test_a_second_turn_knows_what_the_first_one_said(hedwig: Container) -> None:
    """The milestone's whole claim, in one test.

    Before Milestone 6 this was impossible: the graph recalled from memory but never wrote
    to it, so every conversation was the first conversation.
    """
    await hedwig.brain.run_turn(
        "Please remember that I am writing a compiler in Rust called Fernweh.",
        session_id="sess_loop",
    )
    await hedwig.bus.drain()

    second = await hedwig.brain.run_turn(
        "what do you know about my compiler?", session_id="sess_loop"
    )

    recalled = " ".join(item.text for item in second["context"].items)
    assert "Fernweh" in recalled, "the turn did not recall what the previous turn taught it"
    assert second["status"] == "completed"


async def test_one_turn_leaves_three_durable_traces(hedwig: Container) -> None:
    """The conversation log, the turn record, and a memory (docs/26 §1)."""
    await hedwig.brain.run_turn(
        "My sister Ana moved to Porto last spring and loves it.", session_id="sess_trace"
    )
    await hedwig.bus.drain()

    database = hedwig.database
    assert len(_rows(database, "SELECT * FROM session WHERE id = 'sess_trace'")) == 1
    assert len(_rows(database, "SELECT * FROM message WHERE session_id = 'sess_trace'")) == 2
    assert len(_rows(database, "SELECT * FROM turn WHERE session_id = 'sess_trace'")) == 1
    assert hedwig.memory.counts()["episodes"] == 1


# =========================================================================
# Observe
# =========================================================================


async def test_the_user_message_is_recorded_before_the_reply_exists(hedwig: Container) -> None:
    """Written at `ingest`, not at the end. A record that only exists if the turn succeeded
    is not a record (docs/05 §7)."""
    await hedwig.brain.run_turn("a thing I said", session_id="sess_observe")

    messages = _rows(
        hedwig.database,
        "SELECT role, text, seq FROM message WHERE session_id = ? ORDER BY seq",
        ("sess_observe",),
    )
    assert messages[0]["role"] == "user"
    assert messages[0]["text"] == "a thing I said"
    assert messages[1]["role"] == "hedwig"


async def test_a_session_is_opened_once_across_turns(hedwig: Container) -> None:
    for index in range(3):
        await hedwig.brain.run_turn(f"message number {index}", session_id="sess_once")

    sessions = _rows(hedwig.database, "SELECT turn_count FROM session WHERE id = 'sess_once'")
    assert len(sessions) == 1
    assert sessions[0]["turn_count"] == 3


async def test_the_turn_is_announced_on_the_bus(hedwig: Container) -> None:
    """Four conversation events, each registered in the catalogue (docs/26 §7.1)."""
    await hedwig.brain.run_turn("hello there, this is the first thing", session_id="sess_events")
    await hedwig.bus.drain()

    types = [
        row["type"]
        for row in _rows(hedwig.database, "SELECT type FROM event WHERE type LIKE 'conversation.%'")
    ]
    assert "conversation.session.started" in types
    assert "conversation.message.received" in types
    assert "conversation.reply.produced" in types
    assert "conversation.turn.completed" in types


# =========================================================================
# Retrieve and inject
# =========================================================================


async def test_the_window_reaches_the_turn_even_with_nothing_recalled(
    hedwig: Container,
) -> None:
    """Retrieval returning nothing must not cost the model the current conversation
    (docs/06 §5.1)."""
    await hedwig.brain.run_turn("first thing said in this session", session_id="sess_window")
    second = await hedwig.brain.run_turn("second thing said", session_id="sess_window")

    window_text = " ".join(message.text for message in second["window"])
    assert "first thing said in this session" in window_text


async def test_the_window_is_the_conversation_and_not_the_memories(hedwig: Container) -> None:
    """They are kept apart all the way into state, because the budget applies to one and
    not the other."""
    await hedwig.brain.run_turn("something about harbour cranes", session_id="sess_apart")
    await hedwig.bus.drain()
    second = await hedwig.brain.run_turn("more about harbour cranes", session_id="sess_apart")

    assert second["window"], "the window was empty on the second turn"
    assert second["context"].items, "retrieval found nothing to recall"
    assert second["window"][0].role in {"user", "hedwig"}


# =========================================================================
# Generate the response context
# =========================================================================


async def test_the_response_context_carries_both_sources(hedwig: Container) -> None:
    await hedwig.brain.run_turn("the ferry timetable changed in March", session_id="sess_ctx")
    await hedwig.bus.drain()
    second = await hedwig.brain.run_turn(
        "what happened to the ferry timetable?", session_id="sess_ctx"
    )

    context = second["response_context"]
    assert context.window
    assert context.recalled
    assert context.cited_memory_ids
    assert context.token_count == context.window_tokens + context.recalled_tokens


# =========================================================================
# Store and persist
# =========================================================================


async def test_an_explicit_request_to_remember_produces_a_stronger_memory(
    hedwig: Container,
) -> None:
    """docs/06 §4.2: the user asking is the strongest signal there is."""
    await hedwig.brain.run_turn(
        "Please remember that my passport expires in November.", session_id="sess_flag"
    )
    await hedwig.brain.run_turn(
        "The bicycle gears were making an odd noise this morning.", session_id="sess_flag"
    )
    await hedwig.bus.drain()

    episodes = _rows(
        hedwig.database,
        "SELECT title, base_importance FROM episode ORDER BY base_importance DESC",
    )
    assert len(episodes) == 2
    assert "passport" in episodes[0]["title"]


async def test_the_turn_record_links_both_messages(hedwig: Container) -> None:
    await hedwig.brain.run_turn("something worth linking up", session_id="sess_link")

    turn = _rows(hedwig.database, "SELECT * FROM turn WHERE session_id = 'sess_link'")[0]
    messages = {
        row["id"]
        for row in _rows(hedwig.database, "SELECT id FROM message WHERE session_id = 'sess_link'")
    }
    assert turn["user_message_id"] in messages
    assert turn["reply_message_id"] in messages
    assert turn["status"] == "completed"
    assert turn["latency_ms"] is not None


async def test_what_was_retrieved_is_explainable_afterwards(hedwig: Container) -> None:
    """ "Why did you bring that up?" answered from the database alone (docs/16 §6)."""
    await hedwig.brain.run_turn("orchid repotting went badly this year", session_id="sess_explain")
    await hedwig.bus.drain()
    await hedwig.brain.run_turn("tell me about the orchid repotting", session_id="sess_explain")

    logs = _rows(hedwig.database, "SELECT * FROM working_set_log")
    assert logs, "nothing recorded what retrieval did"
    assert "orchid" in logs[-1]["query"]

    turn = _rows(
        hedwig.database,
        "SELECT working_set_id FROM turn WHERE session_id = 'sess_explain' "
        "ORDER BY started_at DESC",
    )[0]
    assert turn["working_set_id"] is not None


async def test_a_refused_turn_leaves_a_record_but_teaches_nothing(hedwig: Container) -> None:
    """The turn happened and is recorded; capturing HEDWIG's own refusal as though the user
    had said it would be worse than remembering nothing."""
    from dataclasses import replace

    from hedwig.brain import Brain, PermissiveGuard

    guarded = Brain(
        replace(
            hedwig.brain.collaborators,
            guard=PermissiveGuard(blocked_phrases=frozenset({"forbidden"})),
        )
    )
    await guarded.run_turn("this contains a forbidden phrase", session_id="sess_refused")
    await hedwig.bus.drain()

    turn = _rows(hedwig.database, "SELECT status FROM turn WHERE session_id = 'sess_refused'")[0]
    assert turn["status"] == "refused"
    assert hedwig.memory.counts()["episodes"] == 0


# =========================================================================
# Feedback into memory
# =========================================================================


async def test_a_memory_that_is_recalled_and_shown_grows_stronger(hedwig: Container) -> None:
    """Retrieved, then cited: the two reinforcement signals this milestone can observe
    (docs/06 §7.2)."""
    await hedwig.brain.run_turn(
        "The sourdough starter finally doubled overnight.", session_id="sess_reinforce"
    )
    await hedwig.bus.drain()

    before = _rows(hedwig.database, "SELECT id, salience FROM episode")[0]

    await hedwig.brain.run_turn("how is the sourdough starter?", session_id="sess_reinforce")
    await hedwig.bus.drain()

    after = _rows(
        hedwig.database, "SELECT salience, access_count FROM episode WHERE id = ?", (before["id"],)
    )[0]
    assert after["salience"] > before["salience"]
    assert after["access_count"] > 0


async def test_health_reports_the_loop_as_connected(hedwig: Container) -> None:
    """`recaller`, `conversation` and `announcer` are no longer stubs — and as of the
    emotion engine, neither is `mind`. What remains stubbed is stated rather than hidden
    (docs/07 §12.2)."""
    health = await hedwig.brain.health()

    assert set(health.detail["stubbed"]) == {"guard", "responder", "tools"}
