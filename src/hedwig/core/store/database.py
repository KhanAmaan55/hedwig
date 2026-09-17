"""The database connection.

Thin by intent. It owns pragmas, transactions and error translation, and nothing else —
no query builder, no ORM, no schema knowledge. The schema is the design and we want to
read it (docs/05 §3, docs/02 §9).

**On blocking.** These calls are synchronous. HEDWIG runs one asyncio loop, so in principle
a disk read blocks it; in practice a WAL-mode SQLite statement against a local file is tens
of microseconds, which is far below the point where it matters (docs/17 §2.1). The escape
hatch, if profiling ever disagrees, is `asyncio.to_thread` inside this class — no caller
changes.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Self

from hedwig.core.errors import ConflictError, HedwigError
from hedwig.core.logging import fields, get_logger

logger = get_logger(__name__)

Transaction = sqlite3.Connection
"""A transaction is just the connection inside a `with database.transaction()` block."""

PRAGMAS: tuple[tuple[str, str], ...] = (
    ("journal_mode", "WAL"),  # concurrent readers alongside one writer
    ("synchronous", "NORMAL"),  # safe under WAL; FULL only for the nightly backup
    ("foreign_keys", "ON"),  # a dangling reference in a memory system is a false memory
    ("busy_timeout", "5000"),
    ("temp_store", "MEMORY"),
    ("mmap_size", "268435456"),
    ("auto_vacuum", "INCREMENTAL"),
)


class StorageError(HedwigError):
    """The database is unreachable, corrupt, or refused an operation."""

    code = "storage_unavailable"
    http_status = 503
    retryable = True


class IntegrityViolationError(ConflictError):
    """A constraint rejected the write — usually a duplicate or a dangling reference."""

    code = "integrity_violation"


class Database:
    """A connection to one SQLite file.

    Connections are per-thread (SQLite objects are not shareable across threads), created
    lazily. In the normal single-loop case there is exactly one.
    """

    def __init__(self, path: Path | str, *, read_only: bool = False) -> None:
        self.path = Path(path) if path != ":memory:" else Path(":memory:")
        self._read_only = read_only
        self._local = threading.local()
        self._shared_memory: sqlite3.Connection | None = None
        self._closed = False

    # -- lifecycle ---------------------------------------------------------

    @property
    def is_memory(self) -> bool:
        return str(self.path) == ":memory:"

    @property
    def connection(self) -> sqlite3.Connection:
        if self._closed:
            raise StorageError("database is closed")

        # An in-memory database exists only inside its connection, so it cannot be
        # per-thread: every caller must share one. This is the test configuration.
        if self.is_memory:
            if self._shared_memory is None:
                self._shared_memory = self._connect()
            return self._shared_memory

        existing: sqlite3.Connection | None = getattr(self._local, "connection", None)
        if existing is None:
            existing = self._connect()
            self._local.connection = existing
        return existing

    def _connect(self) -> sqlite3.Connection:
        if self.is_memory:
            uri, use_uri = "file::memory:?cache=shared", True
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            uri, use_uri = f"file:{self.path}{'?mode=ro' if self._read_only else ''}", True

        try:
            connection = sqlite3.connect(
                uri,
                uri=use_uri,
                # Transactions are explicit; autocommit-style control is ours.
                isolation_level=None,
                check_same_thread=False,
                timeout=5.0,
            )
        except sqlite3.Error as exc:  # pragma: no cover - depends on the filesystem
            raise StorageError(f"cannot open database at {self.path}: {exc}") from exc

        connection.row_factory = sqlite3.Row
        for pragma, value in PRAGMAS:
            if self._read_only and pragma in {"journal_mode", "auto_vacuum"}:
                continue
            connection.execute(f"PRAGMA {pragma} = {value}")

        logger.debug("database connection opened", extra=fields(path=str(self.path)))
        return connection

    def close(self) -> None:
        for connection in (getattr(self._local, "connection", None), self._shared_memory):
            if connection is not None:
                connection.close()
        self._local = threading.local()
        self._shared_memory = None
        self._closed = True

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # -- transactions ------------------------------------------------------

    @contextmanager
    def transaction(self, *, immediate: bool = True) -> Iterator[sqlite3.Connection]:
        """Run a block in one transaction, committing on success and rolling back on error.

        `immediate` takes the write lock up front. That converts the "two writers collide
        halfway through" case into a clean failure at the start, which is much easier to
        reason about than a partially-applied transaction that has to be unwound.

        Nested calls join the outer transaction rather than opening a second one.
        """
        connection = self.connection
        if connection.in_transaction:
            yield connection  # already inside one; the outermost block owns commit
            return

        connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        try:
            yield connection
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise IntegrityViolationError(str(exc)) from exc
        except sqlite3.Error as exc:
            connection.rollback()
            raise StorageError(str(exc)) from exc
        except BaseException:
            connection.rollback()
            raise
        else:
            connection.commit()

    # -- queries -----------------------------------------------------------

    def execute(self, sql: str, parameters: Sequence[Any] | dict[str, Any] = ()) -> int:
        """Run a statement, returning the number of affected rows."""
        with self.transaction() as connection:
            cursor = connection.execute(sql, parameters)
            return cursor.rowcount

    def execute_many(self, sql: str, rows: Sequence[Sequence[Any]]) -> int:
        with self.transaction() as connection:
            cursor = connection.executemany(sql, rows)
            return cursor.rowcount

    def query(self, sql: str, parameters: Sequence[Any] | dict[str, Any] = ()) -> list[sqlite3.Row]:
        try:
            return self.connection.execute(sql, parameters).fetchall()
        except sqlite3.Error as exc:
            raise StorageError(f"{exc} (query: {sql.strip().splitlines()[0]})") from exc

    def query_one(
        self, sql: str, parameters: Sequence[Any] | dict[str, Any] = ()
    ) -> sqlite3.Row | None:
        try:
            row: sqlite3.Row | None = self.connection.execute(sql, parameters).fetchone()
        except sqlite3.Error as exc:
            raise StorageError(f"{exc} (query: {sql.strip().splitlines()[0]})") from exc
        return row

    # -- maintenance -------------------------------------------------------

    def integrity_check(self) -> list[str]:
        """Empty list means healthy. Anything else and HEDWIG refuses to start (docs/17 §3)."""
        problems = [row[0] for row in self.query("PRAGMA integrity_check")]
        return [] if problems == ["ok"] else problems

    def foreign_key_check(self) -> list[sqlite3.Row]:
        return self.query("PRAGMA foreign_key_check")

    def backup_to(self, destination: Path) -> Path:
        """Snapshot the database while it is in use.

        `VACUUM INTO` is consistent without stopping writers, which is what makes the
        automatic pre-migration backup safe (docs/05 §6 rule 3).
        """
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if destination.exists():
            destination.unlink()
        self.connection.execute("VACUUM INTO ?", (str(destination),))
        logger.info("database backed up", extra=fields(destination=str(destination)))
        return destination

    def size_bytes(self) -> int:
        row = self.query_one(
            "SELECT page_count * page_size AS size FROM pragma_page_count(), pragma_page_size()"
        )
        return int(row["size"]) if row else 0
