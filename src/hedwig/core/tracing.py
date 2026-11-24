"""Where the time went: spans (docs/19 §5, §13).

A span is one stage of a turn or one bus handler delivery — a name, a start, a duration, a
status — recorded under the correlation id the work ran with, so a trace is one query. Names,
timings and counts only; never content (docs/19 §4).

Written as each span finishes. A recorder that fails never fails the work it measures.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any, Protocol

from hedwig.core.context import current_correlation_id
from hedwig.core.ids import new_id
from hedwig.core.logging import fields, get_logger
from hedwig.core.ports import Clock
from hedwig.core.ports.scheduler import TaskContext, TaskResult
from hedwig.core.store import Database

logger = get_logger(__name__)

RETENTION = timedelta(days=30)


class SpanRecorder(Protocol):
    def now(self) -> datetime:
        """Wall-clock time from the injected clock, so spans line up with every other row."""
        ...

    def record(
        self,
        name: str,
        *,
        kind: str,
        started_at: datetime,
        duration_ms: float,
        ok: bool = True,
        detail: Mapping[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> None: ...


class SpanStore:
    """Owner of `span`."""

    def __init__(self, database: Database, *, clock: Clock) -> None:
        self._db = database
        self._clock = clock

    def now(self) -> datetime:
        return self._clock.now()

    def record(
        self,
        name: str,
        *,
        kind: str,
        started_at: datetime,
        duration_ms: float,
        ok: bool = True,
        detail: Mapping[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> None:
        correlation = correlation_id or current_correlation_id()
        if not correlation:
            return
        try:
            self._db.execute(
                "INSERT INTO span (id, correlation_id, name, kind, started_at, duration_ms, "
                "status, detail) VALUES (?,?,?,?,?,?,?,?)",
                (
                    new_id("spn"),
                    correlation,
                    name,
                    kind,
                    started_at.isoformat(timespec="milliseconds"),
                    round(duration_ms, 3),
                    "ok" if ok else "failed",
                    json.dumps(dict(detail)) if detail else None,
                ),
            )
        except Exception as error:  # measuring must never break what is measured
            logger.warning("span not recorded", extra=fields(name=name, error=str(error)))

    def prune(self) -> int:
        cutoff = (self._clock.now() - RETENTION).isoformat(timespec="milliseconds")
        return self._db.execute("DELETE FROM span WHERE started_at < ?", (cutoff,))

    async def prune_task(self, context: TaskContext) -> TaskResult:
        return TaskResult(stats={"pruned": self.prune()})
