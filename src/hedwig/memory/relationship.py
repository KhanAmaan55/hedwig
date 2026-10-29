"""Relationship memory (docs/06 §3.3, docs/27 §5).

Social memory: how HEDWIG stands with someone. Today that is one row — the user — but the
table is per-entity, as docs/05 specifies, so the day there is a second person nothing about
the model changes.

**Applied at session end, never per turn.** Relationships do not lurch. The inputs arrive per
turn, so they are accumulated durably in a tally and folded in once, when the session ends:

    familiarity <- familiarity + 0.05 * (1 - familiarity) * min(1, turns / 5)
    affinity    <- affinity    + 0.08 * (session_valence - affinity)
    trust       <- trust       + 0.03 * (reliability_signal - trust)

**Trust does not move yet.** Its documented input — did this entity's claims hold up? — comes
from belief corroboration, which does not exist. The update takes the signal as an argument
and is tested with one; in production none is supplied, and trust stays where it is rather
than being fed a proxy. A fabricated reliability signal would be the worst kind of fabricated
signal: a number that claims to measure whether the user can be believed.

`relation.trust` is per-entity and evidence-accumulated. It is not the emotional `trust`
dimension (ADR-0020).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from hedwig.core.logging import fields, get_logger
from hedwig.core.ports import Clock, EventBus, Health, HealthStatus
from hedwig.core.ports.emotion import EmotionState
from hedwig.core.ports.event_bus import Event
from hedwig.core.ports.personality import RelationshipState
from hedwig.core.store import Database

logger = get_logger(__name__)

USER_ENTITY_ID = "ent_principal_local"
"""The user, as an entity. A fixed id rather than a resolved one: the principal is not
something HEDWIG *discovers*, so creating it must not announce `memory.entity.discovered`
(which emotion would appraise as novelty)."""

K_FAMILIARITY = 0.05
K_AFFINITY = 0.08
K_TRUST = 0.03
TURNS_FOR_FULL_WEIGHT = 5
"""A session of five turns or more counts fully toward familiarity. One exchange is
acquaintance, not familiarity."""

SUBSCRIPTION_TURNS = "memory-relationship-turns"
SUBSCRIPTION_SESSIONS = "memory-relationship-sessions"


@dataclass(frozen=True, slots=True)
class SessionTally:
    turns: int = 0
    valence_sum: float = 0.0
    valence_count: int = 0

    @property
    def mean_valence(self) -> float | None:
        return self.valence_sum / self.valence_count if self.valence_count else None


def updated(
    state: RelationshipState,
    *,
    tally: SessionTally,
    reliability: float | None = None,
    at: datetime | None = None,
) -> RelationshipState:
    """The update rule (docs/06 §3.3). Pure.

    Each term only moves when its input exists. A session with no recorded mood leaves
    affinity alone; no reliability signal leaves trust alone. Absent is not neutral — feeding
    0 or 0.5 in place of a missing signal would drag the relationship toward a number nobody
    measured.
    """
    if tally.turns <= 0:
        return state

    weight = min(1.0, tally.turns / TURNS_FOR_FULL_WEIGHT)
    familiarity = state.familiarity + K_FAMILIARITY * (1.0 - state.familiarity) * weight

    affinity = state.affinity
    if (valence := tally.mean_valence) is not None:
        affinity = state.affinity + K_AFFINITY * (valence - state.affinity)

    trust = state.trust
    if reliability is not None:
        trust = state.trust + K_TRUST * (max(0.0, min(1.0, reliability)) - state.trust)

    return replace(
        state,
        familiarity=round(max(0.0, min(1.0, familiarity)), 9),
        affinity=round(max(-1.0, min(1.0, affinity)), 9),
        trust=round(max(0.0, min(1.0, trust)), 9),
        interaction_count=state.interaction_count + tally.turns,
        last_interaction_at=at or state.last_interaction_at,
    )


class RelationshipMemory:
    """The `RelationshipReader` implementation, and the subscriber that keeps it current."""

    def __init__(self, database: Database, *, clock: Clock, bus: EventBus | None = None) -> None:
        self._db = database
        self._clock = clock
        self._bus = bus
        self._subscribed = False
        self._ensure_user()

    # -- lifecycle -------------------------------------------------------------

    async def start(self) -> None:
        if self._bus is not None and not self._subscribed:
            self._bus.subscribe(
                "conversation.turn.completed", self._on_turn, name=SUBSCRIPTION_TURNS
            )
            self._bus.subscribe(
                "conversation.session.ended", self._on_session_ended, name=SUBSCRIPTION_SESSIONS
            )
            self._subscribed = True

    async def stop(self) -> None:
        return None

    # -- reading ---------------------------------------------------------------

    async def with_user(self) -> RelationshipState:
        return self.get(USER_ENTITY_ID)

    def get(self, entity_id: str) -> RelationshipState:
        row = self._db.query_one("SELECT * FROM relation WHERE entity_id = ?", (entity_id,))
        if row is None:
            return RelationshipState(entity_id=entity_id)
        return RelationshipState(
            entity_id=entity_id,
            familiarity=float(row["familiarity"]),
            affinity=float(row["affinity"]),
            trust=float(row["trust"]),
            interaction_count=int(row["interaction_count"]),
            last_interaction_at=(
                datetime.fromisoformat(str(row["last_interaction_at"]))
                if row["last_interaction_at"]
                else None
            ),
        )

    def tally(self, session_id: str) -> SessionTally:
        row = self._db.query_one(
            "SELECT turns, valence_sum, valence_count FROM relationship_tally WHERE session_id = ?",
            (session_id,),
        )
        if row is None:
            return SessionTally()
        return SessionTally(
            turns=int(row["turns"]),
            valence_sum=float(row["valence_sum"]),
            valence_count=int(row["valence_count"]),
        )

    # -- writing ---------------------------------------------------------------

    async def record_turn(self, session_id: str, *, mood: dict[str, float] | None) -> None:
        """Accumulate one turn. Durable, so a restart mid-session forgets nothing."""
        valence = _valence(mood)
        self._db.execute(
            """
            INSERT INTO relationship_tally
                (session_id, entity_id, turns, valence_sum, valence_count, updated_at)
            VALUES (?, ?, 1, ?, ?, ?)
            ON CONFLICT (session_id) DO UPDATE SET
                turns         = turns + 1,
                valence_sum   = valence_sum + excluded.valence_sum,
                valence_count = valence_count + excluded.valence_count,
                updated_at    = excluded.updated_at
            """,
            (
                session_id,
                USER_ENTITY_ID,
                valence or 0.0,
                1 if valence is not None else 0,
                _iso(self._clock.now()),
            ),
        )

    async def close_session(
        self,
        session_id: str,
        *,
        reliability: float | None = None,
        correlation_id: str | None = None,
    ) -> RelationshipState | None:
        """Fold a finished session into the relationship, once.

        The tally is deleted in the same transaction as the update, so a redelivered
        `session.ended` finds nothing to apply rather than counting the session twice.
        """
        tally = self.tally(session_id)
        if tally.turns <= 0:
            return None

        now = self._clock.now()
        before = self.get(USER_ENTITY_ID)
        after = updated(before, tally=tally, reliability=reliability, at=now)
        at = _iso(now)
        with self._db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO relation (entity_id, familiarity, affinity, trust,
                    interaction_count, last_interaction_at, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT (entity_id) DO UPDATE SET
                    familiarity = excluded.familiarity, affinity = excluded.affinity,
                    trust = excluded.trust, interaction_count = excluded.interaction_count,
                    last_interaction_at = excluded.last_interaction_at,
                    updated_at = excluded.updated_at, version = version + 1
                """,
                (
                    USER_ENTITY_ID,
                    after.familiarity,
                    after.affinity,
                    after.trust,
                    after.interaction_count,
                    at,
                    at,
                    at,
                ),
            )
            connection.execute("DELETE FROM relationship_tally WHERE session_id = ?", (session_id,))

        logger.info(
            "relationship updated",
            extra=fields(session=session_id, turns=tally.turns, **after.as_dict()),
        )
        await self._emit(
            "memory.relationship.updated",
            {"entity_id": USER_ENTITY_ID, **after.as_dict(), "turns": tally.turns},
            correlation_id=correlation_id,
        )
        return after

    # -- events in -----------------------------------------------------------

    async def _on_turn(self, event: Event) -> None:
        session_id = str(event.payload.get("session_id", ""))
        if not session_id:
            return
        mood = event.payload.get("mood")
        await self.record_turn(session_id, mood=dict(mood) if isinstance(mood, dict) else None)

    async def _on_session_ended(self, event: Event) -> None:
        session_id = str(event.payload.get("session_id", ""))
        if session_id:
            await self.close_session(session_id, correlation_id=event.correlation_id)

    # -- internals -------------------------------------------------------------

    def _ensure_user(self) -> None:
        """The principal exists as an entity from the first moment. Idempotent, silent."""
        at = _iso(self._clock.now())
        self._db.execute(
            "INSERT INTO entity (id, kind, name, canonical_key, first_seen_at, created_at, "
            "updated_at) VALUES (?, 'person', 'the user', 'principal:local', ?, ?, ?) "
            "ON CONFLICT DO NOTHING",
            (USER_ENTITY_ID, at, at, at),
        )

    async def _emit(
        self, type_: str, payload: dict[str, Any], *, correlation_id: str | None = None
    ) -> None:
        if self._bus is None:
            return
        try:
            await self._bus.emit(type_, payload, source="memory", correlation_id=correlation_id)
        except Exception as error:  # a relationship update must not break capture
            logger.warning("could not announce", extra=fields(type=type_, error=str(error)))

    async def health(self) -> Health:
        state = self.get(USER_ENTITY_ID)
        open_tallies = self._db.query_one("SELECT COUNT(*) AS n FROM relationship_tally")
        return Health(
            status=HealthStatus.OK,
            detail={
                **state.as_dict(),
                "interaction_count": state.interaction_count,
                "open_sessions_tallied": int(open_tallies["n"]) if open_tallies else 0,
            },
        )


def _valence(mood: dict[str, float] | None) -> float | None:
    """Session valence from the mood a turn ran under, via the one definition of valence.

    Uses `EmotionState.valence` from the port rather than recomputing it, so relationship
    and emotion cannot disagree about what a mood was (docs/09 §3.2). Missing dimensions
    take the state's defaults; a mood with *no* dimensions is no mood, and yields `None`.
    """
    if not mood:
        return None
    defaults = EmotionState()
    state = EmotionState(
        happiness=float(mood.get("happiness", defaults.happiness)),
        trust=float(mood.get("trust", defaults.trust)),
        curiosity=float(mood.get("curiosity", defaults.curiosity)),
        confidence=float(mood.get("confidence", defaults.confidence)),
        energy=float(mood.get("energy", defaults.energy)),
        stress=float(mood.get("stress", defaults.stress)),
    )
    return state.valence


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds")
