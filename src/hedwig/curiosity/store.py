"""The gap register and the findings (docs/11 §13). Owner of `gap` and `finding`."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from hedwig.core.errors import InvalidRequestError, NotFoundError
from hedwig.core.ids import new_id
from hedwig.core.logging import fields, get_logger
from hedwig.core.ports import Clock, EventBus
from hedwig.core.store import Database
from hedwig.knowledge.cues import is_personal
from hedwig.knowledge.injection import scan
from hedwig.memory.capture import similarity

logger = get_logger(__name__)

SAME_GAP = 0.7
"""Word overlap above which a new gap is an old one again: bump it, do not duplicate."""
GAP_EXPIRY = timedelta(days=30)
MAX_ATTEMPTS = 3

ORIGIN_PRIORITY = {"unanswered_question": 0.8, "user": 0.7, "user_interest": 0.5}
"""docs/11 §3's seeds. `user` — asked for explicitly — sits just under an unanswered question."""


@dataclass(frozen=True, slots=True)
class Gap:
    id: str
    question: str
    origin: str
    priority: float
    status: str
    attempt_count: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class Finding:
    id: str
    gap_id: str | None
    question: str
    summary: str
    sources: tuple[dict[str, str], ...]
    status: str
    novelty: float
    created_at: datetime
    surfaced_at: datetime | None


class CuriosityStore:
    def __init__(self, database: Database, *, clock: Clock, bus: EventBus | None = None) -> None:
        self._db = database
        self._clock = clock
        self._bus = bus

    # -- gaps ---------------------------------------------------------------------

    async def record_gap(
        self, question: str, *, origin: str, origin_ref: str | None = None
    ) -> Gap | None:
        """Register a question HEDWIG cannot answer yet. None when it must not be one.

        Never about the user: gap text may one day be sent to a search engine (docs/11 §6).
        """
        text = " ".join(question.split())[:300]
        if len(text) < 8 or is_personal(text) or scan(text).quarantined:
            return None
        if origin not in ORIGIN_PRIORITY:
            raise InvalidRequestError(f"unknown gap origin {origin!r}")

        now = self._now()
        for gap in await self.gaps(statuses=("open", "exploring", "blocked")):
            if similarity(text, gap.question) >= SAME_GAP:
                self._db.execute(
                    "UPDATE gap SET priority = MIN(1.0, priority + 0.1), updated_at = ?, "
                    "version = version + 1 WHERE id = ?",
                    (now, gap.id),
                )
                return await self.gap(gap.id)

        gap_id = new_id("gap")
        self._db.execute(
            "INSERT INTO gap (id, question, origin, origin_ref, priority, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (gap_id, text, origin, origin_ref, ORIGIN_PRIORITY[origin], now, now),
        )
        if self._bus is not None:
            await self._bus.emit(
                "curiosity.gap.recorded",
                {"gap_id": gap_id, "origin": origin},
                source="curiosity",
            )
        logger.info("gap recorded", extra=fields(gap=gap_id, origin=origin))
        return await self.gap(gap_id)

    async def gaps(self, *, statuses: Sequence[str] = ("open",), limit: int = 100) -> Sequence[Gap]:
        marks = ",".join("?" for _ in statuses)
        rows = self._db.query(
            f"SELECT * FROM gap WHERE status IN ({marks}) ORDER BY priority DESC, created_at LIMIT ?",
            (*statuses, limit),
        )
        return tuple(_gap(row) for row in rows)

    async def gap(self, gap_id: str) -> Gap | None:
        row = self._db.query_one("SELECT * FROM gap WHERE id = ?", (gap_id,))
        return _gap(row) if row else None

    async def set_gap(self, gap_id: str, *, status: str, attempted: bool = False) -> None:
        self._db.execute(
            "UPDATE gap SET status = ?, attempt_count = attempt_count + ?, updated_at = ?, "
            "version = version + 1 WHERE id = ?",
            (status, int(attempted), self._now(), gap_id),
        )

    async def expire(self) -> int:
        """30 days unanswered, or 3 failed attempts, and a gap is let go (docs/11 §3)."""
        cutoff = (self._clock.now() - GAP_EXPIRY).isoformat(timespec="milliseconds")
        return self._db.execute(
            "UPDATE gap SET status = 'abandoned', updated_at = ?, version = version + 1 "
            "WHERE status IN ('open','blocked') AND (created_at < ? OR attempt_count >= ?)",
            (self._now(), cutoff, MAX_ATTEMPTS),
        )

    # -- findings -----------------------------------------------------------------

    async def add_finding(
        self,
        *,
        gap_id: str | None,
        summary: str,
        sources: Sequence[dict[str, str]],
        status: str,
        novelty: float,
        relevance: float,
    ) -> Finding:
        finding_id = new_id("fnd")
        now = self._now()
        self._db.execute(
            "INSERT INTO finding (id, gap_id, summary, sources, novelty_score, relevance_score, "
            "status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                finding_id,
                gap_id,
                summary,
                json.dumps(list(sources)),
                novelty,
                relevance,
                status,
                now,
                now,
            ),
        )
        finding = await self.finding(finding_id)
        assert finding is not None
        return finding

    async def findings(
        self, *, statuses: Sequence[str] | None = None, limit: int = 50
    ) -> Sequence[Finding]:
        if statuses:
            marks = ",".join("?" for _ in statuses)
            rows = self._db.query(
                f"{_FINDING_SELECT} WHERE f.status IN ({marks}) ORDER BY f.created_at DESC LIMIT ?",
                (*statuses, limit),
            )
        else:
            rows = self._db.query(f"{_FINDING_SELECT} ORDER BY f.created_at DESC LIMIT ?", (limit,))
        return tuple(_finding(row) for row in rows)

    async def finding(self, finding_id: str) -> Finding | None:
        row = self._db.query_one(f"{_FINDING_SELECT} WHERE f.id = ?", (finding_id,))
        return _finding(row) if row else None

    async def set_finding(
        self, finding_id: str, *, status: str, belief_id: str | None = None
    ) -> Finding:
        if await self.finding(finding_id) is None:
            raise NotFoundError("No such finding.", finding_id=finding_id)
        now = self._now()
        self._db.execute(
            "UPDATE finding SET status = ?, updated_at = ?, "
            "surfaced_at = CASE WHEN ? = 'surfaced' THEN ? ELSE surfaced_at END, "
            "promoted_belief_id = COALESCE(?, promoted_belief_id) WHERE id = ?",
            (status, now, status, now, belief_id, finding_id),
        )
        finding = await self.finding(finding_id)
        assert finding is not None
        return finding

    def surfaced_since(self, since: datetime) -> list[datetime]:
        rows = self._db.query(
            "SELECT surfaced_at FROM finding WHERE surfaced_at IS NOT NULL AND surfaced_at >= ?",
            (since.isoformat(timespec="milliseconds"),),
        )
        return [datetime.fromisoformat(row["surfaced_at"]) for row in rows]

    def explorations_since(self, since: datetime) -> int:
        row = self._db.query_one(
            "SELECT COUNT(*) AS n FROM finding WHERE created_at >= ?",
            (since.isoformat(timespec="milliseconds"),),
        )
        attempted = self._db.query_one(
            "SELECT COUNT(*) AS n FROM gap WHERE attempt_count > 0 AND updated_at >= ? "
            "AND status IN ('open','abandoned')",
            (since.isoformat(timespec="milliseconds"),),
        )
        return (int(row["n"]) if row else 0) + (int(attempted["n"]) if attempted else 0)

    def _now(self) -> str:
        return self._clock.now().isoformat(timespec="milliseconds")


_FINDING_SELECT = (
    "SELECT f.*, COALESCE(g.question, '') AS question FROM finding f "
    "LEFT JOIN gap g ON g.id = f.gap_id"
)


def _dt(value: Any) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _gap(row: Any) -> Gap:
    created, updated = _dt(row["created_at"]), _dt(row["updated_at"])
    assert created and updated
    return Gap(
        id=str(row["id"]),
        question=str(row["question"]),
        origin=str(row["origin"]),
        priority=float(row["priority"]),
        status=str(row["status"]),
        attempt_count=int(row["attempt_count"]),
        created_at=created,
        updated_at=updated,
    )


def _finding(row: Any) -> Finding:
    created = _dt(row["created_at"])
    assert created
    return Finding(
        id=str(row["id"]),
        gap_id=row["gap_id"],
        question=str(row["question"]),
        summary=str(row["summary"]),
        sources=tuple(json.loads(row["sources"] or "[]")),
        status=str(row["status"]),
        novelty=float(row["novelty_score"]),
        created_at=created,
        surfaced_at=_dt(row["surfaced_at"]),
    )
