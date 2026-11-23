"""The goals port (docs/08 §6).

`GoalReader` is what the rest of HEDWIG may know about goals: the active ones. Writes go
through the owning module's own API (docs/08 §3).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable


class GoalKind(StrEnum):
    USER_STATED = "user_stated"
    SELF_GENERATED = "self_generated"
    MAINTENANCE = "maintenance"


class GoalStatus(StrEnum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    BLOCKED = "blocked"
    DONE = "done"
    ABANDONED = "abandoned"

    @property
    def is_open(self) -> bool:
        return self in {GoalStatus.PROPOSED, GoalStatus.ACTIVE, GoalStatus.BLOCKED}


@dataclass(frozen=True, slots=True)
class Goal:
    id: str
    description: str
    kind: GoalKind
    status: GoalStatus
    priority: float
    progress: float
    context: str
    completion: str
    origin: str
    created_at: datetime
    last_touched_at: datetime
    last_progress_at: datetime | None = None
    due_at: datetime | None = None


class NoteKind(StrEnum):
    """A project journal entry (ADR-0025)."""

    DECISION = "decision"
    PROGRESS = "progress"
    QUESTION = "question"
    BLOCKER = "blocker"
    DONE = "done"


@dataclass(frozen=True, slots=True)
class GoalNote:
    id: str
    goal_id: str
    kind: NoteKind
    text: str
    source: str
    """`system` (the store's own changes), `reflection`, or `user`."""
    created_at: datetime
    session_id: str | None = None


@runtime_checkable
class GoalReader(Protocol):
    async def active(self, *, limit: int = 12) -> Sequence[Goal]:
        """Active goals, highest priority first."""
        ...

    async def notes(self, goal_id: str, *, limit: int = 20) -> Sequence[GoalNote]:
        """A goal's journal, newest first (ADR-0025)."""
        ...

    async def mentioned_in(self, text: str, *, limit: int = 2) -> Sequence[Goal]:
        """Finished, dropped or blocked goals the text names — "how did the compiler go?"."""
        ...
