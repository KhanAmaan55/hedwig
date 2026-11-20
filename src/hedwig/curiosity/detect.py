"""Gap detection, offline (docs/11 §3, §13).

Two subscribers' worth of rules, no model and no network: a question HEDWIG admitted it could
not answer, and the general interests reflection noticed. Works with curiosity switched off —
the register is worth having on its own.
"""

from __future__ import annotations

from hedwig.core.logging import get_logger
from hedwig.core.ports import Event, EventBus
from hedwig.curiosity.store import CuriosityStore
from hedwig.knowledge.cues import admits_not_knowing, is_question

logger = get_logger(__name__)

SUBSCRIPTION = "curiosity-gaps"


class GapDetector:
    def __init__(self, store: CuriosityStore, *, bus: EventBus | None = None) -> None:
        self._store = store
        self._bus = bus
        self._subscribed = False

    async def start(self) -> None:
        if self._bus is not None and not self._subscribed:
            # One subscription, two kinds of evidence (docs/04 §4: one ordered stream).
            self._bus.subscribe("*.*.completed", self.handle, name=SUBSCRIPTION)
            self._subscribed = True

    async def stop(self) -> None:
        return None

    async def handle(self, event: Event) -> None:
        if event.type == "conversation.turn.completed":
            question = str(event.payload.get("input", ""))
            reply = str(event.payload.get("reply", ""))
            if is_question(question) and admits_not_knowing(reply):
                await self._store.record_gap(
                    question, origin="unanswered_question", origin_ref=event.payload.get("turn_id")
                )
        elif event.type == "reflection.session.completed":
            for topic in event.payload.get("curious_about") or []:
                await self._store.record_gap(
                    str(topic), origin="user_interest", origin_ref=event.payload.get("session_id")
                )
