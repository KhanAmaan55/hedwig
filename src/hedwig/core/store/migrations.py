"""Forward-only SQL migrations (docs/05 §6).

Numbered `.sql` files plus a runner small enough to read in one sitting. No framework:
we have one linear history and one database, which is precisely the situation Alembic's
complexity does not pay for.

The rules, all enforced below:

1. **Forward only.** Rollback is restore-from-backup, because that is what actually happens
   when a migration goes wrong. Untested down-migrations are theatre.
2. **One transaction per file.** A failed migration leaves nothing behind.
3. **Checksums.** Editing an applied migration is a startup error, not a mystery next month.
4. **Automatic backup first.** The user's memory is at stake and disk is cheap.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from hedwig.core.clock import SystemClock
from hedwig.core.errors import HedwigError
from hedwig.core.logging import fields, get_logger
from hedwig.core.ports import Clock
from hedwig.core.store.database import Database

logger = get_logger(__name__)

MIGRATION_PATTERN = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")

SCHEMA_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migration (
    version    INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    checksum   TEXT NOT NULL,
    applied_at TEXT NOT NULL
)
"""


class MigrationError(HedwigError):
    """A migration is missing, malformed, modified after application, or failed."""

    code = "migration_failed"
    http_status = 500


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    path: Path
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()[:16]

    def __str__(self) -> str:
        return f"{self.version:04d}_{self.name}"


def discover(directory: Path) -> list[Migration]:
    """Read migration files in version order, rejecting anything ambiguous."""
    if not directory.is_dir():
        raise MigrationError(f"migration directory not found: {directory}")

    migrations: list[Migration] = []
    seen: dict[int, str] = {}

    for path in sorted(directory.glob("*.sql")):
        match = MIGRATION_PATTERN.match(path.name)
        if not match:
            raise MigrationError(
                f"migration filename must be NNNN_lower_snake.sql, got {path.name!r}"
            )
        version, name = int(match.group(1)), match.group(2)
        if version in seen:
            raise MigrationError(f"duplicate migration version {version}: {seen[version]}, {name}")
        seen[version] = name
        migrations.append(
            Migration(version=version, name=name, path=path, sql=path.read_text("utf-8"))
        )

    expected = list(range(1, len(migrations) + 1))
    if [migration.version for migration in migrations] != expected:
        raise MigrationError(
            f"migration versions must be contiguous from 0001; found {seen.keys()}"
        )
    return migrations


def applied_versions(database: Database) -> dict[int, str]:
    """Version → checksum for everything already applied."""
    database.execute(SCHEMA_TABLE)
    return {
        int(row["version"]): str(row["checksum"])
        for row in database.query("SELECT version, checksum FROM schema_migration")
    }


class MigrationRunner:
    """Applies pending migrations, backing up first."""

    def __init__(
        self,
        database: Database,
        directory: Path,
        *,
        clock: Clock | None = None,
        backup_dir: Path | None = None,
    ) -> None:
        self._database = database
        self._directory = directory
        self._clock = clock or SystemClock()
        self._backup_dir = backup_dir

    def pending(self) -> list[Migration]:
        applied = applied_versions(self._database)
        available = discover(self._directory)

        for migration in available:
            recorded = applied.get(migration.version)
            if recorded is not None and recorded != migration.checksum:
                raise MigrationError(
                    f"migration {migration} was modified after it was applied "
                    f"(recorded {recorded}, file {migration.checksum}). "
                    "Migrations are immutable; add a new one instead."
                )
        return [migration for migration in available if migration.version not in applied]

    def current_version(self) -> int:
        applied = applied_versions(self._database)
        return max(applied, default=0)

    def run(self) -> list[Migration]:
        """Apply everything pending. Returns what was applied, newest last."""
        pending = self.pending()
        if not pending:
            logger.debug("schema up to date", extra=fields(version=self.current_version()))
            return []

        self._backup_before(pending)

        applied: list[Migration] = []
        for migration in pending:
            logger.info("applying migration", extra=fields(migration=str(migration)))
            try:
                with self._database.transaction() as connection:
                    connection.executescript(migration.sql)
                    connection.execute(
                        "INSERT INTO schema_migration (version, name, checksum, applied_at) "
                        "VALUES (?, ?, ?, ?)",
                        (
                            migration.version,
                            migration.name,
                            migration.checksum,
                            self._clock.now().isoformat(timespec="milliseconds"),
                        ),
                    )
            except Exception as exc:
                raise MigrationError(
                    f"migration {migration} failed and was rolled back: {exc}"
                ) from exc
            applied.append(migration)

        logger.info(
            "schema migrated",
            extra=fields(applied=[str(m) for m in applied], version=self.current_version()),
        )
        return applied

    def _backup_before(self, pending: list[Migration]) -> None:
        """Snapshot the database before touching it — non-negotiable (docs/05 §6 rule 3)."""
        if self._backup_dir is None or self._database.is_memory:
            return
        if self.current_version() == 0:
            return  # nothing to lose yet

        stamp = self._clock.now().strftime("%Y%m%dT%H%M%S")
        destination = self._backup_dir / f"pre-{pending[0].version:04d}-{stamp}.db"
        try:
            self._database.backup_to(destination)
        except Exception as exc:  # pragma: no cover - filesystem dependent
            raise MigrationError(
                f"refusing to migrate: pre-migration backup to {destination} failed ({exc})"
            ) from exc
