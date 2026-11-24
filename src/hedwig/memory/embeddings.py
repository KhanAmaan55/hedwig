"""The semantic channel: recall by meaning (docs/06 §17.2, MEM-04).

Word matching cannot connect "Do I eat meat?" to "The user is vegetarian." Embeddings can. Each
live memory gets one vector from the configured local model, kept in `memory_embedding` inside
`hedwig.db` (ADR-0005), and a query's nearest neighbours by cosine distance become one more ranked
list for reciprocal rank fusion.

Written by events (a memory was stored or formed), repaired by a task (anything missing, stale,
or made by a different model). Nothing on the write path waits for the model, and a query never
fails because of it: without the model or `sqlite-vec`, the channel returns nothing and health
says why.
"""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Callable, Collection, Sequence
from typing import Any, Protocol

from hedwig.core.errors import HedwigError
from hedwig.core.logging import fields, get_logger
from hedwig.core.ports import Clock, Event
from hedwig.core.ports.registry import Health, HealthStatus
from hedwig.core.ports.scheduler import TaskContext, TaskResult
from hedwig.core.store import Database
from hedwig.core.types import MemoryId, MemoryKind
from hedwig.memory.retrieval import episode_text

logger = get_logger(__name__)

SAME_SUBJECT = 0.22
"""Cosine distance under which two beliefs are about the same thing, for nomic-embed-text.
Measured (docs/06 §20): paraphrases and conflicts 0.02-0.18; unrelated beliefs 0.265 and up."""

BATCH = 32
BACKFILL_LIMIT = 512


class Embedder(Protocol):
    async def embed(
        self, texts: Sequence[str], *, model: str | None = None
    ) -> Sequence[Sequence[float]]: ...


class EmbeddingIndex:
    def __init__(
        self,
        database: Database,
        embedder: Embedder,
        *,
        clock: Clock,
        model: str,
        query_prefix: str = "",
        document_prefix: str = "",
        enabled: bool = True,
    ) -> None:
        self._db = database
        self._embedder = embedder
        self._clock = clock
        self._model = model
        self._query_prefix = query_prefix
        self._document_prefix = document_prefix
        self._enabled = enabled
        self._problem = ""
        self._stopped = False

    async def start(self) -> None:
        self._stopped = False

    async def stop(self) -> None:
        """Stop taking events. Stopped before the gateway it depends on, so a memory stored
        during shutdown is not embedded against a closed client — and dead-lettered (found
        live). The repair task embeds it on the next start."""
        self._stopped = True

    @property
    def available(self) -> bool:
        if not self._enabled or not self._model:
            return False
        if self._db.vector_search is None:
            _ = self._db.connection  # the first connection decides
        return bool(self._db.vector_search)

    # -- writing ------------------------------------------------------------------

    async def handle(self, event: Event) -> None:
        """`memory.episode.stored` / `memory.belief.formed`: embed the new memory now."""
        memory_id = event.payload.get("memory_id")
        if not memory_id or not self.available or self._stopped:
            return
        try:
            await self.index([MemoryId(str(memory_id))])
        except Exception:
            if self.stopping():  # shut down mid-call: the repair task embeds it next start
                return
            raise

    def stopping(self) -> bool:
        """Read fresh: `stop()` may have run while a handler awaited the model."""
        return self._stopped

    async def task(self, context: TaskContext) -> TaskResult:
        return TaskResult(stats={"embedded": await self.backfill()})

    async def backfill(self, *, limit: int = BACKFILL_LIMIT) -> int:
        """Embed every live memory whose vector is missing, stale, or from another model."""
        if not self.available:
            return 0
        pending = self._pending(limit=limit)
        return await self._embed(pending)

    async def index(self, ids: Collection[MemoryId]) -> int:
        if not self.available or not ids:
            return 0
        return await self._embed(self._pending(limit=None, ids=sorted(set(ids))))

    def _pending(
        self, *, limit: int | None, ids: Sequence[MemoryId] = ()
    ) -> list[tuple[MemoryId, str, str]]:
        only = f"AND {{t}}.id IN ({','.join('?' * len(ids))})" if ids else ""
        rows = self._db.query(
            "SELECT e.id, 'episode' AS kind, e.title, e.content, m.model, m.text_hash "
            "FROM episode e LEFT JOIN memory_embedding m ON m.memory_id = e.id "
            f"WHERE e.tombstoned_at IS NULL {only.format(t='e')} "
            "UNION ALL "
            "SELECT b.id, 'belief', NULL, b.statement, m.model, m.text_hash "
            "FROM belief b LEFT JOIN memory_embedding m ON m.memory_id = b.id "
            "WHERE b.tombstoned_at IS NULL AND b.status IN ('tentative','active') "
            f"{only.format(t='b')}",
            (*ids, *ids),
        )
        pending: list[tuple[MemoryId, str, str]] = []
        for row in rows:
            text = (
                episode_text(str(row["title"]), str(row["content"]))
                if row["kind"] == "episode"
                else str(row["content"])
            )
            if row["model"] == self._model and row["text_hash"] == _hash(text):
                continue
            pending.append((MemoryId(str(row["id"])), str(row["kind"]), text))
            if limit is not None and len(pending) >= limit:
                break
        if not ids:  # document passages (docs/13 §14): immutable, so only missing or other-model
            for row in self._db.query(
                "SELECT c.id, c.heading_path, c.text FROM document_chunk c "
                "JOIN document d ON d.id = c.document_id "
                "LEFT JOIN document_embedding v ON v.chunk_id = c.id AND v.model = ? "
                "WHERE d.removed_at IS NULL AND v.chunk_id IS NULL",
                (self._model,),
            ):
                if limit is not None and len(pending) >= limit:
                    break
                pending.append(
                    (MemoryId(str(row["id"])), "document", f"{row['heading_path']}: {row['text']}")
                )
        return pending

    async def _embed(self, pending: list[tuple[MemoryId, str, str]]) -> int:
        done = 0
        for start in range(0, len(pending), BATCH):
            batch = pending[start : start + BATCH]
            vectors = await self._vectors([self._document_prefix + text for _, _, text in batch])
            if vectors is None:
                break  # the model is unavailable: the task tries again later
            now = self._clock.now().isoformat()
            with self._db.transaction() as tx:
                for (memory_id, kind, text), vector in zip(batch, vectors, strict=True):
                    if kind == "document":
                        tx.execute(
                            "INSERT OR REPLACE INTO document_embedding "
                            "(chunk_id, model, dims, vector, created_at) VALUES (?,?,?,?,?)",
                            (memory_id, self._model, len(vector), _blob(vector), now),
                        )
                        continue
                    tx.execute(
                        "INSERT OR REPLACE INTO memory_embedding "
                        "(memory_id, memory_kind, model, dims, vector, text_hash, created_at) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (
                            memory_id,
                            kind,
                            self._model,
                            len(vector),
                            _blob(vector),
                            _hash(text),
                            now,
                        ),
                    )
            done += len(batch)
        if done:
            logger.debug("memories embedded", extra=fields(count=done, model=self._model))
        return done

    # -- recalling ------------------------------------------------------------------

    async def nearest(
        self, queries: Sequence[str], *, kinds: Collection[MemoryKind], limit: int
    ) -> list[MemoryId]:
        """The memories closest in meaning to any of the queries, nearest first."""
        if not self.available or not queries:
            return []
        vectors = await self._vectors([self._query_prefix + q for q in queries])
        if not vectors:
            return []
        allowed = [k.value for k in kinds if k in (MemoryKind.EPISODE, MemoryKind.BELIEF)]
        documents = MemoryKind.DOCUMENT in kinds
        if not allowed and not documents:
            return []
        best: dict[MemoryId, float] = {}
        marks = ",".join("?" * len(allowed)) or "NULL"
        for vector in vectors:
            blob = _blob(vector)
            # Memories and the user's documents are ranked together, so "near" is judged
            # against everything HEDWIG could recall, not against each store alone.
            rows = self._db.query(
                "SELECT m.memory_id, vec_distance_cosine(m.vector, ?) AS distance "
                "FROM memory_embedding m "
                "LEFT JOIN episode e ON m.memory_kind = 'episode' AND e.id = m.memory_id "
                "LEFT JOIN belief b ON m.memory_kind = 'belief' AND b.id = m.memory_id "
                f"WHERE m.model = ? AND m.dims = ? AND m.memory_kind IN ({marks}) AND ("
                "(e.id IS NOT NULL AND e.tombstoned_at IS NULL) OR "
                "(b.id IS NOT NULL AND b.tombstoned_at IS NULL "
                "AND b.status IN ('tentative','active'))) "
                "UNION ALL "
                "SELECT v.chunk_id, vec_distance_cosine(v.vector, ?) FROM document_embedding v "
                "JOIN document_chunk c ON c.id = v.chunk_id "
                "JOIN document d ON d.id = c.document_id "
                "WHERE ? AND v.model = ? AND v.dims = ? AND d.removed_at IS NULL "
                "ORDER BY distance",
                (
                    blob,
                    self._model,
                    len(vector),
                    *allowed,
                    blob,
                    int(documents),
                    self._model,
                    len(vector),
                ),
            )
            for memory_id, distance in _close(
                [(MemoryId(str(r["memory_id"])), float(r["distance"])) for r in rows], limit
            ):
                best[memory_id] = min(best.get(memory_id, 2.0), distance)
        return sorted(best, key=lambda key: best[key])[:limit]

    def close_beliefs(
        self,
        *,
        max_distance: float = SAME_SUBJECT,
        limit: int | None = None,
        skip: Callable[[MemoryId, MemoryId], bool] | None = None,
    ) -> list[tuple[MemoryId, MemoryId, float]]:
        """Pairs of live beliefs close in meaning, closest first (docs/06 §20).

        From the stored vectors, no model call: what the nightly judge should look at when two
        beliefs share no words — "vegetarian" and "does not eat meat". Rows are read one at a
        time; pairs the caller has settled (`skip`) are passed over *before* `limit` counts, so
        a night's batch is always new pairs and later ones are reached once earlier ones settle.
        """
        if not self.available:
            return []
        cursor = self._db.connection.execute(
            "SELECT a.memory_id AS first, b.memory_id AS second, "
            "vec_distance_cosine(a.vector, b.vector) AS distance "
            "FROM memory_embedding a JOIN memory_embedding b "
            "ON a.memory_id < b.memory_id AND b.model = a.model AND b.dims = a.dims "
            "JOIN belief x ON x.id = a.memory_id JOIN belief y ON y.id = b.memory_id "
            "WHERE a.model = ? AND a.memory_kind = 'belief' AND b.memory_kind = 'belief' "
            "AND x.tombstoned_at IS NULL AND y.tombstoned_at IS NULL "
            "AND x.status IN ('tentative','active') AND y.status IN ('tentative','active') "
            "AND vec_distance_cosine(a.vector, b.vector) <= ? "
            "ORDER BY distance",
            (self._model, max_distance),
        )
        found: list[tuple[MemoryId, MemoryId, float]] = []
        try:
            for row in cursor:
                first, second = MemoryId(str(row["first"])), MemoryId(str(row["second"]))
                if skip is not None and skip(first, second):
                    continue
                found.append((first, second, float(row["distance"])))
                if limit is not None and len(found) >= limit:
                    break
        finally:
            cursor.close()
        return found

    async def _vectors(self, texts: list[str]) -> Sequence[Sequence[float]] | None:
        try:
            vectors = await self._embedder.embed(texts, model=self._model)
        except HedwigError as error:
            if self._stopped:
                return None
            if error.message != self._problem:
                logger.warning(
                    "embedding model unavailable",
                    extra=fields(model=self._model, error=error.message),
                )
            self._problem = error.message
            return None
        self._problem = ""
        return vectors

    # -- health -----------------------------------------------------------------------

    async def health(self) -> Health:
        if not self._enabled:
            return Health(HealthStatus.OK, {"semantic_recall": "off (memory.semantic_recall)"})
        if not self.available:
            return Health(
                HealthStatus.DEGRADED,
                {"semantic_recall": "off"},
                message=self._db.vector_problem or "no embedding model configured",
            )
        row = self._db.query_one(
            "SELECT (SELECT COUNT(*) FROM memory_embedding WHERE model = ?) AS embedded",
            (self._model,),
        )
        detail: dict[str, Any] = {
            "semantic_recall": "on",
            "model": self._model,
            "embedded": int(row["embedded"]) if row else 0,
        }
        if self._problem:
            return Health(HealthStatus.DEGRADED, detail, message=self._problem)
        return Health(HealthStatus.OK, detail)


def _close(ranked: list[tuple[MemoryId, float]], limit: int) -> list[tuple[MemoryId, float]]:
    """Only the neighbours that are actually near (docs/06 §17.2).

    Every memory has a nearest neighbour; nominating the top thirty of a small store gave
    every memory semantic credit, and fusion then rewarded being nominated over being close.
    Kept: closer than halfway from the best match to the median. Relative, so it does not
    depend on one model's distance scale.
    """
    if not ranked:
        return []
    best, median = ranked[0][1], ranked[len(ranked) // 2][1]
    cutoff = best + (median - best) / 2
    return [(memory_id, d) for memory_id, d in ranked if d <= cutoff][:limit]


def _blob(vector: Sequence[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]
