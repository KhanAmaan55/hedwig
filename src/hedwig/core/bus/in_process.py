"""In-process event bus with a durable outbox (docs/04 §3).

The pipeline, in order: validate → append to the outbox → route → per-subscription queue →
isolated dispatch. Everything below implements one of the properties in docs/04 §3.1, and
each is here because of a specific way event systems fail:

* **Durable before dispatch** — a crash between "it happened" and "someone handled it" must
  not lose the fact.
* **At-least-once, per-subscription cursors** — a restart mid-handler re-delivers rather
  than silently skipping. Handlers must therefore be idempotent, and `processed_event` is
  what makes that cheap.
* **Per-subscription queues, isolated dispatch** — a crashing emotion handler must never
  cost a memory write.
* **Declared lossiness** — every subscriber says at registration time whether losing an
  event is acceptable. Under pressure, lossy subscribers drop (and count) while durable
  ones apply backpressure.
* **Depth cap** — a handler that publishes what it consumes is stopped at depth 8 instead of
  spinning a core.
* **Dead letters, never discards** — retries are bounded and the failure stays visible.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from hedwig.core.bus.catalogue import EventCatalogue, platform_catalogue
from hedwig.core.context import correlation_scope, current_correlation_id, new_correlation_id
from hedwig.core.errors import HedwigError, InvalidRequestError
from hedwig.core.ids import new_id, new_ulid
from hedwig.core.logging import fields, get_logger
from hedwig.core.ports import Clock, Delivery, Event, EventHandler, Health, HealthStatus
from hedwig.core.store import Database

logger = get_logger(__name__)

MAX_DEPTH = 8
"""A handler may publish, but eight levels of that is a storm, not a design."""

DEFAULT_QUEUE_SIZE = 512
DEFAULT_HANDLER_TIMEOUT = 30.0
RETRY_DELAYS: tuple[float, ...] = (1.0, 8.0, 60.0)
MAX_DRAIN_PASSES = 1000
"""Bounded so a permanently busy bus cannot make `drain()` hang a shutdown."""
DEFAULT_REPLAY_WINDOW = timedelta(hours=24)

_depth: ContextVar[int] = ContextVar("hedwig_event_depth", default=0)


class EventStormError(HedwigError):
    """Publication exceeded the maximum causal depth."""

    code = "event_storm"


@dataclass(slots=True)
class _Envelope:
    event: Event
    attempts: int = 0
    depth_hint: int = 0
    """Causal depth of the publisher, so the storm cap survives handler-published events."""


@dataclass(slots=True)
class _Subscription:
    name: str
    pattern: str
    handler: EventHandler
    delivery: Delivery
    queue: asyncio.Queue[_Envelope | None]
    timeout: float
    task: asyncio.Task[None] | None = None
    delivered: int = 0
    failed: int = 0
    dropped: int = 0
    dead: int = 0
    active: bool = True
    segments: tuple[str, ...] = field(default_factory=tuple)

    @property
    def durable(self) -> bool:
        return self.delivery is Delivery.AT_LEAST_ONCE


class _SubscriptionHandle:
    """Satisfies the `Subscription` port."""

    def __init__(self, bus: InProcessBus, subscription: _Subscription) -> None:
        self._bus = bus
        self._subscription = subscription

    @property
    def name(self) -> str:
        return self._subscription.name

    @property
    def pattern(self) -> str:
        return self._subscription.pattern

    async def unsubscribe(self) -> None:
        await self._bus._unsubscribe(self._subscription)


class InProcessBus:
    """The `EventBus` implementation.

    Single process, single loop. The transport is an `asyncio.Queue` per subscription; that
    is the only part a future out-of-process bus would replace (docs/02 §2).
    """

    def __init__(
        self,
        database: Database,
        *,
        clock: Clock,
        catalogue: EventCatalogue | None = None,
        queue_size: int = DEFAULT_QUEUE_SIZE,
        handler_timeout: float = DEFAULT_HANDLER_TIMEOUT,
        replay_window: timedelta = DEFAULT_REPLAY_WINDOW,
        strict_types: bool = True,
    ) -> None:
        self._database = database
        self._clock = clock
        self._catalogue = catalogue if catalogue is not None else platform_catalogue()
        self._queue_size = queue_size
        self._handler_timeout = handler_timeout
        self._replay_window = replay_window
        self._strict_types = strict_types

        self._subscriptions: dict[str, _Subscription] = {}
        self._retries: set[asyncio.Task[None]] = set()
        self._in_flight = 0
        self._running = False
        self._published = 0
        self._storms = 0

    # -- catalogue ---------------------------------------------------------

    @property
    def catalogue(self) -> EventCatalogue:
        return self._catalogue

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        """Start dispatchers and re-deliver anything missed while the process was down."""
        if self._running:
            return
        self._running = True
        for subscription in self._subscriptions.values():
            self._ensure_dispatcher(subscription)
        await self._recover()
        logger.info(
            "event bus started",
            extra=fields(subscriptions=len(self._subscriptions), types=len(self._catalogue)),
        )

    async def stop(self) -> None:
        """Drain, then stop dispatchers. Shutdown must not lose a durable event."""
        if not self._running:
            return
        self._running = False
        await self.drain()

        for subscription in self._subscriptions.values():
            if subscription.task is not None:
                await subscription.queue.put(None)
        for subscription in self._subscriptions.values():
            task = subscription.task
            if task is not None:
                with_timeout = asyncio.wait_for(task, timeout=5.0)
                try:
                    await with_timeout
                except (TimeoutError, asyncio.CancelledError):  # pragma: no cover
                    task.cancel()
                subscription.task = None
        logger.info("event bus stopped", extra=fields(published=self._published))

    async def health(self) -> Health:
        open_dead_letters = self._open_dead_letters()
        backlog = max((s.queue.qsize() for s in self._subscriptions.values()), default=0)

        status = HealthStatus.OK
        message = ""
        if open_dead_letters:
            status = HealthStatus.DEGRADED
            message = f"{open_dead_letters} unresolved dead letters"
        elif backlog > self._queue_size // 2:
            status = HealthStatus.DEGRADED
            message = f"subscription backlog {backlog}"

        return Health(
            status=status,
            message=message,
            detail={
                "running": self._running,
                "subscriptions": len(self._subscriptions),
                "published": self._published,
                "max_queue_depth": backlog,
                "dead_letters": open_dead_letters,
                "dropped": sum(s.dropped for s in self._subscriptions.values()),
            },
        )

    # -- publishing --------------------------------------------------------

    async def emit(
        self,
        type: str,  # noqa: A002 - matches the field name on the wire
        payload: Mapping[str, Any] | None = None,
        *,
        source: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> Event:
        event = Event(
            id=new_ulid(),
            type=type,
            occurred_at=self._clock.now(),
            source=source,
            correlation_id=correlation_id or current_correlation_id() or new_correlation_id("evt"),
            payload=dict(payload or {}),
            causation_id=causation_id,
        )
        await self.publish(event)
        return event

    async def publish(self, event: Event) -> None:
        if self._strict_types:
            self._catalogue.validate(event.type, event.payload)

        depth = _depth.get()
        if depth >= MAX_DEPTH:
            self._storms += 1
            raise EventStormError(
                f"event depth {depth} reached while publishing {event.type!r}; "
                "a handler is publishing what it consumes",
                type=event.type,
                depth=depth,
            )

        self._persist(event)
        self._published += 1

        for subscription in list(self._subscriptions.values()):
            if subscription.active and self._matches(subscription, event.type):
                await self._enqueue(subscription, _Envelope(event=event, depth_hint=depth))

    def _persist(self, event: Event) -> None:
        try:
            payload = json.dumps(dict(event.payload), default=str, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise InvalidRequestError(
                f"event {event.type!r} has a payload that is not JSON-serialisable: {exc}"
            ) from exc

        self._database.execute(
            """
            INSERT INTO event (id, type, schema_version, occurred_at, source,
                               correlation_id, causation_id, principal_id, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.id,
                event.type,
                event.schema_version,
                event.occurred_at.isoformat(timespec="milliseconds"),
                event.source,
                event.correlation_id,
                event.causation_id,
                event.principal_id,
                payload,
            ),
        )

    # -- subscribing -------------------------------------------------------

    def subscribe(
        self,
        pattern: str,
        handler: EventHandler,
        *,
        name: str,
        delivery: Delivery = Delivery.AT_LEAST_ONCE,
        timeout: float | None = None,
    ) -> _SubscriptionHandle:
        if name in self._subscriptions:
            raise InvalidRequestError(f"subscription {name!r} already exists", name=name)

        subscription = _Subscription(
            name=name,
            pattern=pattern,
            handler=handler,
            delivery=delivery,
            queue=asyncio.Queue(maxsize=self._queue_size),
            timeout=timeout if timeout is not None else self._handler_timeout,
            segments=tuple(pattern.split(".")),
        )
        self._subscriptions[name] = subscription
        if self._running:
            self._ensure_dispatcher(subscription)

        logger.debug(
            "subscription registered",
            extra=fields(name=name, pattern=pattern, delivery=delivery.value),
        )
        return _SubscriptionHandle(self, subscription)

    async def _unsubscribe(self, subscription: _Subscription) -> None:
        subscription.active = False
        self._subscriptions.pop(subscription.name, None)
        if subscription.task is not None:
            await subscription.queue.put(None)
            try:
                await asyncio.wait_for(subscription.task, timeout=5.0)
            except (TimeoutError, asyncio.CancelledError):  # pragma: no cover
                subscription.task.cancel()
            subscription.task = None

    @staticmethod
    def _matches(subscription: _Subscription, event_type: str) -> bool:
        if subscription.pattern == "*":
            return True
        segments = event_type.split(".")
        if len(segments) != len(subscription.segments):
            return False
        return all(
            wanted in ("*", actual)
            for wanted, actual in zip(subscription.segments, segments, strict=True)
        )

    # -- dispatch ----------------------------------------------------------

    def _ensure_dispatcher(self, subscription: _Subscription) -> None:
        if subscription.task is None or subscription.task.done():
            subscription.task = asyncio.create_task(
                self._dispatch_loop(subscription), name=f"bus:{subscription.name}"
            )

    async def _enqueue(self, subscription: _Subscription, envelope: _Envelope) -> None:
        try:
            subscription.queue.put_nowait(envelope)
            return
        except asyncio.QueueFull:
            pass

        if subscription.durable:
            # Declared as unable to lose events, so the publisher waits instead. That is
            # what backpressure means, and it is why publish() is async (docs/04 §3.1).
            logger.warning(
                "durable subscription at capacity; applying backpressure",
                extra=fields(subscription=subscription.name, depth=subscription.queue.qsize()),
            )
            await subscription.queue.put(envelope)
            return

        # Lossy: drop the oldest, keep the newest, and count it. A silent drop is how an
        # event system starts lying to you (docs/04 §3.1).
        try:
            subscription.queue.get_nowait()
            subscription.queue.task_done()
        except asyncio.QueueEmpty:  # pragma: no cover - racy but harmless
            pass
        subscription.dropped += 1
        subscription.queue.put_nowait(envelope)

    async def _dispatch_loop(self, subscription: _Subscription) -> None:
        while True:
            envelope = await subscription.queue.get()
            if envelope is None:
                subscription.queue.task_done()
                return
            try:
                await self._deliver(subscription, envelope)
            finally:
                subscription.queue.task_done()

    async def _deliver(self, subscription: _Subscription, envelope: _Envelope) -> None:
        event = envelope.event

        if subscription.durable and self._already_processed(subscription.name, event.id):
            self._advance_cursor(subscription.name, event.id)
            return

        self._in_flight += 1
        started = time.monotonic()
        try:
            with correlation_scope(event.correlation_id, causation_id=event.id):
                token = _depth.set(envelope.depth_hint + 1)
                try:
                    await asyncio.wait_for(
                        subscription.handler(event), timeout=subscription.timeout
                    )
                finally:
                    _depth.reset(token)
        except Exception as exc:
            await self._on_failure(subscription, envelope, exc)
        else:
            subscription.delivered += 1
            if subscription.durable:
                self._record(subscription.name, event.id, "ok", envelope.attempts + 1, None)
                self._advance_cursor(subscription.name, event.id)
            logger.debug(
                "event delivered",
                extra={
                    "duration_ms": round((time.monotonic() - started) * 1000, 2),
                    "fields": {"subscription": subscription.name, "type": event.type},
                },
            )
        finally:
            self._in_flight -= 1

    async def _on_failure(
        self, subscription: _Subscription, envelope: _Envelope, exc: BaseException
    ) -> None:
        envelope.attempts += 1
        subscription.failed += 1
        error = f"{type(exc).__name__}: {exc}"

        if envelope.attempts <= len(RETRY_DELAYS):
            delay = RETRY_DELAYS[envelope.attempts - 1]
            if subscription.durable:
                self._record(
                    subscription.name, envelope.event.id, "failed", envelope.attempts, error
                )
            logger.warning(
                "event handler failed, retrying",
                extra=fields(
                    subscription=subscription.name,
                    type=envelope.event.type,
                    attempt=envelope.attempts,
                    retry_in_s=delay,
                    error=error,
                ),
            )
            self._schedule_retry(subscription, envelope, delay)
            return

        subscription.dead += 1
        if subscription.durable:
            self._record(subscription.name, envelope.event.id, "dead", envelope.attempts, error)
            self._dead_letter(subscription.name, envelope, error)
            self._advance_cursor(subscription.name, envelope.event.id)
        logger.error(
            "event handler dead-lettered",
            extra=fields(
                subscription=subscription.name,
                type=envelope.event.type,
                event_id=envelope.event.id,
                attempts=envelope.attempts,
                error=error,
            ),
        )

    def _schedule_retry(
        self, subscription: _Subscription, envelope: _Envelope, delay: float
    ) -> None:
        async def retry() -> None:
            await self._clock.sleep(delay)
            if subscription.active:
                await self._enqueue(subscription, envelope)

        task = asyncio.create_task(retry(), name=f"bus-retry:{subscription.name}")
        self._retries.add(task)
        task.add_done_callback(self._retries.discard)

    # -- persistence helpers ----------------------------------------------

    def _already_processed(self, subscription: str, event_id: str) -> bool:
        row = self._database.query_one(
            "SELECT status FROM processed_event WHERE subscription = ? AND event_id = ?",
            (subscription, event_id),
        )
        return row is not None and row["status"] in ("ok", "dead")

    def _record(
        self, subscription: str, event_id: str, status: str, attempts: int, error: str | None
    ) -> None:
        self._database.execute(
            """
            INSERT INTO processed_event (subscription, event_id, status, attempts,
                                         last_error, processed_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (subscription, event_id) DO UPDATE SET
                status = excluded.status,
                attempts = excluded.attempts,
                last_error = excluded.last_error,
                processed_at = excluded.processed_at
            """,
            (
                subscription,
                event_id,
                status,
                attempts,
                error,
                self._clock.now().isoformat(timespec="milliseconds"),
            ),
        )

    def _advance_cursor(self, subscription: str, event_id: str) -> None:
        self._database.execute(
            """
            INSERT INTO subscription_cursor (subscription, last_event_id, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT (subscription) DO UPDATE SET
                last_event_id = CASE
                    WHEN excluded.last_event_id > subscription_cursor.last_event_id
                    THEN excluded.last_event_id ELSE subscription_cursor.last_event_id END,
                updated_at = excluded.updated_at
            """,
            (subscription, event_id, self._clock.now().isoformat(timespec="milliseconds")),
        )

    def _dead_letter(self, subscription: str, envelope: _Envelope, error: str) -> None:
        self._database.execute(
            """
            INSERT INTO event_dead_letter (id, subscription, event_id, attempts, error, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("dl"),
                subscription,
                envelope.event.id,
                envelope.attempts,
                error,
                self._clock.now().isoformat(timespec="milliseconds"),
            ),
        )

    def _open_dead_letters(self) -> int:
        row = self._database.query_one(
            "SELECT COUNT(*) AS n FROM event_dead_letter WHERE resolved_at IS NULL"
        )
        return int(row["n"]) if row else 0

    # -- recovery and replay ----------------------------------------------

    async def _recover(self) -> None:
        """Re-deliver durable events that were never processed (docs/04 §3.3)."""
        since = (self._clock.now() - self._replay_window).isoformat(timespec="milliseconds")
        recovered = 0

        for subscription in self._subscriptions.values():
            if not subscription.durable:
                continue
            cursor_row = self._database.query_one(
                "SELECT last_event_id FROM subscription_cursor WHERE subscription = ?",
                (subscription.name,),
            )
            cursor = cursor_row["last_event_id"] if cursor_row else None

            rows = self._database.query(
                """
                SELECT e.* FROM event e
                LEFT JOIN processed_event p
                       ON p.event_id = e.id AND p.subscription = ?
                WHERE e.occurred_at >= ?
                  AND (? IS NULL OR e.id > ?)
                  AND (p.status IS NULL OR p.status = 'failed')
                ORDER BY e.id
                LIMIT 1000
                """,
                (subscription.name, since, cursor, cursor),
            )
            for row in rows:
                event = _row_to_event(row)
                if self._matches(subscription, event.type):
                    await self._enqueue(subscription, _Envelope(event=event))
                    recovered += 1

        if recovered:
            logger.info("recovered undelivered events", extra=fields(count=recovered))

    async def replay(self, *, since: datetime, pattern: str = "*") -> AsyncIterator[Event]:
        segments = tuple(pattern.split("."))
        rows = self._database.query(
            "SELECT * FROM event WHERE occurred_at >= ? ORDER BY id",
            (since.isoformat(timespec="milliseconds"),),
        )
        for row in rows:
            event = _row_to_event(row)
            if pattern == "*" or _segments_match(segments, event.type):
                yield event

    async def drain(self) -> None:
        """Wait until every queued event and every scheduled retry has been handled.

        Quiescence is three conditions at once — nothing queued, nothing in flight, no
        retry outstanding — and it has to be checked *after* the loop has yielded, because a
        handler that has just failed schedules its retry through `call_soon`. Checking
        earlier sees a moment that looks idle and is not, which would let `stop()` drop a
        durable event that was one retry away from succeeding.
        """
        for _ in range(MAX_DRAIN_PASSES):
            pending = [task for task in self._retries if not task.done()]
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

            await asyncio.gather(
                *(subscription.queue.join() for subscription in self._subscriptions.values())
            )

            # Two yields: one for the dispatcher's done-callbacks, one for any retry task
            # those callbacks created.
            await asyncio.sleep(0)
            await asyncio.sleep(0)

            if self._quiescent():
                return

        logger.warning(
            "drain gave up while work was still outstanding",
            extra=fields(passes=MAX_DRAIN_PASSES, in_flight=self._in_flight),
        )

    def _quiescent(self) -> bool:
        return (
            self._in_flight == 0
            and all(task.done() for task in self._retries)
            and all(subscription.queue.empty() for subscription in self._subscriptions.values())
        )

    # -- introspection -----------------------------------------------------

    def stats(self) -> Mapping[str, Any]:
        return {
            "published": self._published,
            "storms": self._storms,
            "subscriptions": {
                subscription.name: {
                    "pattern": subscription.pattern,
                    "delivery": subscription.delivery.value,
                    "delivered": subscription.delivered,
                    "failed": subscription.failed,
                    "dropped": subscription.dropped,
                    "dead": subscription.dead,
                    "queued": subscription.queue.qsize(),
                }
                for subscription in self._subscriptions.values()
            },
        }


def _segments_match(pattern: tuple[str, ...], event_type: str) -> bool:
    segments = event_type.split(".")
    if len(segments) != len(pattern):
        return False
    return all(wanted in ("*", actual) for wanted, actual in zip(pattern, segments, strict=True))


def _row_to_event(row: Any) -> Event:
    return Event(
        id=row["id"],
        type=row["type"],
        occurred_at=datetime.fromisoformat(row["occurred_at"]),
        source=row["source"],
        correlation_id=row["correlation_id"],
        payload=json.loads(row["payload"]),
        causation_id=row["causation_id"],
        principal_id=row["principal_id"],
        schema_version=row["schema_version"],
    )
