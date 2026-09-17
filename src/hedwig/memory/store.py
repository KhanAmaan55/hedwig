"""The memory store (docs/06 §3, docs/05 §5.2).

Persistence for episodic and semantic memory, addressed by identity. Everything here is a
`MemoryStore` method; the hard part — finding a memory when you only know the situation —
lives in `retrieval.py`.

Three behaviours worth reading the code for, because each encodes a decision rather than a
mechanism:

* **`supersede_belief` never mutates.** The old belief keeps its text and gains a
  `valid_to`, which is what lets HEDWIG say "I used to think you were in Berlin". Systems
  that update in place cannot tell you what they used to believe (docs/06 §3.2).
* **`tombstone` refuses to forget a protected memory** and says so by returning `False`,
  rather than silently declining.
* **Lineage is recorded on every derived write.** It is what makes "why do you think that?"
  answerable, and what the forget pass consults before dropping anything.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import AsyncIterator, Sequence
from datetime import datetime
from typing import Any

from hedwig.core.ids import new_id
from hedwig.core.logging import fields, get_logger
from hedwig.core.ports import Clock, EventBus, Health, HealthStatus
from hedwig.core.ports.memory import (
    Belief,
    BeliefStatus,
    DerivationRelation,
    Entity,
    Episode,
    EpisodeKind,
    ForgetReason,
    RetrievedItem,
)
from hedwig.core.store import Database
from hedwig.core.types import (
    EntityId,
    MemoryId,
    MemoryKind,
    Provenance,
    SessionId,
    TrustTier,
)
from hedwig.memory.salience import reinforced_salience

logger = get_logger(__name__)


class SqliteMemoryStore:
    """The `MemoryStore` implementation."""

    def __init__(self, database: Database, *, clock: Clock, bus: EventBus | None = None) -> None:
        self._db = database
        self._clock = clock
        self._bus = bus

    # -- episodic ----------------------------------------------------------

    async def add_episode(self, episode: Episode) -> MemoryId:
        now = self._now()
        with self._db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO episode (
                    id, kind, occurred_at, ended_at, title, content, session_id,
                    salience, base_importance, emotional_charge, confidence,
                    pinned, trust_tier, source_ref, model_id,
                    last_decay_at, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    episode.id,
                    episode.kind.value,
                    _iso(episode.occurred_at),
                    _iso(episode.ended_at),
                    episode.title,
                    episode.content,
                    episode.session_id,
                    episode.salience,
                    episode.base_importance,
                    episode.emotional_charge,
                    episode.confidence,
                    int(episode.pinned),
                    episode.provenance.tier.value,
                    episode.provenance.source_ref,
                    episode.provenance.model_id,
                    now,
                    now,
                    now,
                ),
            )
            for entity_id in episode.entities:
                _link(connection, episode.id, MemoryKind.EPISODE, entity_id, "mentioned", now)
            for source in episode.provenance.derived_from:
                _derive(
                    connection,
                    episode.id,
                    MemoryKind.EPISODE,
                    source,
                    DerivationRelation.SUMMARISED_FROM,
                    now,
                )

        logger.debug(
            "episode stored",
            extra=fields(memory=episode.id, kind=episode.kind.value, salience=episode.salience),
        )
        await self._announce(
            "memory.episode.stored",
            {
                "memory_id": episode.id,
                "salience": episode.salience,
                "entities": list(episode.entities),
            },
        )
        return episode.id

    async def get_episode(self, memory_id: MemoryId) -> Episode | None:
        row = self._db.query_one(
            "SELECT * FROM episode WHERE id = ? AND tombstoned_at IS NULL", (memory_id,)
        )
        return _to_episode(row, self._entities_for(memory_id)) if row else None

    async def recent_episodes(
        self, *, limit: int = 20, before: datetime | None = None
    ) -> Sequence[Episode]:
        """The most recent episodes. Long-term memory read by time rather than relevance."""
        if before is None:
            rows = self._db.query(
                "SELECT * FROM episode WHERE tombstoned_at IS NULL "
                "ORDER BY occurred_at DESC LIMIT ?",
                (limit,),
            )
        else:
            rows = self._db.query(
                "SELECT * FROM episode WHERE tombstoned_at IS NULL AND occurred_at < ? "
                "ORDER BY occurred_at DESC LIMIT ?",
                (_iso(before), limit),
            )
        return tuple(_to_episode(row, ()) for row in rows)

    # -- semantic ----------------------------------------------------------

    async def add_belief(self, belief: Belief) -> MemoryId:
        now = self._now()
        with self._db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO belief (
                    id, statement, subject_id, predicate, object_text, confidence, status,
                    valid_from, valid_to, salience, base_importance, pinned,
                    trust_tier, source_ref, model_id, last_decay_at, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    belief.id,
                    belief.statement,
                    belief.subject_id,
                    belief.predicate,
                    belief.object_text,
                    belief.confidence,
                    belief.status.value,
                    _iso(belief.valid_from),
                    _iso(belief.valid_to),
                    belief.salience,
                    belief.base_importance,
                    int(belief.pinned),
                    belief.provenance.tier.value,
                    belief.provenance.source_ref,
                    belief.provenance.model_id,
                    now,
                    now,
                    now,
                ),
            )
            if belief.subject_id:
                _link(connection, belief.id, MemoryKind.BELIEF, belief.subject_id, "subject", now)
            for source in belief.provenance.derived_from:
                _derive(
                    connection,
                    belief.id,
                    MemoryKind.BELIEF,
                    source,
                    DerivationRelation.EXTRACTED_FROM,
                    now,
                )

        await self._announce(
            "memory.belief.formed",
            {
                "memory_id": belief.id,
                "statement": belief.statement,
                "confidence": belief.confidence,
                "provenance": belief.provenance.tier.value,
            },
        )
        return belief.id

    async def get_belief(self, memory_id: MemoryId) -> Belief | None:
        row = self._db.query_one(
            "SELECT * FROM belief WHERE id = ? AND tombstoned_at IS NULL", (memory_id,)
        )
        return _to_belief(row) if row else None

    async def supersede_belief(self, old: MemoryId, new: Belief) -> MemoryId:
        """Replace a belief while keeping the old one as history."""
        existing = await self.get_belief(old)
        if existing is None:
            raise LookupError(f"belief {old} does not exist")

        # The new belief must exist before the old one can point at it: `superseded_by` is
        # a foreign key, and a dangling reference in a memory system is a false memory
        # (docs/05 §3 rule 8).
        await self.add_belief(new)

        now = self._now()
        self._db.execute(
            "UPDATE belief SET status = 'superseded', valid_to = ?, superseded_by = ?, "
            "updated_at = ?, version = version + 1 WHERE id = ?",
            (now, new.id, now, old),
        )

        await self.record_derivation(new.id, old, DerivationRelation.MERGED_FROM)
        await self._announce(
            "memory.belief.superseded",
            {"old_id": old, "new_id": new.id, "reason": "superseded by a newer belief"},
        )
        return new.id

    async def promote_belief(self, memory_id: MemoryId, *, confidence: float) -> None:
        """Tentative to active. Requires corroboration; the caller decides that."""
        self._db.execute(
            "UPDATE belief SET status = 'active', confidence = ?, updated_at = ?, "
            "version = version + 1 WHERE id = ? AND status = 'tentative'",
            (confidence, self._now(), memory_id),
        )

    async def beliefs_about(
        self, entity: EntityId, *, include_historical: bool = False
    ) -> Sequence[Belief]:
        statuses = (
            ("tentative", "active", "superseded", "retracted")
            if include_historical
            else ("tentative", "active")
        )
        placeholders = ",".join("?" * len(statuses))
        rows = self._db.query(
            f"SELECT * FROM belief WHERE subject_id = ? AND status IN ({placeholders}) "
            "AND tombstoned_at IS NULL ORDER BY valid_from DESC",
            (entity, *statuses),
        )
        return tuple(_to_belief(row) for row in rows)

    # -- entities ----------------------------------------------------------

    async def upsert_entity(self, entity: Entity) -> EntityId:
        now = self._now()
        key = _canonical(entity.name)
        existing = self._db.query_one(
            "SELECT id FROM entity WHERE kind = ? AND canonical_key = ?", (entity.kind, key)
        )
        if existing is not None:
            self._db.execute(
                "UPDATE entity SET mention_count = mention_count + 1, updated_at = ?, "
                "version = version + 1 WHERE id = ?",
                (now, existing["id"]),
            )
            return EntityId(str(existing["id"]))

        self._db.execute(
            "INSERT INTO entity (id, kind, name, canonical_key, aliases, first_seen_at, "
            "mention_count, created_at, updated_at) VALUES (?,?,?,?,?,?,1,?,?)",
            (
                entity.id,
                entity.kind,
                entity.name,
                key,
                json.dumps(list(entity.aliases)),
                now,
                now,
                now,
            ),
        )
        await self._announce(
            "memory.entity.discovered",
            {"entity_id": entity.id, "kind": entity.kind, "name": entity.name},
        )
        return entity.id

    async def resolve_entity(self, name: str) -> Entity | None:
        row = self._db.query_one(
            "SELECT * FROM entity WHERE canonical_key = ? AND merged_into IS NULL",
            (_canonical(name),),
        )
        return _to_entity(row) if row else None

    async def link(self, memory_id: MemoryId, entity: EntityId, *, role: str = "mentioned") -> None:
        with self._db.transaction() as connection:
            kind = MemoryKind.BELIEF if self._is_belief(memory_id) else MemoryKind.EPISODE
            _link(connection, memory_id, kind, entity, role, self._now())

    # -- lineage -----------------------------------------------------------

    async def record_derivation(
        self, derived: MemoryId, source: MemoryId, relation: DerivationRelation
    ) -> None:
        kind = MemoryKind.BELIEF if self._is_belief(derived) else MemoryKind.EPISODE
        source_kind = MemoryKind.BELIEF if self._is_belief(source) else MemoryKind.EPISODE
        with self._db.transaction() as connection:
            _derive(connection, derived, kind, source, relation, self._now(), source_kind)

    async def sources_of(self, memory_id: MemoryId) -> Sequence[MemoryId]:
        rows = self._db.query(
            "SELECT source_id FROM memory_derivation WHERE derived_id = ?", (memory_id,)
        )
        return tuple(MemoryId(str(row["source_id"])) for row in rows)

    async def derived_from(self, memory_id: MemoryId) -> Sequence[MemoryId]:
        """What was built on top of this memory. Used by the forget guard."""
        rows = self._db.query(
            "SELECT derived_id FROM memory_derivation WHERE source_id = ?", (memory_id,)
        )
        return tuple(MemoryId(str(row["derived_id"])) for row in rows)

    # -- lifecycle ---------------------------------------------------------

    async def reinforce(self, ids: Sequence[MemoryId], amount: float, *, cause: str) -> None:
        if not ids or amount <= 0:
            return
        now = self._now()
        for memory_id in ids:
            table = "belief" if self._is_belief(memory_id) else "episode"
            row = self._db.query_one(f"SELECT salience FROM {table} WHERE id = ?", (memory_id,))
            if row is None:
                continue
            self._db.execute(
                f"UPDATE {table} SET salience = ?, updated_at = ?, "
                "version = version + 1 WHERE id = ?",
                (reinforced_salience(float(row["salience"]), amount), now, memory_id),
            )

        await self._announce(
            "memory.item.reinforced",
            {"memory_ids": list(ids), "amount": amount, "cause": cause},
        )

    async def record_access(
        self,
        items: Sequence[RetrievedItem],
        *,
        used_in_reply: bool = False,
        turn_id: str | None = None,
    ) -> None:
        """Log that these memories were surfaced, and reinforce accordingly.

        Retrieval and use are recorded separately because they reinforce by different
        amounts (docs/06 §7.2).
        """
        if not items:
            return

        now = self._now()
        for item in items:
            table = "belief" if item.kind is MemoryKind.BELIEF else "episode"
            self._db.execute(
                "INSERT INTO memory_access (id, memory_id, memory_kind, accessed_at, "
                "retrieval_score, used_in_reply, turn_id) VALUES (?,?,?,?,?,?,?)",
                (
                    new_id("acc"),
                    item.memory_id,
                    item.kind.value,
                    now,
                    item.score,
                    int(used_in_reply),
                    turn_id,
                ),
            )
            self._db.execute(
                f"UPDATE {table} SET access_count = access_count + 1, "
                "last_accessed_at = ? WHERE id = ?",
                (now, item.memory_id),
            )

        from hedwig.memory.salience import reinforcement

        await self.reinforce(
            [item.memory_id for item in items],
            reinforcement(retrieved=True, cited=used_in_reply),
            cause="cited" if used_in_reply else "retrieved",
        )

    async def set_salience(self, memory_id: MemoryId, salience: float) -> None:
        table = "belief" if self._is_belief(memory_id) else "episode"
        self._db.execute(
            f"UPDATE {table} SET salience = ?, last_decay_at = ?, "
            "updated_at = ?, version = version + 1 WHERE id = ?",
            (max(0.0, min(1.0, salience)), self._now(), self._now(), memory_id),
        )

    async def set_pinned(self, memory_id: MemoryId, pinned: bool) -> None:
        """Protect a memory from forgetting, permanently, on the user's word."""
        table = "belief" if self._is_belief(memory_id) else "episode"
        self._db.execute(
            f"UPDATE {table} SET pinned = ?, updated_at = ?, version = version + 1 WHERE id = ?",
            (int(pinned), self._now(), memory_id),
        )

    async def tombstone(self, memory_id: MemoryId, reason: ForgetReason) -> bool:
        """Forget, reversibly. Returns `False` if the memory is protected."""
        is_belief = self._is_belief(memory_id)
        table = "belief" if is_belief else "episode"
        row = self._db.query_one(
            f"SELECT * FROM {table} WHERE id = ? AND tombstoned_at IS NULL",
            (memory_id,),
        )
        if row is None:
            return False
        if row["pinned"] and reason is not ForgetReason.PRIVACY_PURGE:
            # Pinned means the user said never forget this. Only an explicit purge overrules.
            logger.info("refused to forget a pinned memory", extra=fields(memory=memory_id))
            return False

        now = self._now()
        with self._db.transaction() as connection:
            connection.execute(
                "INSERT INTO tombstone (id, memory_id, memory_kind, reason, "
                "salience_at_death, archived_body, reversible, created_at) "
                "VALUES (?,?,?,?,?,?,1,?)",
                (
                    new_id("tomb"),
                    memory_id,
                    "belief" if is_belief else "episode",
                    reason.value,
                    float(row["salience"]),
                    json.dumps(dict(row), default=str),
                    now,
                ),
            )
            connection.execute(
                f"UPDATE {table} SET tombstoned_at = ?, updated_at = ?, "
                "version = version + 1 WHERE id = ?",
                (now, now, memory_id),
            )

        await self._announce(
            "memory.item.forgotten",
            {"memory_id": memory_id, "reason": reason.value, "tombstone_id": memory_id},
        )
        return True

    async def restore(self, memory_id: MemoryId) -> bool:
        table = "belief" if self._is_belief(memory_id) else "episode"
        affected = self._db.execute(
            f"UPDATE {table} SET tombstoned_at = NULL, updated_at = ?, "
            "version = version + 1 WHERE id = ? AND tombstoned_at IS NOT NULL",
            (self._now(), memory_id),
        )
        return affected > 0

    async def iter_for_consolidation(
        self, *, since: datetime, kinds: frozenset[MemoryKind] | None = None
    ) -> AsyncIterator[Episode | Belief]:
        wanted = kinds or frozenset({MemoryKind.EPISODE, MemoryKind.BELIEF})

        if MemoryKind.EPISODE in wanted:
            for row in self._db.query(
                "SELECT * FROM episode WHERE created_at >= ? AND tombstoned_at IS NULL "
                "ORDER BY occurred_at",
                (_iso(since),),
            ):
                yield _to_episode(row, ())

        if MemoryKind.BELIEF in wanted:
            for row in self._db.query(
                "SELECT * FROM belief WHERE created_at >= ? AND tombstoned_at IS NULL "
                "ORDER BY valid_from",
                (_iso(since),),
            ):
                yield _to_belief(row)

    # -- statistics and health --------------------------------------------

    def counts(self) -> dict[str, int]:
        def one(sql: str) -> int:
            row = self._db.query_one(sql)
            return int(row["n"]) if row else 0

        return {
            "episodes": one("SELECT COUNT(*) AS n FROM episode WHERE tombstoned_at IS NULL"),
            "beliefs": one("SELECT COUNT(*) AS n FROM belief WHERE tombstoned_at IS NULL"),
            "tentative_beliefs": one(
                "SELECT COUNT(*) AS n FROM belief WHERE status = 'tentative' "
                "AND tombstoned_at IS NULL"
            ),
            "entities": one("SELECT COUNT(*) AS n FROM entity WHERE merged_into IS NULL"),
            "forgotten": one("SELECT COUNT(*) AS n FROM tombstone"),
            "pinned": one(
                "SELECT COUNT(*) AS n FROM episode WHERE pinned = 1 AND tombstoned_at IS NULL"
            ),
        }

    async def health(self) -> Health:
        counts = self.counts()
        total = counts["episodes"] + counts["beliefs"]
        tentative_ratio = (
            counts["tentative_beliefs"] / counts["beliefs"] if counts["beliefs"] else 0.0
        )

        status = HealthStatus.OK
        message = ""
        if tentative_ratio > 0.5 and counts["beliefs"] > 20:
            # Unverified beliefs accumulating is the confabulation-pressure signal
            # (docs/19 §6.2).
            status = HealthStatus.DEGRADED
            message = f"{tentative_ratio:.0%} of beliefs are still unverified"

        return Health(status=status, message=message, detail={**counts, "total_memories": total})

    # -- helpers -----------------------------------------------------------

    def _now(self) -> str:
        return self._clock.now().isoformat(timespec="milliseconds")

    def _is_belief(self, memory_id: MemoryId) -> bool:
        row = self._db.query_one("SELECT 1 AS hit FROM belief WHERE id = ?", (memory_id,))
        return row is not None

    def _entities_for(self, memory_id: MemoryId) -> tuple[EntityId, ...]:
        rows = self._db.query(
            "SELECT entity_id FROM memory_entity_link WHERE memory_id = ?", (memory_id,)
        )
        return tuple(EntityId(str(row["entity_id"])) for row in rows)

    async def _announce(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._bus is None:
            return
        try:
            await self._bus.emit(event_type, payload, source="memory.store")
        except Exception:  # telemetry must never break the write it describes
            logger.exception("failed to publish memory event", extra=fields(type=event_type))


# -- row mapping -----------------------------------------------------------


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="milliseconds") if value else None


def _dt(value: Any) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _canonical(name: str) -> str:
    """Normalise for deduplication. Prevents "Amaan", "amaan" and " Amaan " becoming three
    entities (docs/06 §10, entity fragmentation)."""
    return " ".join(name.strip().lower().split())


def _provenance(row: Any) -> Provenance:
    return Provenance(
        tier=TrustTier(row["trust_tier"]),
        source_ref=row["source_ref"],
        model_id=row["model_id"],
        captured_at=_dt(row["created_at"]),
    )


def _to_episode(row: Any, entities: tuple[EntityId, ...]) -> Episode:
    occurred = _dt(row["occurred_at"])
    assert occurred is not None
    return Episode(
        id=MemoryId(str(row["id"])),
        occurred_at=occurred,
        ended_at=_dt(row["ended_at"]),
        title=str(row["title"]),
        content=str(row["content"]),
        kind=EpisodeKind(row["kind"]),
        session_id=SessionId(str(row["session_id"])) if row["session_id"] else None,
        entities=entities,
        salience=float(row["salience"]),
        base_importance=float(row["base_importance"]),
        emotional_charge=float(row["emotional_charge"]),
        confidence=float(row["confidence"]),
        access_count=int(row["access_count"]),
        last_accessed_at=_dt(row["last_accessed_at"]),
        pinned=bool(row["pinned"]),
        provenance=_provenance(row),
        tombstoned_at=_dt(row["tombstoned_at"]),
    )


def _to_belief(row: Any) -> Belief:
    valid_from = _dt(row["valid_from"])
    assert valid_from is not None
    return Belief(
        id=MemoryId(str(row["id"])),
        statement=str(row["statement"]),
        valid_from=valid_from,
        status=BeliefStatus(row["status"]),
        subject_id=EntityId(str(row["subject_id"])) if row["subject_id"] else None,
        predicate=row["predicate"],
        object_text=row["object_text"],
        confidence=float(row["confidence"]),
        salience=float(row["salience"]),
        base_importance=float(row["base_importance"]),
        access_count=int(row["access_count"]),
        last_accessed_at=_dt(row["last_accessed_at"]),
        pinned=bool(row["pinned"]),
        valid_to=_dt(row["valid_to"]),
        superseded_by=MemoryId(str(row["superseded_by"])) if row["superseded_by"] else None,
        provenance=_provenance(row),
        tombstoned_at=_dt(row["tombstoned_at"]),
    )


def _to_entity(row: Any) -> Entity:
    return Entity(
        id=EntityId(str(row["id"])),
        name=str(row["name"]),
        kind=str(row["kind"]),
        aliases=tuple(json.loads(row["aliases"])),
        mention_count=int(row["mention_count"]),
        first_seen_at=_dt(row["first_seen_at"]),
        merged_into=EntityId(str(row["merged_into"])) if row["merged_into"] else None,
    )


def _link(
    connection: sqlite3.Connection,
    memory_id: MemoryId,
    kind: MemoryKind,
    entity: EntityId,
    role: str,
    now: str,
) -> None:
    connection.execute(
        "INSERT INTO memory_entity_link (memory_id, memory_kind, entity_id, role, created_at) "
        "VALUES (?,?,?,?,?) ON CONFLICT DO NOTHING",
        (memory_id, kind.value, entity, role, now),
    )


def _derive(
    connection: sqlite3.Connection,
    derived: MemoryId,
    derived_kind: MemoryKind,
    source: MemoryId,
    relation: DerivationRelation,
    now: str,
    source_kind: MemoryKind | None = None,
) -> None:
    connection.execute(
        "INSERT INTO memory_derivation (derived_id, derived_kind, source_id, source_kind, "
        "relation, created_at) VALUES (?,?,?,?,?,?) ON CONFLICT DO NOTHING",
        (
            derived,
            derived_kind.value,
            source,
            (source_kind or MemoryKind.EPISODE).value,
            relation.value,
            now,
        ),
    )
