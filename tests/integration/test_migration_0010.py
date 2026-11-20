"""Upgrading to one relationship subscription re-delivers nothing (migration 0010, docs/27 §5.1).

A subscription name with no delivery history is handed every event in the replay window when
recovery runs. Without 0010, the first recovery after the upgrade would tally every recent turn a
second time. This runs the upgrade the way a real install meets it: a 0009 database whose
relationship has already counted a turn, migrated, then started.

Before FND-07 this passed without 0010 too, because recovery never reached subscribers that
register in their own `start()` (docs/29 G14). Now it fails without 0010, as it should.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from hedwig.core.clock import FakeClock
from hedwig.core.config import Config
from hedwig.core.store import Database, MigrationRunner
from hedwig.llm.recorded import RecordedProvider
from hedwig.wiring import MIGRATIONS_DIR, build_container

OLD_TURNS = "memory-relationship-turns"
NOW = "2026-01-01T09:00:00.000+00:00"


def _at_0009(tmp_path: Path, clock: FakeClock) -> Database:
    old = tmp_path / "migrations-0009"
    old.mkdir()
    for script in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if script.name < "0010":
            shutil.copy(script, old / script.name)
    database = Database(tmp_path / "hedwig.db")
    MigrationRunner(database, old, clock=clock).run()
    return database


async def test_the_first_start_after_the_upgrade_tallies_nothing_twice(
    tmp_path: Path, clock: FakeClock, config: Config, llm_provider: RecordedProvider
) -> None:
    database = _at_0009(tmp_path, clock)
    # A conversation the old subscriptions already handled: one turn, tallied once.
    database.execute(
        "INSERT INTO session (id, started_at, created_at, updated_at) VALUES (?,?,?,?)",
        ("sess_old", NOW, NOW, NOW),
    )
    database.execute(
        "INSERT INTO event (id, type, occurred_at, source, correlation_id, payload) "
        "VALUES (?,?,?,?,?,?)",
        (
            "evt_01",
            "conversation.turn.completed",
            NOW,
            "brain",
            "turn_1",
            '{"turn_id": "turn_1", "session_id": "sess_old", "status": "completed"}',
        ),
    )
    database.execute(
        "INSERT INTO processed_event (subscription, event_id, status, processed_at) "
        "VALUES (?,?,?,?)",
        (OLD_TURNS, "evt_01", "ok", NOW),
    )

    MigrationRunner(database, MIGRATIONS_DIR, clock=clock).run()
    container = build_container(
        config, clock=clock, database=database, migrate=False, llm_provider=llm_provider
    )
    # The tally that turn produced. Written once the container has created the user entity it
    # references; the bus has not started, so nothing has been delivered yet.
    database.execute(
        "INSERT INTO relationship_tally (session_id, entity_id, turns, valence_sum, "
        "valence_count, updated_at) VALUES ('sess_old', 'ent_principal_local', 1, 0, 0, ?)",
        (NOW,),
    )
    await container.start()
    await container.bus.drain()

    tally = database.query_one("SELECT turns FROM relationship_tally WHERE session_id = 'sess_old'")
    await container.stop()
    assert tally is not None
    assert int(tally["turns"]) == 1, (
        "the upgrade re-delivered a turn the old subscription had counted"
    )


def test_the_migration_only_adds(tmp_path: Path, clock: FakeClock) -> None:
    database = _at_0009(tmp_path, clock)
    database.execute(
        "INSERT INTO event (id, type, occurred_at, source, correlation_id, payload) "
        "VALUES ('evt_02', 'conversation.session.ended', ?, 'sessions', 's', '{}')",
        (NOW,),
    )
    database.execute(
        "INSERT INTO processed_event (subscription, event_id, status, processed_at) "
        "VALUES ('memory-relationship-sessions', 'evt_02', 'ok', ?)",
        (NOW,),
    )

    MigrationRunner(database, MIGRATIONS_DIR, clock=clock).run()

    rows = {
        (row["subscription"], row["event_id"])
        for row in database.query("SELECT subscription, event_id FROM processed_event")
    }
    assert rows == {
        ("memory-relationship-sessions", "evt_02"),
        ("memory-relationship", "evt_02"),
    }
    database.close()
