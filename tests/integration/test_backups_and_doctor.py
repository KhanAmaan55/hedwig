"""Nightly backups, restore, and `hedwig doctor` (SEC-03, docs/05 §7)."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from hedwig.core.clock import FakeClock
from hedwig.core.config import Config, load_config
from hedwig.core.ids import new_id
from hedwig.core.ports.memory import Belief
from hedwig.core.store import Database
from hedwig.core.store.backups import BackupError, Backups, restore
from hedwig.core.types import MemoryId
from hedwig.doctor import run_checks
from hedwig.llm.recorded import RecordedProvider
from hedwig.wiring import MIGRATIONS_DIR, build_container


def _nightly(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.glob("hedwig-*.db"))


async def test_a_backup_is_made_once_a_day_and_checked(
    config: Config, clock: FakeClock, llm_provider: RecordedProvider
) -> None:
    container = build_container(config, clock=clock, llm_provider=llm_provider)
    backups = Backups(container.database, config.runtime.backups_dir, clock=clock)

    first = backups.run()
    again = backups.run()

    assert first["made"] is True or _nightly(config.runtime.backups_dir), "made at start or now"
    assert again["made"] is False
    (name,) = _nightly(config.runtime.backups_dir)
    connection = sqlite3.connect(config.runtime.backups_dir / name)
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    connection.close()
    container.database.close()


def test_retention_keeps_seven_days_four_weeks_and_twelve_months(
    tmp_path: Path, clock: FakeClock
) -> None:
    folder = tmp_path / "backups"
    folder.mkdir()
    today = date(2026, 11, 23)
    for back in range(400):
        (folder / f"hedwig-{(today - timedelta(days=back)).isoformat()}.db").write_bytes(b"")
    for n in range(6):
        (folder / f"pre-00{n:02d}-x.db").write_bytes(b"")

    Backups(Database(":memory:"), folder, clock=clock).prune()

    kept = sorted(date.fromisoformat(n[7:17]) for n in _nightly(folder))
    assert sorted(today - timedelta(days=d) for d in range(7)) == kept[-7:]
    assert len(kept) <= 7 + 4 + 12 and min(kept) >= today - timedelta(days=366)
    assert len({(d.year, d.month) for d in kept}) == 12
    assert len(list(folder.glob("pre-*.db"))) == 3


async def test_a_restore_puts_the_backup_back_and_keeps_what_it_replaced(
    config: Config, clock: FakeClock, llm_provider: RecordedProvider
) -> None:
    container = build_container(config, clock=clock, llm_provider=llm_provider)
    await container.start()
    before = MemoryId(new_id("bel"))
    await container.memory.add_belief(Belief(id=before, statement="Kept.", valid_from=clock.now()))
    clock.advance(days=1)
    Backups(container.database, config.runtime.backups_dir, clock=clock).run()
    after = MemoryId(new_id("bel"))
    await container.memory.add_belief(Belief(id=after, statement="Later.", valid_from=clock.now()))
    await container.stop()

    (name,) = [
        n for n in _nightly(config.runtime.backups_dir) if clock.now().date().isoformat() in n
    ]
    safety = restore(config.runtime.backups_dir / name, config.database_path, stamp="t1")

    restored = sqlite3.connect(config.database_path)
    statements = {r[0] for r in restored.execute("SELECT statement FROM belief")}
    restored.close()
    assert statements == {"Kept."}
    assert safety is not None
    kept = sqlite3.connect(safety)
    assert {r[0] for r in kept.execute("SELECT statement FROM belief")} == {"Kept.", "Later."}
    kept.close()


def test_an_unsound_backup_is_never_restored(tmp_path: Path) -> None:
    broken = tmp_path / "hedwig-2026-11-01.db"
    broken.write_bytes(b"not a database at all, just bytes")
    live = tmp_path / "hedwig.db"
    live.write_bytes(b"the current one")
    with pytest.raises(BackupError):
        restore(broken, live, stamp="t")
    assert live.read_bytes() == b"the current one"


def test_auto_vacuum_takes_effect_on_a_new_database(tmp_path: Path) -> None:
    database = Database(tmp_path / "fresh.db")
    assert int(database.query_one("PRAGMA auto_vacuum")[0]) == 2  # type: ignore[index]
    database.close()


def test_an_old_database_is_switched_once(tmp_path: Path, clock: FakeClock) -> None:
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("PRAGMA journal_mode = WAL")
    old.execute("CREATE TABLE schema_migration (version INTEGER)")
    old.close()
    database = Database(path)
    assert int(database.query_one("PRAGMA auto_vacuum")[0]) == 0  # type: ignore[index]

    first = Backups(database, tmp_path / "b", clock=clock).run()
    second = Backups(database, tmp_path / "b", clock=clock).run()

    assert (first["vacuum_mode"], second["vacuum_mode"]) == ("switched", "incremental")
    database.close()


def test_the_doctor_says_what_is_wrong_and_what_to_do(tmp_path: Path) -> None:
    data = tmp_path / "Dropbox" / "hedwig"
    data.mkdir(parents=True)
    data.chmod(0o755)
    (data / "token").write_text("secret")
    (data / "token").chmod(0o644)
    config = load_config(
        runtime={"data_dir": str(data)},
        llm={"endpoint": "http://127.0.0.1:9"},
        api={"port": 9},
    )

    findings = {
        f.name: f for f in run_checks(config, migrations=MIGRATIONS_DIR, today=date(2026, 11, 23))
    }

    assert findings["data folder"].fix == f"chmod 700 {data}"
    assert findings["not in a synced folder"].status == "warn"
    assert findings["API token"].status == "fail" and "chmod 600" in findings["API token"].fix
    assert findings["Ollama"].status == "fail" and findings["Ollama"].fix
    assert findings["backups"].status == "warn"
    assert findings["HEDWIG"].detail.startswith("not running")
    assert findings["recall by meaning"].status == "ok"


def test_the_doctor_reads_the_last_week(tmp_path: Path) -> None:
    """OBS-01: failed turns and dead letters from the records, with where to look next."""
    config = load_config(runtime={"data_dir": str(tmp_path)}, api={"port": 9})
    database = Database(config.database_path)
    clock = FakeClock("2026-11-20T09:00:00+00:00")
    from hedwig.core.store import MigrationRunner

    MigrationRunner(database, MIGRATIONS_DIR, clock=clock).run()
    database.execute(
        "INSERT INTO session (id, channel, started_at, created_at, updated_at) "
        "VALUES ('s','web','2026-11-20','2026-11-20','2026-11-20')"
    )
    for index, (status, ms) in enumerate([("completed", 3000), ("failed", 9000)]):
        database.execute(
            "INSERT INTO turn (id, session_id, correlation_id, started_at, status, latency_ms, "
            "created_at) VALUES (?, 's', ?, '2026-11-20T09:00:00', ?, ?, '2026-11-20')",
            (f"turn_{index}", f"turn_{index}", status, ms),
        )
    database.close()

    findings = {
        f.name: f for f in run_checks(config, migrations=MIGRATIONS_DIR, today=date(2026, 11, 23))
    }

    week = findings["last 7 days"]
    assert week.status == "warn" and week.detail.startswith("2 turns, 1 failed")
    assert "hedwig trace" in week.fix


def test_dead_letters_warn_even_in_a_week_with_no_conversations(tmp_path: Path) -> None:
    config = load_config(runtime={"data_dir": str(tmp_path)}, api={"port": 9})
    database = Database(config.database_path)
    from hedwig.core.store import MigrationRunner

    MigrationRunner(database, MIGRATIONS_DIR, clock=FakeClock("2026-11-20T09:00:00+00:00")).run()
    database.execute(
        "INSERT INTO event (id, type, occurred_at, source, correlation_id, payload) "
        "VALUES ('evt_1', 'memory.episode.stored', '2026-11-20', 'memory', 'c', '{}')"
    )
    database.execute(
        "INSERT INTO event_dead_letter (id, subscription, event_id, attempts, error, created_at) "
        "VALUES ('dl_1', 'memory.embed', 'evt_1', 5, 'boom', '2026-11-21')"
    )
    database.close()

    findings = {
        f.name: f for f in run_checks(config, migrations=MIGRATIONS_DIR, today=date(2026, 11, 23))
    }

    week = findings["last 7 days"]
    assert week.status == "warn" and week.detail == "0 turns, 0 failed, p95 0.0 s, 1 dead letters"
