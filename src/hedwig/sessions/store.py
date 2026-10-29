"""The conversation store (docs/05 §5.1, docs/26 §4).

Owner of `session`, `message`, `turn` and `working_set_log`. docs/08 §3 makes the owner the
only module permitted to write these tables; this is that module.

Three properties are worth the code they cost:

* **`seq` is allocated inside the transaction that inserts the message.** The window reads
  by descending `seq`, and a gap or a duplicate there is a conversation that reads out of
  order. The `UNIQUE (session_id, seq)` constraint makes a mistake here a failure rather
  than a subtly wrong transcript.
* **`record_turn` is an upsert.** Events are at-least-once and turns can be replayed from a
  checkpoint; recording the same turn twice must be a no-op, not a duplicate row.
* **The recent-turn window lives here**, not in `memory`. It reads a table `memory` does not
  own, and docs/06 §2's claim is unaffected: short-term memory is still a query, not a
  store. The query simply lives with its table now (docs/26 §4).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from hedwig.core.logging import fields, get_logger
from hedwig.core.ports import Clock, EventBus, Health, HealthStatus
from hedwig.core.ports.scheduler import TaskContext, TaskResult
from hedwig.core.ports.sessions import (
    MessageRole,
    TurnRecord,
    WindowMessage,
    WorkingSetRecord,
)
from hedwig.core.store import Database
from hedwig.core.text import estimate_tokens
from hedwig.core.types import TrustTier

logger = get_logger(__name__)

DEFAULT_WINDOW = 8
DEFAULT_IDLE = timedelta(minutes=30)


class SqliteSessionStore:
    """The `Conversation` implementation."""

    def __init__(
        self,
        database: Database,
        *,
        clock: Clock,
        bus: EventBus | None = None,
        window_size: int = DEFAULT_WINDOW,
        idle_for: timedelta = DEFAULT_IDLE,
    ) -> None:
        self._db = database
        self._clock = clock
        self._bus = bus
        self._window_size = max(1, window_size)
        self._idle_for = idle_for

    @property
    def window_size(self) -> int:
        return self._window_size

    # -- sessions ----------------------------------------------------------

    async def open(self, session_id: str, *, channel: str = "api") -> bool:
        """Open or resume a session. Returns whether a conversation *started*.

        True for a new session, and also for one that had ended and is being resumed: a
        session id reused after an idle timeout is a new conversation, and treating it as a
        continuation would let a long-lived id update the relationship once and never again
        (docs/27 §5.1).

        `INSERT ... ON CONFLICT DO NOTHING` rather than a read-then-write: two turns starting
        at once on the same session must not race into two rows or an error.
        """
        now = self._now()
        changed = self._db.execute(
            "INSERT INTO session (id, channel, started_at, created_at, updated_at) "
            "VALUES (?,?,?,?,?) ON CONFLICT (id) DO NOTHING",
            (session_id, channel, now, now, now),
        )
        if changed > 0:
            logger.info("session opened", extra=fields(session=session_id, channel=channel))
            return True

        resumed = self._db.execute(
            "UPDATE session SET ended_at = NULL, end_reason = NULL, started_at = ?, "
            "updated_at = ?, version = version + 1 WHERE id = ? AND ended_at IS NOT NULL",
            (now, now, session_id),
        )
        if resumed > 0:
            logger.info("session resumed", extra=fields(session=session_id))
        return resumed > 0

    async def end_idle(self, *, idle_for: timedelta) -> list[str]:
        """End every open session with no activity for `idle_for`, and announce each.

        The relationship model's update is applied at session end (docs/06 §3.3); until this
        existed, sessions never ended and the relationship could never change.
        """
        now = self._clock.now()
        cutoff = (now - idle_for).isoformat(timespec="milliseconds")
        rows = self._db.query(
            "SELECT id, turn_count FROM session WHERE ended_at IS NULL AND updated_at < ?",
            (cutoff,),
        )
        ended: list[str] = []
        for row in rows:
            changed = self._db.execute(
                "UPDATE session SET ended_at = ?, end_reason = 'timeout', updated_at = ?, "
                "version = version + 1 WHERE id = ? AND ended_at IS NULL",
                (self._now(), self._now(), row["id"]),
            )
            if not changed:
                continue  # someone else ended or resumed it in between
            session_id = str(row["id"])
            ended.append(session_id)
            await self._announce_end(session_id, reason="timeout", turns=int(row["turn_count"]))
        if ended:
            logger.info("idle sessions ended", extra=fields(count=len(ended)))
        return ended

    async def end_idle_task(self, context: TaskContext) -> TaskResult:
        """The scheduler's entry point."""
        ended = await self.end_idle(idle_for=self._idle_for)
        return TaskResult(stats={"ended": len(ended)})

    async def _announce_end(self, session_id: str, *, reason: str, turns: int) -> None:
        if self._bus is None:
            return
        try:
            await self._bus.emit(
                "conversation.session.ended",
                {"session_id": session_id, "reason": reason, "turn_count": turns},
                source="sessions",
                correlation_id=session_id,
            )
        except Exception as error:  # ending a session must not fail on a full queue
            logger.warning("could not announce session end", extra=fields(error=str(error)))

    # -- messages ----------------------------------------------------------

    async def record_message(
        self,
        *,
        session_id: str,
        message_id: str,
        role: MessageRole,
        text: str,
        trust: TrustTier = TrustTier.USER,
        meta: Mapping[str, Any] | None = None,
        emotion_ref: str | None = None,
    ) -> int:
        """Append a message and return its sequence number.

        The sequence is allocated and used in one transaction, so concurrent appends
        cannot both claim the same position.
        """
        now = self._now()
        with self._db.transaction() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(seq), 0) AS seq FROM message WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            seq = int(row["seq"]) + 1
            connection.execute(
                "INSERT INTO message (id, session_id, seq, role, text, trust_tier, "
                "token_count, meta, emotion_ref, created_at) VALUES (?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT (id) DO NOTHING",
                (
                    message_id,
                    session_id,
                    seq,
                    role.value,
                    text,
                    trust.value,
                    estimate_tokens(text),
                    json.dumps(dict(meta)) if meta else None,
                    emotion_ref,
                    now,
                ),
            )
            connection.execute(
                "UPDATE session SET updated_at = ?, version = version + 1 WHERE id = ?",
                (now, session_id),
            )
        return seq

    async def window(self, session_id: str, *, limit: int | None = None) -> Sequence[WindowMessage]:
        """The last N messages, oldest first (docs/06 §5.1).

        Oldest first because that is the order a reader — or a model — needs them in; the
        `DESC` is only how they are cheaply fetched.
        """
        rows = self._db.query(
            "SELECT role, text, created_at FROM message WHERE session_id = ? "
            "ORDER BY seq DESC LIMIT ?",
            (session_id, limit or self._window_size),
        )
        return tuple(
            reversed(
                [
                    WindowMessage(
                        role=str(row["role"]),
                        text=str(row["text"]),
                        at=str(row["created_at"]),
                    )
                    for row in rows
                ]
            )
        )

    def render_window(self, session_id: str, *, limit: int | None = None) -> str:
        """The window as text. Verbatim: nothing is summarised, nothing is scored."""
        rows = self._db.query(
            "SELECT role, text FROM message WHERE session_id = ? ORDER BY seq DESC LIMIT ?",
            (session_id, limit or self._window_size),
        )
        return "\n".join(f"{row['role']}: {row['text']}" for row in reversed(rows))

    # -- turns -------------------------------------------------------------

    async def record_turn(self, record: TurnRecord) -> None:
        """Write the turn record. Idempotent by turn id.

        Timestamps default from this store's clock, so a caller never has to read one
        (docs/03 §5.6): `started_at` is derived backwards from the measured latency.
        """
        now = self._now()
        completed_at = _iso(record.completed_at) or now
        started_at = _iso(record.started_at) or _shift(
            self._clock.now(), -(record.latency_ms or 0) / 1000.0
        )
        with self._db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO turn (
                    id, session_id, user_message_id, reply_message_id, correlation_id,
                    started_at, completed_at, status, latency_ms, working_set_id, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT (id) DO UPDATE SET
                    reply_message_id = excluded.reply_message_id,
                    completed_at     = excluded.completed_at,
                    status           = excluded.status,
                    latency_ms       = excluded.latency_ms,
                    working_set_id   = excluded.working_set_id
                """,
                (
                    record.id,
                    record.session_id,
                    record.user_message_id,
                    record.reply_message_id,
                    record.correlation_id,
                    started_at,
                    completed_at,
                    record.status,
                    record.latency_ms,
                    record.working_set_id,
                    now,
                ),
            )
            # Recounted rather than incremented: the upsert above means this can run twice
            # for one turn, and a counter that drifts is worse than a cheap COUNT.
            connection.execute(
                "UPDATE session SET turn_count = (SELECT COUNT(*) FROM turn WHERE "
                "session_id = ?), updated_at = ? WHERE id = ?",
                (record.session_id, now, record.session_id),
            )

    async def record_working_set(self, record: WorkingSetRecord) -> None:
        """Write what retrieval assembled, for explainability (docs/16 §6)."""
        self._db.execute(
            "INSERT INTO working_set_log (id, turn_id, query, policy, items, token_count, "
            "dropped_count, created_at) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT (id) DO NOTHING",
            (
                record.id,
                record.turn_id,
                json.dumps(list(record.queries)),
                json.dumps(dict(record.policy)),
                json.dumps([dict(item) for item in record.items]),
                record.token_count,
                record.dropped,
                self._now(),
            ),
        )

    # -- reads for diagnostics --------------------------------------------

    def counts(self) -> dict[str, int]:
        def one(sql: str) -> int:
            row = self._db.query_one(sql)
            return int(row["n"]) if row else 0

        return {
            "sessions": one("SELECT COUNT(*) AS n FROM session"),
            "open_sessions": one("SELECT COUNT(*) AS n FROM session WHERE ended_at IS NULL"),
            "messages": one("SELECT COUNT(*) AS n FROM message"),
            "turns": one("SELECT COUNT(*) AS n FROM turn"),
            "unfinished_turns": one("SELECT COUNT(*) AS n FROM turn WHERE status = 'running'"),
        }

    async def health(self) -> Health:
        counts = self.counts()
        status = HealthStatus.OK
        message = ""
        if counts["unfinished_turns"] > 0:
            # A turn stuck in `running` means a process died mid-turn. Visible rather than
            # quietly accumulating (docs/07 §8).
            status = HealthStatus.DEGRADED
            message = f"{counts['unfinished_turns']} turn(s) never finished"
        return Health(status=status, message=message, detail=dict(counts))

    # -- helpers -----------------------------------------------------------

    def _now(self) -> str:
        return self._clock.now().isoformat(timespec="milliseconds")


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="milliseconds") if value else None


def _shift(value: datetime, seconds: float) -> str:
    return (value + timedelta(seconds=seconds)).isoformat(timespec="milliseconds")
