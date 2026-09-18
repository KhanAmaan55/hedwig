"""The emotion engine (docs/09).

Subscribes to events, appraises each one by rule, coalesces the deltas, and integrates them
on a tick. Everything that decides *anything* lives in the three pure modules beside this
one; this file is the part that talks to the bus, the clock and the database.

**Why a tick rather than per-event** (docs/09 §5.2): per-event updates cause version-conflict
storms on a single guarded row, fire `emotion.state.changed` dozens of times per turn — which
the avatar would try to render — and make the history unreadable. A 30-second coalescing tick
fixes all three at the cost of latency nobody can perceive.

The engine is deterministic: the same events against the same clock produce the same vector,
bit for bit. That is the property §4.2 gave up a model to keep, and `tests/unit/
test_emotion_engine.py` asserts it rather than assuming it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from hedwig.core.logging import fields, get_logger
from hedwig.core.ports import Clock, EventBus, Health, HealthStatus
from hedwig.core.ports.emotion import (
    DIMENSIONS,
    Appraisal,
    Dimension,
    EmotionSnapshot,
    EmotionState,
)
from hedwig.core.ports.event_bus import Event
from hedwig.core.ports.scheduler import TaskContext, TaskResult
from hedwig.emotion.appraisal import RULES, Appraiser
from hedwig.emotion.bindings import behaviour
from hedwig.emotion.dynamics import Baselines, circadian_energy, integrate
from hedwig.emotion.mapping import combine, deltas_for
from hedwig.emotion.store import EmotionStore

logger = get_logger(__name__)

SUBSCRIPTION = "emotion-appraiser"
TICK_SECONDS = 30.0
MIN_PUBLISH_DELTA = 0.02
"""L1 change below which nothing is published. Keeps the event log and the avatar quiet
during idle periods (docs/09 §5.2)."""

HIGH = 0.70
LOW = 0.30
HYSTERESIS = 0.05
"""A dimension must fall 0.05 back inside the band before it can cross again. Without it a
value sitting on a threshold publishes a crossing every tick."""


@dataclass(frozen=True, slots=True)
class _Pending:
    """One appraised event, waiting for the next tick."""

    event_type: str
    event_id: str | None
    correlation_id: str | None
    appraisal: Appraisal
    deltas: dict[Dimension, float]


class EmotionEngine:
    """The service. Owns the state, the tick, and the events it publishes."""

    def __init__(
        self,
        store: EmotionStore,
        *,
        clock: Clock,
        bus: EventBus | None = None,
        baselines: Baselines | None = None,
        timezone: str = "",
        min_publish_delta: float = MIN_PUBLISH_DELTA,
    ) -> None:
        self._store = store
        self._clock = clock
        self._bus = bus
        self._baselines = baselines or Baselines.from_traits()
        self._timezone = _resolve_timezone(timezone)
        self._min_publish_delta = min_publish_delta
        self._appraiser = Appraiser()
        self._pending: list[_Pending] = []
        self._state = store.load() or EmotionState()
        self._zones: dict[Dimension, str] = {d: _zone_of(self._state[d]) for d in DIMENSIONS}
        self._subscription: Any = None
        self._ticks = 0
        self._reference: str | None = None
        """The last history row written. Handed to a turn so the reply it produces can
        be joined back to the mood it was produced under (docs/07 §14.2)."""
        self._correlation: str | None = None

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        """Subscribe, and settle the state for however long the process was down.

        The catch-up tick matters: a companion restarted after a week should not resume the
        mood it had a week ago. Decay is a function of elapsed time, so one tick with no
        appraisals resolves the whole gap.
        """
        if self._bus is not None and self._subscription is None:
            for event_type in RULES:
                self._bus.subscribe(
                    event_type,
                    self._on_event,
                    name=f"{SUBSCRIPTION}:{event_type}",
                )
            self._subscription = True

        now = self._clock.now()
        self._state = self._store.load() or self._state
        await self.tick(cause="restore")
        logger.info(
            "emotion engine started",
            extra=fields(**self._state.as_dict(), local_hour=self._local(now).hour),
        )

    async def stop(self) -> None:
        """Persist whatever has not been integrated yet.

        Without this, up to one tick of appraisals is lost on every shutdown — small, but it
        would make the history quietly wrong at exactly the moments a user restarts.
        """
        if self._pending:
            await self.tick()

    # -- the read side -----------------------------------------------------

    async def state(self) -> EmotionState:
        return self._state

    async def snapshot(self) -> EmotionSnapshot:
        energy_baseline = circadian_energy(self._local(self._clock.now()))
        return EmotionSnapshot(
            state=self._state,
            valence=self._state.valence,
            arousal=self._state.arousal,
            behaviour=behaviour(self._state),
            baselines=self._baselines.as_dict(energy=energy_baseline),
            reference=self._reference,
        )

    async def history(
        self, *, since: datetime | None = None, limit: int = 200
    ) -> list[Mapping[str, Any]]:
        return list(self._store.history(since=since, limit=limit))

    # -- the write side ----------------------------------------------------

    async def _on_event(self, event: Event) -> None:
        """Appraise one event and queue it. Never integrates — that is the tick's job."""
        appraisal = self._appraiser.appraise(event)
        if appraisal.is_neutral:
            return

        self._pending.append(
            _Pending(
                event_type=event.type,
                event_id=event.id,
                correlation_id=event.correlation_id,
                appraisal=appraisal,
                deltas=deltas_for(appraisal),
            )
        )

    async def tick(self, *, cause: str | None = None) -> EmotionState:
        """Apply everything since the last tick, plus decay. The transition rule, applied.

        Idempotent in the way that matters: calling it twice in the same instant applies the
        queued appraisals once and computes zero decay for the second call.
        """
        now = self._clock.now()
        last = self._store.ticked_at() or now
        elapsed = max(timedelta(0), now - last)

        batch, self._pending = self._pending, []
        previous = self._state

        state = integrate(
            previous,
            deltas=combine([item.deltas for item in batch]),
            baselines=self._baselines,
            energy_baseline=circadian_energy(self._local(now)),
            elapsed=elapsed,
            now=now,
        )
        self._state = state
        self._ticks += 1
        self._store.save(state, ticked_at=now)

        appraisal_id: str | None = None
        for item in batch:
            appraisal_id = self._store.record_appraisal(
                item.appraisal,
                event_type=item.event_type,
                event_id=item.event_id,
                deltas=item.deltas,
                correlation_id=item.correlation_id,
            )

        # A coalesced tick genuinely has more than one cause; naming the most recent is a
        # reporting convention, not a claim (docs/07 §14.5).
        self._correlation = batch[-1].correlation_id if batch else None

        moved = previous.distance(state)
        if moved >= self._min_publish_delta or cause == "restore":
            resolved = cause or ("appraisal" if batch else "decay")
            self._reference = self._store.record_history(
                state,
                cause=resolved,
                appraisal_id=appraisal_id,
                correlation_id=self._correlation,
            )
            await self._announce_state(state, cause=resolved)

        await self._announce_thresholds(state)
        return state

    async def tick_task(self, context: TaskContext) -> TaskResult:
        """The scheduler's entry point (docs/17 §5).

        A thin wrapper so the tick reports what it did into the run record: a scheduler
        history of "emotion.tick: completed" thirty times an hour tells you nothing, and
        the stats below tell you whether anything is actually moving.
        """
        before = self._state
        state = await self.tick()
        return TaskResult(
            stats={
                "moved": round(before.distance(state), 6),
                "valence": state.valence,
                **state.as_dict(),
            }
        )

    # -- events out --------------------------------------------------------

    async def _announce_state(self, state: EmotionState, *, cause: str) -> None:
        """A full snapshot, never a delta (docs/02 §4.2).

        Out-of-order delivery and restart replay are then both harmless: a subscriber that
        misses one update is not left with a state assembled from half the changes.
        """
        await self._emit(
            "emotion.state.changed",
            {
                "state": state.as_dict(),
                "valence": state.valence,
                "arousal": state.arousal,
                "cause": cause,
            },
        )

    async def _announce_thresholds(self, state: EmotionState) -> None:
        for dimension in DIMENSIONS:
            zone = _zone_of(state[dimension], previous=self._zones[dimension])
            if zone == self._zones[dimension]:
                continue
            self._zones[dimension] = zone
            await self._emit(
                "emotion.threshold.crossed",
                {
                    "dimension": dimension.value,
                    "direction": zone,
                    "value": round(state[dimension], 6),
                },
            )

    async def _emit(self, type_: str, payload: dict[str, Any]) -> None:
        if self._bus is None:
            return
        try:
            # Correlated to the turn that moved the mood, so a turn, the memories it formed
            # and the state it produced share one id — which is the whole of docs/04 §6
            # made real for cognition.
            await self._bus.emit(type_, payload, source="emotion", correlation_id=self._correlation)
        except Exception as error:  # a mood must not be able to break the process
            logger.warning("could not announce", extra=fields(type=type_, error=str(error)))

    # -- diagnostics -------------------------------------------------------

    def _local(self, moment: datetime) -> datetime:
        return moment.astimezone(self._timezone) if self._timezone else moment.astimezone()

    async def health(self) -> Health:
        """Degraded when a dimension is pinned at an extreme.

        docs/09 §9 lists a runaway dimension as the first failure mode. The clamps stop it
        being unbounded; this is what stops it being unnoticed.
        """
        pinned = [d.value for d in DIMENSIONS if self._state[d] >= 0.98 or self._state[d] <= 0.02]
        status = HealthStatus.DEGRADED if pinned else HealthStatus.OK
        return Health(
            status=status,
            message=f"pinned at an extreme: {', '.join(pinned)}" if pinned else "",
            detail={
                **self._state.as_dict(),
                "valence": self._state.valence,
                "arousal": self._state.arousal,
                "ticks": self._ticks,
                "pending_appraisals": len(self._pending),
                **self._store.counts(),
            },
        )


def _zone_of(value: float, *, previous: str = "mid") -> str:
    """Which band a value is in, with hysteresis so a value on a threshold does not flap."""
    if previous == "high":
        return "high" if value > HIGH - HYSTERESIS else _zone_of(value)
    if previous == "low":
        return "low" if value < LOW + HYSTERESIS else _zone_of(value)
    if value >= HIGH:
        return "high"
    if value <= LOW:
        return "low"
    return "mid"


def _resolve_timezone(name: str) -> tzinfo | None:
    """`None` means the system's local zone, which is the right default for a local-first app."""
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("unknown timezone, falling back to system local", extra=fields(tz=name))
        return None
