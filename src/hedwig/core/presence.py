"""Who has HEDWIG open right now (ADR-0026).

A registry of live listeners — today, WebSocket connections that subscribed — each with the
conversation it is showing. What lets HEDWIG know someone is there before it speaks first, and
where to put what it says.

Ephemeral by design, like `core.live`: not the bus, not persisted. A message for nobody is not
queued; it is already in the conversation, and the app shows it on its next load.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from hedwig.core.ids import new_id
from hedwig.core.logging import get_logger
from hedwig.core.ports import Clock

logger = get_logger(__name__)

Deliver = Callable[[str, Mapping[str, Any]], Awaitable[None]]


@dataclass(slots=True)
class Listener:
    id: str
    deliver: Deliver
    session_id: str | None
    seen_at: datetime


class Presence:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._listeners: dict[str, Listener] = {}

    def join(self, deliver: Deliver, *, session_id: str | None = None) -> str:
        listener = Listener(new_id("lsn"), deliver, session_id, self._clock.now())
        self._listeners[listener.id] = listener
        return listener.id

    def showing(self, listener_id: str, session_id: str | None) -> None:
        """The listener moved to another conversation (or to none: "New conversation")."""
        listener = self._listeners.get(listener_id)
        if listener is not None:
            listener.session_id = session_id
            listener.seen_at = self._clock.now()

    def leave(self, listener_id: str) -> None:
        self._listeners.pop(listener_id, None)

    @property
    def present(self) -> bool:
        return bool(self._listeners)

    def current_session(self) -> str | None:
        """The conversation the most recently active listener is showing."""
        latest = max(self._listeners.values(), key=lambda item: item.seen_at, default=None)
        return latest.session_id if latest else None

    async def broadcast(self, kind: str, payload: Mapping[str, Any]) -> int:
        """Deliver to every listener; one that fails is dropped. Returns how many received it."""
        listeners = list(self._listeners.values())
        results = await asyncio.gather(
            *(item.deliver(kind, payload) for item in listeners), return_exceptions=True
        )
        delivered = 0
        for item, result in zip(listeners, results, strict=True):
            if isinstance(result, BaseException):
                self.leave(item.id)
            else:
                delivered += 1
        return delivered
