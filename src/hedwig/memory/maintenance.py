"""Decay and forgetting (docs/06 §7).

The nightly pass that keeps memory *organised* rather than merely large. Two operations,
in this order, and the order matters:

1. **Decay** — every active memory loses salience toward a floor set by its base importance.
2. **Forget** — memories that have fallen below the threshold, and satisfy four further
   conditions, are tombstoned.

Decay is applied in a batch rather than lazily at read time, which keeps reads cheap and
pure and makes the curve inspectable (docs/06 §7.1).

The forget pass is deliberately timid. It is capped at 2% of active memories per run, it
will not touch anything pinned, recent, recently-used, or serving as the sole justification
for a belief. A bug in the salience maths must not be able to cause mass amnesia overnight,
and a rate cap is the cheapest possible insurance against that class of catastrophe.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from hedwig.core.logging import fields, get_logger
from hedwig.core.ports import Clock, EventBus, Health, HealthStatus
from hedwig.core.ports.memory import ForgetReason
from hedwig.core.store import Database
from hedwig.core.types import MemoryId, MemoryKind
from hedwig.memory.salience import (
    DEFAULT_HALF_LIFE_DAYS,
    FORGET_THRESHOLD,
    decayed_salience,
    elapsed_days,
    forget_budget,
    is_forgettable,
)
from hedwig.memory.store import SqliteMemoryStore

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class MaintenanceReport:
    """What one pass did. Returned rather than logged only, so a job can record it."""

    decayed: int = 0
    forgotten: int = 0
    protected: int = 0
    """Eligible by salience but saved by a guard — pinned, or the sole source of a belief."""
    budget: int = 0
    considered: int = 0

    def as_stats(self) -> dict[str, int]:
        return {
            "decayed": self.decayed,
            "forgotten": self.forgotten,
            "protected": self.protected,
            "budget": self.budget,
            "considered": self.considered,
        }


class MemoryMaintenance:
    """The decay and forgetting pass."""

    def __init__(
        self,
        database: Database,
        store: SqliteMemoryStore,
        *,
        clock: Clock,
        bus: EventBus | None = None,
        half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
        forget_threshold: float = FORGET_THRESHOLD,
    ) -> None:
        self._db = database
        self._store = store
        self._clock = clock
        self._bus = bus
        self._half_life = half_life_days
        self._threshold = forget_threshold

    # -- the pass ----------------------------------------------------------

    async def run(self) -> MaintenanceReport:
        decayed = await self.decay_pass()
        forgotten, protected, budget, considered = await self.forget_pass()

        report = MaintenanceReport(
            decayed=decayed,
            forgotten=forgotten,
            protected=protected,
            budget=budget,
            considered=considered,
        )
        logger.info("memory maintenance complete", extra=fields(**report.as_stats()))
        return report

    async def decay_pass(self) -> int:
        """Apply exponential decay to every active memory.

        Guarded by `last_decay_at` rather than by "the job runs once a night, so it's
        fine": double-applied decay would silently halve the lifetime of every memory, and
        it is the one maintenance bug that corrupts data rather than merely wasting time.
        """
        now = self._clock.now()
        touched = 0

        for table in ("episode", "belief"):
            rows = self._db.query(
                f"SELECT id, salience, base_importance, access_count, last_decay_at, pinned "
                f"FROM {table} WHERE tombstoned_at IS NULL AND pinned = 0"
            )
            for row in rows:
                since = _dt(row["last_decay_at"])
                days = elapsed_days(since, now)
                if days <= 0:
                    continue

                updated = decayed_salience(
                    salience=float(row["salience"]),
                    base=float(row["base_importance"]),
                    access_count=int(row["access_count"]),
                    elapsed_days=days,
                    base_half_life=self._half_life,
                    pinned=bool(row["pinned"]),
                )
                if abs(updated - float(row["salience"])) < 1e-9:
                    # Already at the floor. Still stamp the clock so the next pass measures
                    # from now rather than accumulating an ever-larger elapsed window.
                    self._db.execute(
                        f"UPDATE {table} SET last_decay_at = ? WHERE id = ?",
                        (_iso(now), row["id"]),
                    )
                    continue

                self._db.execute(
                    f"UPDATE {table} SET salience = ?, last_decay_at = ?, "
                    "updated_at = ?, version = version + 1 WHERE id = ?",
                    (updated, _iso(now), _iso(now), row["id"]),
                )
                touched += 1

        return touched

    async def forget_pass(self) -> tuple[int, int, int, int]:
        """Tombstone what has faded, within a hard rate cap."""
        now = self._clock.now()
        active = self._active_count()
        budget = forget_budget(active)
        if budget == 0:
            return 0, 0, 0, 0

        forgotten = 0
        protected = 0
        considered = 0

        for table, kind in (("episode", MemoryKind.EPISODE), ("belief", MemoryKind.BELIEF)):
            time_column = "occurred_at" if table == "episode" else "valid_from"
            rows = self._db.query(
                f"SELECT id, salience, pinned, last_accessed_at, {time_column} AS at "
                f"FROM {table} WHERE tombstoned_at IS NULL AND salience < ? "
                "ORDER BY salience ASC LIMIT ?",
                (self._threshold, budget * 4),
            )

            for row in rows:
                if forgotten >= budget:
                    break
                considered += 1

                memory_id = MemoryId(str(row["id"]))
                created = _dt(row["at"])
                accessed = _dt(row["last_accessed_at"]) or created

                sole_source = await self._is_sole_source(memory_id)
                if not is_forgettable(
                    salience=float(row["salience"]),
                    pinned=bool(row["pinned"]),
                    age_days=elapsed_days(created, now),
                    idle_days=elapsed_days(accessed, now),
                    is_sole_source=sole_source,
                    threshold=self._threshold,
                ):
                    protected += 1
                    continue

                if await self._store.tombstone(memory_id, ForgetReason.DECAYED):
                    forgotten += 1
                    logger.debug(
                        "memory forgotten",
                        extra=fields(memory=memory_id, kind=kind.value, salience=row["salience"]),
                    )
                else:
                    protected += 1

        return forgotten, protected, budget, considered

    # -- guards ------------------------------------------------------------

    async def _is_sole_source(self, memory_id: MemoryId) -> bool:
        """Whether anything still standing depends on this memory alone.

        Forgetting the only episode that justifies an active belief would leave HEDWIG
        holding an opinion it cannot account for — which is exactly how humans acquire
        prejudices, and not a feature we want (docs/06 §7.3).
        """
        dependents = self._db.query(
            "SELECT derived_id FROM memory_derivation WHERE source_id = ?", (memory_id,)
        )
        for row in dependents:
            derived = str(row["derived_id"])
            other = self._db.query_one(
                "SELECT COUNT(*) AS n FROM memory_derivation "
                "WHERE derived_id = ? AND source_id != ?",
                (derived, memory_id),
            )
            if other is not None and int(other["n"]) == 0:
                return True
        return False

    def _active_count(self) -> int:
        row = self._db.query_one(
            "SELECT (SELECT COUNT(*) FROM episode WHERE tombstoned_at IS NULL) + "
            "(SELECT COUNT(*) FROM belief WHERE tombstoned_at IS NULL) AS n"
        )
        return int(row["n"]) if row else 0

    # -- health ------------------------------------------------------------

    async def health(self) -> Health:
        row = self._db.query_one(
            "SELECT COUNT(*) AS n FROM tombstone WHERE reason = 'decayed' AND created_at >= ?",
            (_iso(self._clock.now().replace(hour=0, minute=0, second=0, microsecond=0)),),
        )
        forgotten_today = int(row["n"]) if row else 0
        active = self._active_count()
        cap = forget_budget(active)

        status = HealthStatus.OK
        message = ""
        if cap and forgotten_today > cap * 3:
            # More than the cap should allow means something is wrong with the arithmetic
            # or the guards, and it is the one failure that loses user data.
            status = HealthStatus.DEGRADED
            message = f"{forgotten_today} memories forgotten today, above the expected cap"

        return Health(
            status=status,
            message=message,
            detail={
                "active_memories": active,
                "forget_budget_per_pass": cap,
                "forgotten_today": forgotten_today,
                "half_life_days": self._half_life,
                "forget_threshold": self._threshold,
            },
        )


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="milliseconds") if value else None


def _dt(value: object) -> datetime | None:
    return datetime.fromisoformat(str(value)) if value else None
