"""The goal store and its lifecycle (docs/08 §6, §6.1). Owner of `goal`.

Three lifecycle rules keep the list from becoming a graveyard, applied by a daily task:
proposed goals untouched for 7 days are abandoned; active goals without progress for 30 days
are blocked; at most 12 are active, by priority.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from hedwig.core.errors import InvalidRequestError, NotFoundError
from hedwig.core.ids import new_id
from hedwig.core.logging import fields, get_logger
from hedwig.core.ports import Clock, EventBus, Health, HealthStatus
from hedwig.core.ports.goals import Goal, GoalKind, GoalStatus
from hedwig.core.ports.scheduler import TaskContext, TaskResult
from hedwig.core.store import Database

logger = get_logger(__name__)

PROPOSED_EXPIRY = timedelta(days=7)
STALE_ACTIVE = timedelta(days=30)
MAX_ACTIVE = 12

TRANSITIONS: dict[GoalStatus, frozenset[GoalStatus]] = {
    GoalStatus.PROPOSED: frozenset({GoalStatus.ACTIVE, GoalStatus.ABANDONED}),
    GoalStatus.ACTIVE: frozenset({GoalStatus.BLOCKED, GoalStatus.DONE, GoalStatus.ABANDONED}),
    GoalStatus.BLOCKED: frozenset({GoalStatus.ACTIVE, GoalStatus.DONE, GoalStatus.ABANDONED}),
    GoalStatus.DONE: frozenset({GoalStatus.ACTIVE}),
    GoalStatus.ABANDONED: frozenset({GoalStatus.PROPOSED, GoalStatus.ACTIVE}),
}
"""A closed goal can be reopened; nothing else jumps the lifecycle."""


class GoalStore:
    """Create, read, update and sweep goals. Implements `GoalReader`."""

    def __init__(self, database: Database, *, clock: Clock, bus: EventBus | None = None) -> None:
        self._db = database
        self._clock = clock
        self._bus = bus

    # -- reading --------------------------------------------------------------------

    async def active(self, *, limit: int = MAX_ACTIVE) -> Sequence[Goal]:
        return await self.list(statuses=(GoalStatus.ACTIVE,), limit=limit)

    async def list(
        self, *, statuses: Sequence[GoalStatus] | None = None, limit: int = 100
    ) -> Sequence[Goal]:
        wanted = [status.value for status in (statuses or tuple(GoalStatus))]
        marks = ",".join("?" for _ in wanted)
        rows = self._db.query(
            f"SELECT * FROM goal WHERE status IN ({marks}) "
            "ORDER BY priority DESC, created_at DESC LIMIT ?",
            (*wanted, limit),
        )
        return tuple(_goal(row) for row in rows)

    async def get(self, goal_id: str) -> Goal | None:
        row = self._db.query_one("SELECT * FROM goal WHERE id = ?", (goal_id,))
        return _goal(row) if row else None

    # -- writing --------------------------------------------------------------------

    async def create(
        self,
        description: str,
        *,
        kind: GoalKind = GoalKind.USER_STATED,
        status: GoalStatus = GoalStatus.ACTIVE,
        priority: float = 0.5,
        context: str = "",
        completion: str = "",
        origin: str = "user",
        origin_memory_id: str | None = None,
    ) -> Goal:
        text = description.strip()
        if not text:
            raise InvalidRequestError("A goal needs a description.")
        now = self._now()
        goal_id = new_id("goal")
        self._db.execute(
            "INSERT INTO goal (id, kind, description, context, completion, status, priority, "
            "origin, origin_memory_id, last_touched_at, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                goal_id,
                kind.value,
                text,
                context.strip(),
                completion.strip(),
                status.value,
                _unit(priority),
                origin,
                origin_memory_id,
                now,
                now,
                now,
            ),
        )
        goal = await self._require(goal_id)
        await self._announce("goals.goal.created", goal)
        if status is GoalStatus.ACTIVE:
            await self._enforce_cap()
        return goal

    async def update(
        self,
        goal_id: str,
        *,
        status: GoalStatus | None = None,
        progress: float | None = None,
        priority: float | None = None,
        description: str | None = None,
        context: str | None = None,
        completion: str | None = None,
    ) -> Goal:
        goal = await self._require(goal_id)
        if (
            status is not None
            and status is not goal.status
            and status not in TRANSITIONS[goal.status]
        ):
            raise InvalidRequestError(
                f"A {goal.status.value} goal cannot become {status.value}.", goal_id=goal_id
            )
        now = self._now()
        changes: dict[str, Any] = {"last_touched_at": now, "updated_at": now}
        if status is not None:
            changes["status"] = status.value
        if progress is not None and _unit(progress) != goal.progress:
            changes["progress"] = _unit(progress)
            changes["last_progress_at"] = now
        if priority is not None:
            changes["priority"] = _unit(priority)
        for column, value in (
            ("description", description),
            ("context", context),
            ("completion", completion),
        ):
            if value is not None:
                changes[column] = value.strip()
        assignments = ", ".join(f"{column} = ?" for column in changes)
        self._db.execute(
            f"UPDATE goal SET {assignments}, version = version + 1 WHERE id = ?",
            (*changes.values(), goal_id),
        )
        updated = await self._require(goal_id)
        await self._announce("goals.goal.updated", updated)
        if status is GoalStatus.ACTIVE or priority is not None:
            await self._enforce_cap()
        return updated

    # -- lifecycle ------------------------------------------------------------------

    async def sweep(self) -> dict[str, int]:
        """The three rules of docs/08 §6. Idempotent: a second sweep changes nothing."""
        now = self._clock.now()
        expired = self._db.execute(
            "UPDATE goal SET status = 'abandoned', updated_at = ?, version = version + 1 "
            "WHERE status = 'proposed' AND last_touched_at < ?",
            (_iso(now), _iso(now - PROPOSED_EXPIRY)),
        )
        blocked = self._db.execute(
            "UPDATE goal SET status = 'blocked', updated_at = ?, version = version + 1 "
            "WHERE status = 'active' AND COALESCE(last_progress_at, created_at) < ?",
            (_iso(now), _iso(now - STALE_ACTIVE)),
        )
        capped = await self._enforce_cap()
        stats = {"expired": expired, "blocked": blocked, "capped": capped}
        if any(stats.values()):
            logger.info("goals swept", extra=fields(**stats))
        return stats

    async def sweep_task(self, context: TaskContext) -> TaskResult:
        return TaskResult(stats=await self.sweep())

    async def _enforce_cap(self) -> int:
        rows = self._db.query(
            "SELECT id FROM goal WHERE status = 'active' "
            "ORDER BY priority DESC, created_at DESC LIMIT -1 OFFSET ?",
            (MAX_ACTIVE,),
        )
        for row in rows:
            self._db.execute(
                "UPDATE goal SET status = 'blocked', updated_at = ?, version = version + 1 "
                "WHERE id = ?",
                (self._now(), row["id"]),
            )
        return len(rows)

    async def health(self) -> Health:
        row = self._db.query_one("SELECT COUNT(*) AS n FROM goal WHERE status = 'active'")
        return Health(status=HealthStatus.OK, detail={"active": int(row["n"]) if row else 0})

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    # -- helpers --------------------------------------------------------------------

    async def _require(self, goal_id: str) -> Goal:
        goal = await self.get(goal_id)
        if goal is None:
            raise NotFoundError("No such goal.", goal_id=goal_id)
        return goal

    async def _announce(self, event_type: str, goal: Goal) -> None:
        if self._bus is None:
            return
        await self._bus.emit(
            event_type,
            {
                "goal_id": goal.id,
                "status": goal.status.value,
                "progress": goal.progress,
                "origin": goal.origin,
            },
            source="goals",
        )

    def _now(self) -> str:
        return _iso(self._clock.now())


def _unit(value: float) -> float:
    return round(max(0.0, min(1.0, float(value))), 6)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds")


def _dt(value: Any) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _goal(row: Any) -> Goal:
    created = _dt(row["created_at"])
    touched = _dt(row["last_touched_at"])
    assert created is not None and touched is not None
    return Goal(
        id=str(row["id"]),
        description=str(row["description"]),
        kind=GoalKind(row["kind"]),
        status=GoalStatus(row["status"]),
        priority=float(row["priority"]),
        progress=float(row["progress"]),
        context=str(row["context"]),
        completion=str(row["completion"]),
        origin=str(row["origin"]),
        created_at=created,
        last_touched_at=touched,
        last_progress_at=_dt(row["last_progress_at"]),
        due_at=_dt(row["due_at"]),
    )
