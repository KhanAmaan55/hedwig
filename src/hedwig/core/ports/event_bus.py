"""The `EventBus` port — interface only.

No implementation exists yet, by design: Milestone 1 defines the shape so that later
milestones can be written against it, and so that the command/event rule is visible in
code from the first commit.

The rule that governs every use of this port (docs/04 §2):

    Commands are direct calls through a port. Events are published facts.
    If the caller needs a result, it is a command, not an event.

Consequences that are part of the contract, not of any particular implementation:

* A publisher never learns whether anyone handled its event, and never waits for effects.
* Events must not be load-bearing for the correctness of the current turn. Anything the
  user would notice as data loss is a command inside a transaction.
* Delivery is at-least-once, so **every handler must be idempotent**.
* Ordering is guaranteed per subscription, never globally.
* Cross-module state events carry full snapshots, not deltas, so out-of-order delivery
  and restart replay are both harmless (docs/02 §4.2).

The implementation planned for Milestone 2 is documented in docs/04 §3: validate, append
to a durable outbox, route, per-subscription queue, isolated dispatch.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class Delivery(StrEnum):
    """How much a subscriber cares about missing an event.

    Declared at subscription time so the question is answered in code review rather than
    discovered under load (docs/04 §7).
    """

    AT_LEAST_ONCE = "at_least_once"
    """Never dropped. Under backpressure the publisher is slowed instead."""

    LOSSY = "lossy"
    """May be dropped, newest-wins, when the subscriber falls behind. Drops are counted."""


@dataclass(frozen=True, slots=True)
class Event:
    """A fact that has already happened.

    `type` is always `domain.noun.verb_past` — three segments, past tense. Unknown types
    are a hard error rather than a warning: an event name typo that silently goes nowhere
    is among the nastiest bugs an event system can have.

    `correlation_id` is constant for a whole turn or job run; `causation_id` points at the
    event that caused this one. Together they form the causal graph the Mind Inspector
    renders and `/v1/explain` walks (docs/04 §4).
    """

    id: str
    type: str
    occurred_at: datetime
    source: str
    correlation_id: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    causation_id: str | None = None
    principal_id: str = "local"
    schema_version: int = 1


EventHandler = Callable[[Event], Awaitable[None]]
"""Handlers are async, idempotent, and independent of one another."""


@runtime_checkable
class Subscription(Protocol):
    """A live registration. Cancelling it stops delivery; it does not lose the cursor."""

    @property
    def name(self) -> str:
        """Stable identifier, used as the dedupe and cursor key across restarts."""
        ...

    @property
    def pattern(self) -> str:
        """Event type pattern, `*` per segment: `conversation.*.*`, `*.state.changed`."""
        ...

    async def unsubscribe(self) -> None: ...


@runtime_checkable
class EventBus(Protocol):
    """Publish/subscribe with a durable outbox."""

    async def publish(self, event: Event) -> None:
        """Record and dispatch an event.

        Returns once the event is durable, not once handlers have run. Raises only if the
        event is invalid or cannot be recorded — never because a handler failed.
        """
        ...

    async def emit(
        self,
        type: str,  # noqa: A002 - `type` is the field's name on the wire
        payload: Mapping[str, Any] | None = None,
        *,
        source: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> Event:
        """Build and publish an event in one step.

        The ordinary way to publish. Identifier, timestamp and correlation come from the
        bus's own clock and the ambient correlation scope, so no caller has to reach for
        the clock — which is what keeps the seam in docs/03 §5.6 intact.
        """
        ...

    def subscribe(
        self,
        pattern: str,
        handler: EventHandler,
        *,
        name: str,
        delivery: Delivery = Delivery.AT_LEAST_ONCE,
    ) -> Subscription:
        """Register `handler` for every event matching `pattern`.

        `name` must be stable across restarts: it keys the delivery cursor and the
        idempotency record.
        """
        ...

    def replay(self, *, since: datetime, pattern: str = "*") -> AsyncIterator[Event]:
        """Re-read recorded events. For recovery and inspection, not state reconstruction.

        HEDWIG is deliberately not event-sourced (ADR-0003): authoritative state lives in
        ordinary tables, and replay is bounded to a short window.
        """
        ...

    async def drain(self) -> None:
        """Wait for in-flight handlers to finish. For shutdown and tests."""
        ...
