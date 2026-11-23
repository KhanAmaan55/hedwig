"""Nightly backups, checked before they are kept, and an offline restore (docs/05 §7, SEC-03).

An untested backup is not a backup: each copy is opened and checked before it replaces
anything, and the restore path is tested end to end. Retention is the documented 7 daily,
4 weekly and 12 monthly; one-step safety copies (before a migration, before a restore) keep
their newest three.
"""

from __future__ import annotations

import re
import shutil
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

from hedwig.core.logging import fields, get_logger
from hedwig.core.ports import Clock
from hedwig.core.ports.scheduler import TaskContext, TaskResult
from hedwig.core.store.database import Database

logger = get_logger(__name__)

NIGHTLY = re.compile(r"^hedwig-(\d{4}-\d{2}-\d{2})\.db$")
ONE_STEP = ("pre-", "pre-restore-")
KEEP_ONE_STEP = 3


class BackupError(Exception):
    """A backup or restore could not be completed; nothing was replaced."""


class Backups:
    def __init__(
        self,
        database: Database,
        directory: Path,
        *,
        clock: Clock,
        keep_daily: int = 7,
        keep_weekly: int = 4,
        keep_monthly: int = 12,
    ) -> None:
        self._db = database
        self._dir = directory
        self._clock = clock
        self._keep = (keep_daily, keep_weekly, keep_monthly)

    async def task(self, context: TaskContext) -> TaskResult:
        return TaskResult(stats=self.run())

    def run(self) -> dict[str, Any]:
        """Make today's backup if there is none, check it, then prune."""
        self._dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        stats: dict[str, Any] = {"vacuum_mode": self._compact()}
        today = self._clock.now().astimezone().date().isoformat()
        final = self._dir / f"hedwig-{today}.db"
        if final.exists() or self._db.is_memory:
            stats["made"] = False
        else:
            scratch = self._dir / f".hedwig-{today}.db.tmp"
            self._db.backup_to(scratch)
            problem = check(scratch)
            if problem:
                scratch.unlink(missing_ok=True)
                logger.error("backup failed its check", extra=fields(problem=problem))
                stats.update(made=False, failed=problem)
            else:
                scratch.replace(final)
                stats.update(made=True, bytes=final.stat().st_size)
        stats["pruned"] = self.prune()
        stats["kept"] = len(self.listing())
        return stats

    def prune(self) -> int:
        nightly: dict[date, Path] = {}
        for path in self._dir.glob("hedwig-*.db"):
            match = NIGHTLY.match(path.name)
            if match:
                nightly[date.fromisoformat(match.group(1))] = path
        days = sorted(nightly, reverse=True)
        daily, weekly, monthly = self._keep
        keep = set(days[:daily])
        keep |= set(_newest_per(days, lambda d: d.isocalendar()[:2], weekly))
        keep |= set(_newest_per(days, lambda d: (d.year, d.month), monthly))
        removed = 0
        for day, path in nightly.items():
            if day not in keep:
                path.unlink(missing_ok=True)
                removed += 1
        for prefix in ONE_STEP:
            copies = sorted(
                (p for p in self._dir.glob(f"{prefix}*.db") if not _deeper(p.name, prefix)),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for path in copies[KEEP_ONE_STEP:]:
                path.unlink(missing_ok=True)
                removed += 1
        return removed

    def listing(self) -> list[dict[str, Any]]:
        if not self._dir.exists():
            return []
        return [
            {
                "name": p.name,
                "bytes": p.stat().st_size,
                "modified": datetime.fromtimestamp(p.stat().st_mtime).astimezone().isoformat(),
            }
            for p in sorted(self._dir.glob("*.db"))
        ]

    def _compact(self) -> str:
        """Switch an old database to incremental auto-vacuum once, then reclaim free pages."""
        if self._db.is_memory:
            return "memory"
        mode = int(self._db.query_one("PRAGMA auto_vacuum")[0])  # type: ignore[index]
        if mode != 2:  # 2 = INCREMENTAL. Takes effect only through a full VACUUM.
            self._db.connection.execute("PRAGMA auto_vacuum = INCREMENTAL")
            self._db.connection.execute("VACUUM")
            logger.info("database switched to incremental auto-vacuum")
            return "switched"
        self._db.connection.execute("PRAGMA incremental_vacuum").fetchall()
        return "incremental"


def check(path: Path) -> str:
    """Empty if `path` is a sound HEDWIG database; otherwise what is wrong with it."""
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
            if integrity != ["ok"]:
                return "; ".join(integrity[:3])
            row = connection.execute("SELECT COUNT(*) FROM schema_migration").fetchone()
            if not row or row[0] == 0:
                return "no schema in it"
        finally:
            connection.close()
    except sqlite3.Error as error:
        return f"cannot be read: {error}"
    return ""


def restore(backup: Path, database: Path, *, stamp: str) -> Path | None:
    """Put `backup` in place of `database`, keeping what it replaces. HEDWIG must be stopped.

    Returns the safety copy of the replaced database, if there was one.
    """
    problem = check(backup)
    if problem:
        raise BackupError(f"That backup is not sound ({problem}); nothing was changed.")
    safety = None
    if database.exists():
        safety = backup.parent / f"pre-restore-{stamp}.db"
        current = sqlite3.connect(str(database))
        try:
            current.execute("VACUUM INTO ?", (str(safety),))  # consistent, WAL included
        finally:
            current.close()
    for suffix in ("-wal", "-shm"):
        database.with_name(database.name + suffix).unlink(missing_ok=True)
    shutil.copyfile(backup, database)
    database.chmod(0o600)
    return safety


def _newest_per(days: list[date], key: Any, count: int) -> list[date]:
    seen: dict[Any, date] = {}
    for day in days:  # newest first, so the first of each period is its newest
        seen.setdefault(key(day), day)
    return list(seen.values())[:count]


def _deeper(name: str, prefix: str) -> bool:
    """`pre-restore-…` also starts with `pre-`: count it once, under its own prefix."""
    return prefix == "pre-" and name.startswith("pre-restore-")
