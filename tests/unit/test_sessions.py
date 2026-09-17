"""The conversation store (docs/05 §5.1, docs/26 §4).

Two things are being verified here, and only one of them is obvious.

The obvious one: sessions, messages and turns are written correctly.

The other: **short-term memory is a query, not a store** (docs/06 §2). These window tests
used to live beside capture, back when the query did. They moved with it — the module that
owns the table owns the query over it — and the behaviour they assert is unchanged, which
is the point of having moved rather than rewritten them.
"""

from __future__ import annotations

import pytest

from hedwig.core.clock import FakeClock
from hedwig.core.ports.sessions import MessageRole, TurnRecord, WorkingSetRecord
from hedwig.core.store import Database
from hedwig.core.types import TrustTier
from hedwig.sessions import SqliteSessionStore


@pytest.fixture
def sessions(database: Database, clock: FakeClock) -> SqliteSessionStore:
    return SqliteSessionStore(database, clock=clock, window_size=4)


async def _seed(store: SqliteSessionStore, count: int = 12, session: str = "sess_1") -> str:
    await store.open(session, channel="cli")
    for index in range(count):
        await store.record_message(
            session_id=session,
            message_id=f"msg_{index}",
            role=MessageRole.USER if index % 2 == 0 else MessageRole.HEDWIG,
            text=f"message {index}",
        )
    return session


# =========================================================================
# Sessions
# =========================================================================


async def test_opening_a_session_says_whether_it_was_new(sessions: SqliteSessionStore) -> None:
    """`ingest` publishes `conversation.session.started` only on the first turn, so the
    answer to this question is load-bearing rather than informational."""
    assert await sessions.open("sess_1") is True
    assert await sessions.open("sess_1") is False


async def test_reopening_a_session_does_not_lose_its_history(
    sessions: SqliteSessionStore,
) -> None:
    await _seed(sessions, count=3)
    await sessions.open("sess_1")

    assert len(await sessions.window("sess_1", limit=10)) == 3


# =========================================================================
# Messages
# =========================================================================


async def test_sequence_numbers_are_dense_and_ordered(sessions: SqliteSessionStore) -> None:
    """A gap or a repeat in `seq` is a conversation that reads out of order."""
    await sessions.open("sess_1")
    seqs = [
        await sessions.record_message(
            session_id="sess_1", message_id=f"m{index}", role=MessageRole.USER, text=f"t{index}"
        )
        for index in range(5)
    ]
    assert seqs == [1, 2, 3, 4, 5]


async def test_sequences_are_per_session(sessions: SqliteSessionStore) -> None:
    await sessions.open("a")
    await sessions.open("b")
    await sessions.record_message(
        session_id="a", message_id="m1", role=MessageRole.USER, text="hello"
    )
    seq = await sessions.record_message(
        session_id="b", message_id="m2", role=MessageRole.USER, text="hello"
    )
    assert seq == 1


async def test_trust_is_stored_with_the_message(
    sessions: SqliteSessionStore, database: Database
) -> None:
    """A reply is HEDWIG's own words, not the user's, and the distinction must survive the
    round trip — only USER content may instruct (docs/13 §3)."""
    await sessions.open("sess_1")
    await sessions.record_message(
        session_id="sess_1",
        message_id="m1",
        role=MessageRole.HEDWIG,
        text="a reply",
        trust=TrustTier.SELF,
    )
    row = database.query_one("SELECT trust_tier FROM message WHERE id = 'm1'")
    assert row is not None
    assert row["trust_tier"] == "self"


# =========================================================================
# The recent-turn window (docs/06 §5.1)
# =========================================================================


async def test_the_window_is_the_last_n_messages_oldest_first(
    sessions: SqliteSessionStore,
) -> None:
    """Short-term memory is a query, not a store (docs/06 §2)."""
    await _seed(sessions)

    window = await sessions.window("sess_1")

    assert [message.text for message in window] == [
        "message 8",
        "message 9",
        "message 10",
        "message 11",
    ]


async def test_the_window_renders_verbatim(sessions: SqliteSessionStore) -> None:
    """Nothing is summarised here; that is reflection's job."""
    await _seed(sessions, count=2)

    assert sessions.render_window("sess_1") == "user: message 0\nhedwig: message 1"


async def test_an_unknown_session_has_an_empty_window(sessions: SqliteSessionStore) -> None:
    assert await sessions.window("sess_missing") == ()


def test_the_window_size_is_bounded_below(database: Database, clock: FakeClock) -> None:
    assert SqliteSessionStore(database, clock=clock, window_size=0).window_size == 1


# =========================================================================
# Turns
# =========================================================================


async def test_a_turn_round_trips(sessions: SqliteSessionStore, database: Database) -> None:
    await sessions.open("sess_1")
    await sessions.record_turn(
        TurnRecord(
            id="turn_1",
            session_id="sess_1",
            correlation_id="corr_1",
            status="completed",
            latency_ms=42,
        )
    )
    row = database.query_one("SELECT * FROM turn WHERE id = 'turn_1'")
    assert row is not None
    assert row["status"] == "completed"
    assert row["latency_ms"] == 42
    assert row["completed_at"] is not None


async def test_recording_the_same_turn_twice_updates_rather_than_duplicates(
    sessions: SqliteSessionStore, database: Database
) -> None:
    """Delivery is at-least-once and turns can resume from a checkpoint (docs/04 §2)."""
    await sessions.open("sess_1")
    for status in ("running", "completed"):
        await sessions.record_turn(
            TurnRecord(id="turn_1", session_id="sess_1", correlation_id="corr_1", status=status)
        )

    rows = database.query("SELECT status FROM turn WHERE id = 'turn_1'")
    assert len(rows) == 1
    assert rows[0]["status"] == "completed"


async def test_the_session_turn_count_follows_the_turns(
    sessions: SqliteSessionStore, database: Database
) -> None:
    await sessions.open("sess_1")
    for index in range(3):
        await sessions.record_turn(
            TurnRecord(
                id=f"turn_{index}",
                session_id="sess_1",
                correlation_id="corr",
                status="completed",
            )
        )
    row = database.query_one("SELECT turn_count FROM session WHERE id = 'sess_1'")
    assert row is not None
    assert row["turn_count"] == 3


async def test_started_at_is_derived_from_the_measured_latency(
    sessions: SqliteSessionStore, database: Database
) -> None:
    """So a caller never has to read a clock (docs/03 §5.6)."""
    await sessions.open("sess_1")
    await sessions.record_turn(
        TurnRecord(
            id="turn_1",
            session_id="sess_1",
            correlation_id="corr",
            status="completed",
            latency_ms=2000,
        )
    )
    row = database.query_one("SELECT started_at, completed_at FROM turn WHERE id = 'turn_1'")
    assert row is not None
    assert row["started_at"] < row["completed_at"]


# =========================================================================
# The working-set log (docs/16 §6)
# =========================================================================


async def test_the_working_set_log_keeps_what_was_retrieved(
    sessions: SqliteSessionStore, database: Database
) -> None:
    """ "Why did you bring that up?" has to be answerable from the database alone."""
    await sessions.record_working_set(
        WorkingSetRecord(
            id="ws_1",
            turn_id="turn_1",
            queries=("compiler project",),
            policy={"token_budget": 3000.0},
            items=({"memory_id": "ep_1", "score": 0.8},),
            token_count=120,
            dropped=3,
        )
    )
    row = database.query_one("SELECT * FROM working_set_log WHERE id = 'ws_1'")
    assert row is not None
    assert "compiler project" in row["query"]
    assert "ep_1" in row["items"]
    assert row["dropped_count"] == 3


async def test_health_notices_a_turn_that_never_finished(
    sessions: SqliteSessionStore,
) -> None:
    """A process that died mid-turn should be visible, not quietly accumulated."""
    await sessions.open("sess_1")
    await sessions.record_turn(
        TurnRecord(id="t", session_id="sess_1", correlation_id="c", status="running")
    )

    health = await sessions.health()

    assert health.status.value == "degraded"
    assert "never finished" in health.message
