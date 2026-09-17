"""The conversation ports (docs/05 §5.1, docs/26 §4).

`session`, `message` and `turn` are owned by the `sessions` module. Until Milestone 6
nothing implemented that owner: the tables existed with one reader and no writer, so every
conversation was the first conversation.

`Conversation` is deliberately five methods wide — what a graph node needs, not a general
conversation API (docs/03 §5). The store implements it directly rather than through an
adapter, because it was written after the port rather than before it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from hedwig.core.types import TrustTier


class MessageRole(StrEnum):
    """Who said it. Mirrors the `message.role` check constraint."""

    USER = "user"
    HEDWIG = "hedwig"
    SYSTEM = "system"
    TOOL = "tool"


@dataclass(frozen=True, slots=True)
class WindowMessage:
    """One message in the recent-turn window (docs/06 §5.1).

    Verbatim: no scoring, no decay, no summarising. It either is recent or it is not.

    `at` is an ISO-8601 string rather than a `datetime` because this travels inside the
    checkpointed turn state, which docs/07 §4 rule 3 requires to stay JSON-serialisable.
    """

    role: str
    text: str
    at: str


@dataclass(frozen=True, slots=True)
class TurnRecord:
    """What happened in one turn, for the `turn` table.

    Written once, at the end. It is a timing and status record, not an authoritative one —
    the message log is authoritative (ADR-0018).
    """

    id: str
    session_id: str
    correlation_id: str
    status: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    """Both default to None so a caller does not have to read a clock. The store has one,
    and nothing else should (docs/03 §5.6) — which is what keeps a year-long simulated test
    possible."""
    user_message_id: str | None = None
    reply_message_id: str | None = None
    latency_ms: int | None = None
    working_set_id: str | None = None


@dataclass(frozen=True, slots=True)
class WorkingSetRecord:
    """What retrieval assembled for a turn, kept so it can be explained (docs/16 §6).

    Explanatory, never authoritative: pruned after 90 days (docs/05 §7). A ranking nobody
    can explain is a ranking nobody can debug.
    """

    id: str
    turn_id: str
    queries: tuple[str, ...] = ()
    policy: Mapping[str, Any] = field(default_factory=dict)
    items: tuple[Mapping[str, Any], ...] = ()
    token_count: int = 0
    dropped: int = 0


@runtime_checkable
class Conversation(Protocol):
    """The conversation log, as a turn needs it."""

    async def open(self, session_id: str, *, channel: str = "api") -> bool:
        """Open or resume a session. Returns whether it was newly created.

        An upsert rather than a create: a session that has gone missing mid-turn is
        recreated instead of failing the turn (docs/26 §9).
        """
        ...

    async def record_message(
        self,
        *,
        session_id: str,
        message_id: str,
        role: MessageRole,
        text: str,
        trust: TrustTier = TrustTier.USER,
        meta: Mapping[str, Any] | None = None,
    ) -> int:
        """Append a message and return its sequence number within the session."""
        ...

    async def window(self, session_id: str, *, limit: int | None = None) -> Sequence[WindowMessage]:
        """The last N messages, oldest first (docs/06 §5.1)."""
        ...

    async def record_turn(self, record: TurnRecord) -> None:
        """Write the turn record. Idempotent: the same turn id overwrites."""
        ...

    async def record_working_set(self, record: WorkingSetRecord) -> None:
        """Write what was retrieved for a turn.

        Written before the turn row, which then carries its id. Explanatory only: a failure
        here must not stop the turn from being recorded.
        """
        ...
