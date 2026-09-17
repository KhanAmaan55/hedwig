"""Publishing what happened in a turn (docs/26 §7.1).

The `TurnAnnouncer` port with the event bus behind it. It exists so that `nodes.py` keeps
its one property worth defending: **a node calls ports, never infrastructure**. A node that
imported the bus would be a node that knows how HEDWIG communicates, and replacing the bus
would then mean editing the graph.

It is also the one place that knows the payload shape of each conversation event, so
docs/04 §5 has a single implementation to disagree with rather than four scattered ones.
"""

from __future__ import annotations

from dataclasses import dataclass

from hedwig.core.logging import fields, get_logger
from hedwig.core.ports import EventBus
from hedwig.core.ports.brain import TurnSummary

logger = get_logger(__name__)

SOURCE = "brain"


@dataclass(frozen=True, slots=True)
class BusAnnouncer:
    """Announces turn events on the event bus.

    Failures are logged, never raised. A turn that produced a good reply must not fail
    because a subscriber's queue was full — events are not load-bearing for the correctness
    of the current turn (docs/04 §2).
    """

    bus: EventBus

    async def session_started(self, *, session_id: str, channel: str) -> None:
        await self._emit(
            "conversation.session.started",
            {"session_id": session_id, "channel": channel},
            correlation_id=session_id,
        )

    async def message_received(
        self, *, session_id: str, message_id: str, text: str, trust: str
    ) -> None:
        await self._emit(
            "conversation.message.received",
            {
                "session_id": session_id,
                "message_id": message_id,
                "text": text,
                "trust": trust,
            },
        )

    async def reply_produced(
        self, *, session_id: str, message_id: str, text: str, working_set_ref: str | None
    ) -> None:
        await self._emit(
            "conversation.reply.produced",
            {
                "session_id": session_id,
                "message_id": message_id,
                "text": text,
                "working_set_ref": working_set_ref,
            },
        )

    async def turn_completed(self, summary: TurnSummary) -> None:
        """The event the memory subscriber captures from.

        It carries the turn's substance, not only its identifiers, so capture never has to
        read back a row `finalize` has not written yet (ADR-0018).
        """
        await self._emit(
            "conversation.turn.completed",
            {
                "turn_id": summary.turn_id,
                "session_id": summary.session_id,
                "status": summary.status,
                "input": summary.input,
                "reply": summary.reply,
                "intent": summary.intent,
                "recalled_memory_ids": list(summary.recalled_memory_ids),
                "tool_calls": summary.tool_calls,
                "latency_ms": round(summary.latency_ms, 3),
            },
            correlation_id=summary.correlation_id,
        )

    async def _emit(
        self, type_: str, payload: dict[str, object], *, correlation_id: str | None = None
    ) -> None:
        try:
            await self.bus.emit(type_, payload, source=SOURCE, correlation_id=correlation_id)
        except Exception as error:  # a turn must survive a failed announcement
            logger.warning("could not announce", extra=fields(type=type_, error=str(error)))
