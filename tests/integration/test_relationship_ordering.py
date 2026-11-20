"""A session's end never overtakes its turns on the way to the relationship (docs/27 §5.1).

Turns feed the relationship tally; the session's end applies it. Through two subscriptions the
two events had no order between them by contract (docs/04 §4). The in-process bus happened to
keep them in order — its dispatchers drain a queue without yielding while handlers do no real
I/O — so the race was never observed, and this test passes against the old code too. It guards
the property, which now holds by construction (one subscription, publish order) rather than by
scheduling accident.
"""

from __future__ import annotations

import json

from hedwig.wiring import Container

TURNS = 20


async def test_an_end_published_behind_a_backlog_of_turns_waits_for_them(
    running_container: Container,
) -> None:
    bus = running_container.bus
    session_id = "sess_burst"
    await running_container.sessions.open(session_id)  # the tally references its session
    for index in range(TURNS):
        await bus.emit(
            "conversation.turn.completed",
            {"turn_id": f"turn_{index}", "session_id": session_id, "status": "completed"},
            source="test",
        )
    await bus.emit(
        "conversation.session.ended",
        {"session_id": session_id, "reason": "explicit", "turn_count": TURNS},
        source="test",
    )
    await bus.drain()

    stranded = running_container.database.query(
        "SELECT turns FROM relationship_tally WHERE session_id = ?", (session_id,)
    )
    assert stranded == [], f"{stranded[0]['turns']} turns were tallied after the session closed"
    updates = running_container.database.query(
        "SELECT payload FROM event WHERE type = 'memory.relationship.updated'"
    )
    assert [json.loads(row["payload"])["turns"] for row in updates] == [TURNS]
