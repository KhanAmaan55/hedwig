"""Hybrid retrieval (docs/06 §5).

The highest-leverage component in the system: **a perfect memory store with mediocre
retrieval behaves exactly like amnesia.**

The pipeline, in order:

    channels → fusion (RRF) → rescoring → diversification (MMR) → packing

Each stage exists because of a specific failure:

* **Multiple channels**, because one query against one index is keyword search, not
  retrieval. Lexical finds exact words, structural finds things linked to the same entity,
  temporal finds what is simply recent.
* **Reciprocal rank fusion**, not score normalisation. BM25 scores and cosine similarities
  are not commensurable, and normalising them is guesswork that quietly changes as the
  corpus grows. RRF only needs ranks (docs/06 §5.3).
* **Rescoring** by salience, confidence, recency and trust, so an important memory beats a
  merely word-matching one.
* **MMR**, because the top ten from a single channel are often ten paraphrases of one
  memory.
* **Packing** with per-kind quotas and *chronological* output, because a chronological
  context reads to a model as a coherent history while a score-ordered one reads as a pile
  of fragments (docs/06 §5.5).

The semantic (embedding) channel is specified in docs/06 §5 and is **not implemented here**
— see docs/06 §13.2. Its weight is carried in the policy and the fusion is written to accept
it, so switching it on is a channel registration rather than a rewrite.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from hedwig.core.logging import fields, get_logger
from hedwig.core.ports import Clock
from hedwig.core.ports.memory import (
    RetrievalPolicy,
    RetrievedItem,
    WorkingSet,
)
from hedwig.core.store import Database
from hedwig.core.types import MemoryId, MemoryKind, Provenance, TrustTier

logger = get_logger(__name__)

RRF_K = 60.0
"""The constant from the reciprocal-rank-fusion literature. Large enough that the
difference between rank 1 and rank 2 does not dwarf everything below it."""

CHARS_PER_TOKEN = 4

KIND_QUOTAS: dict[MemoryKind, float] = {
    # No kind may crowd out the others (docs/06 §5.5).
    MemoryKind.EPISODE: 0.65,
    MemoryKind.BELIEF: 0.45,
}

_WORD = re.compile(r"[0-9A-Za-z_]+")


@dataclass(frozen=True, slots=True)
class Candidate:
    """A memory found by at least one channel, before fusion."""

    memory_id: MemoryId
    kind: MemoryKind
    text: str
    occurred_at: datetime
    salience: float
    confidence: float
    base_importance: float
    trust: TrustTier
    source_ref: str | None


class HybridRetrieval:
    """The `RetrievalEngine` implementation."""

    def __init__(self, database: Database, *, clock: Clock) -> None:
        self._db = database
        self._clock = clock

    async def search(
        self, queries: Sequence[str], *, policy: RetrievalPolicy | None = None
    ) -> WorkingSet:
        resolved = policy or RetrievalPolicy()
        cleaned = [query.strip() for query in queries if query and query.strip()]
        if not cleaned:
            return WorkingSet(queries=())

        # 1. Channels. Each returns a ranked list; none of them knows about the others.
        channels: dict[str, list[MemoryId]] = {
            "lexical": self._lexical(cleaned, resolved),
            "structural": self._structural(resolved),
            "temporal": self._temporal(resolved),
        }
        weights = {
            "lexical": resolved.lexical_weight,
            "structural": resolved.structural_weight,
            "temporal": resolved.temporal_weight,
        }

        universe = {memory_id for ranked in channels.values() for memory_id in ranked}
        if not universe:
            return WorkingSet(queries=tuple(cleaned))

        candidates = self._hydrate(universe, resolved)

        # 2. Fusion, then 3. rescoring.
        fused = _fuse(channels, weights)
        scored = [
            self._rescore(candidate, fused.get(candidate.memory_id, 0.0), resolved)
            for candidate in candidates.values()
        ]
        scored.sort(key=lambda item: item.score, reverse=True)

        # 4. Diversification, then 5. packing.
        diversified = _mmr(scored, lambda_diversity=resolved.diversity, limit=resolved.max_items)
        packed, dropped = _pack(diversified, budget=resolved.token_budget)

        logger.debug(
            "working set assembled",
            extra=fields(
                queries=len(cleaned),
                candidates=len(candidates),
                packed=len(packed),
                dropped=dropped,
            ),
        )
        return WorkingSet(
            # Chronological, not by score: a context that reads as a history beats one that
            # reads as a pile of fragments.
            items=tuple(sorted(packed, key=lambda item: item.occurred_at)),
            queries=tuple(cleaned),
            token_count=sum(item.token_estimate for item in packed),
            dropped=dropped,
            candidates=len(candidates),
        )

    # -- channels ----------------------------------------------------------

    def _lexical(self, queries: Sequence[str], policy: RetrievalPolicy) -> list[MemoryId]:
        """FTS5 over episode titles/content and belief statements."""
        found: list[MemoryId] = []
        seen: set[MemoryId] = set()

        for query in queries:
            match = _to_match(query)
            if not match:
                continue

            if MemoryKind.EPISODE in policy.include_kinds:
                for row in self._db.query(
                    "SELECT e.id FROM episode_fts f JOIN episode e ON e.rowid = f.rowid "
                    "WHERE episode_fts MATCH ? AND e.tombstoned_at IS NULL "
                    "ORDER BY bm25(episode_fts) LIMIT ?",
                    (match, policy.candidates_per_channel),
                ):
                    _push(found, seen, MemoryId(str(row["id"])))

            if MemoryKind.BELIEF in policy.include_kinds:
                for row in self._db.query(
                    "SELECT b.id FROM belief_fts f JOIN belief b ON b.rowid = f.rowid "
                    "WHERE belief_fts MATCH ? AND b.tombstoned_at IS NULL "
                    "AND b.status IN ('tentative','active') "
                    "ORDER BY bm25(belief_fts) LIMIT ?",
                    (match, policy.candidates_per_channel),
                ):
                    _push(found, seen, MemoryId(str(row["id"])))

        return found

    def _structural(self, policy: RetrievalPolicy) -> list[MemoryId]:
        """Memories linked to the entities this turn is about."""
        if not policy.entity_focus:
            return []

        placeholders = ",".join("?" * len(policy.entity_focus))
        rows = self._db.query(
            f"SELECT memory_id FROM memory_entity_link "
            f"WHERE entity_id IN ({placeholders}) "
            "ORDER BY weight DESC LIMIT ?",
            (*policy.entity_focus, policy.candidates_per_channel),
        )
        return [MemoryId(str(row["memory_id"])) for row in rows]

    def _temporal(self, policy: RetrievalPolicy) -> list[MemoryId]:
        """What is simply recent.

        Present as its own channel because "what were we just doing?" is a real and common
        question that no amount of word matching answers.
        """
        if MemoryKind.EPISODE not in policy.include_kinds:
            return []
        rows = self._db.query(
            "SELECT id FROM episode WHERE tombstoned_at IS NULL ORDER BY occurred_at DESC LIMIT ?",
            (min(10, policy.candidates_per_channel),),
        )
        return [MemoryId(str(row["id"])) for row in rows]

    # -- hydration and scoring --------------------------------------------

    def _hydrate(self, ids: set[MemoryId], policy: RetrievalPolicy) -> dict[MemoryId, Candidate]:
        if not ids:
            return {}
        placeholders = ",".join("?" * len(ids))
        ordered = list(ids)
        candidates: dict[MemoryId, Candidate] = {}

        for row in self._db.query(
            f"SELECT id, title, content, occurred_at, salience, confidence, "
            f"base_importance, trust_tier, source_ref FROM episode "
            f"WHERE id IN ({placeholders}) AND tombstoned_at IS NULL",
            ordered,
        ):
            memory_id = MemoryId(str(row["id"]))
            candidates[memory_id] = Candidate(
                memory_id=memory_id,
                kind=MemoryKind.EPISODE,
                text=f"{row['title']}: {row['content']}",
                occurred_at=datetime.fromisoformat(row["occurred_at"]),
                salience=float(row["salience"]),
                confidence=float(row["confidence"]),
                base_importance=float(row["base_importance"]),
                trust=TrustTier(row["trust_tier"]),
                source_ref=row["source_ref"],
            )

        for row in self._db.query(
            f"SELECT id, statement, valid_from, salience, confidence, base_importance, "
            f"trust_tier, source_ref FROM belief "
            f"WHERE id IN ({placeholders}) AND tombstoned_at IS NULL "
            "AND status IN ('tentative','active')",
            ordered,
        ):
            memory_id = MemoryId(str(row["id"]))
            candidates[memory_id] = Candidate(
                memory_id=memory_id,
                kind=MemoryKind.BELIEF,
                text=str(row["statement"]),
                occurred_at=datetime.fromisoformat(row["valid_from"]),
                salience=float(row["salience"]),
                confidence=float(row["confidence"]),
                base_importance=float(row["base_importance"]),
                trust=TrustTier(row["trust_tier"]),
                source_ref=row["source_ref"],
            )

        return {
            memory_id: candidate
            for memory_id, candidate in candidates.items()
            if candidate.confidence >= policy.min_confidence
        }

    def _rescore(
        self, candidate: Candidate, fused: float, policy: RetrievalPolicy
    ) -> RetrievedItem:
        """docs/06 §5.4, with every factor recorded so the ranking can be explained."""
        age_days = max(0.0, (self._clock.now() - candidate.occurred_at).total_seconds() / 86_400)
        recency = 1.0 + 0.3 * math.exp(-age_days / 14.0) * policy.recency_weight
        salience = 0.5 + 0.5 * candidate.salience * policy.salience_weight
        confidence = math.sqrt(max(0.0, candidate.confidence))
        trust = candidate.trust.retrieval_factor

        score = fused * salience * confidence * recency * trust
        return RetrievedItem(
            memory_id=candidate.memory_id,
            kind=candidate.kind,
            text=candidate.text,
            score=score,
            occurred_at=candidate.occurred_at,
            provenance=Provenance(tier=candidate.trust, source_ref=candidate.source_ref),
            confidence=candidate.confidence,
            token_estimate=max(1, len(candidate.text) // CHARS_PER_TOKEN),
            score_breakdown={
                "fused": round(fused, 5),
                "salience": round(salience, 4),
                "confidence": round(confidence, 4),
                "recency": round(recency, 4),
                "trust": trust,
                "age_days": round(age_days, 2),
            },
        )


# -- pure pipeline stages --------------------------------------------------


def _fuse(channels: dict[str, list[MemoryId]], weights: dict[str, float]) -> dict[MemoryId, float]:
    """Reciprocal rank fusion (docs/06 §5.3).

        score(m) = Σ  w_channel / (RRF_K + rank_channel(m))

    Only ranks are used, so the channels never have to agree about what a score means.
    """
    fused: dict[MemoryId, float] = {}
    for name, ranked in channels.items():
        weight = weights.get(name, 1.0)
        if weight <= 0:
            continue
        for rank, memory_id in enumerate(ranked, start=1):
            fused[memory_id] = fused.get(memory_id, 0.0) + weight / (RRF_K + rank)
    return fused


def _tokens(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", text.lower()) if len(word) > 2}


def _similarity(left: RetrievedItem, right: RetrievedItem) -> float:
    """Jaccard overlap on words.

    A stand-in for cosine similarity until the embedding channel exists. Crude, but it
    catches the case MMR is actually for — two near-identical summaries of the same event.
    """
    a, b = _tokens(left.text), _tokens(right.text)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _mmr(
    items: Sequence[RetrievedItem], *, lambda_diversity: float, limit: int
) -> list[RetrievedItem]:
    """Maximal marginal relevance (docs/06 §5.5).

    Prevents the top-N being N paraphrases of one memory, which is a real and common
    failure of any single-channel ranking over a corpus containing repeated summaries.
    """
    if not items:
        return []
    diversity = max(0.0, min(1.0, lambda_diversity))
    if diversity == 0.0:
        return list(items[:limit])

    selected: list[RetrievedItem] = [items[0]]
    remaining = list(items[1:])

    while remaining and len(selected) < limit:
        best_index, best_value = 0, -math.inf
        for index, candidate in enumerate(remaining):
            penalty = max(_similarity(candidate, chosen) for chosen in selected)
            value = (1 - diversity) * candidate.score - diversity * penalty
            if value > best_value:
                best_index, best_value = index, value
        selected.append(remaining.pop(best_index))

    return selected


def _pack(items: Sequence[RetrievedItem], *, budget: int) -> tuple[list[RetrievedItem], int]:
    """Fit items into the token budget, respecting per-kind quotas (docs/06 §5.5)."""
    packed: list[RetrievedItem] = []
    used = 0
    dropped = 0
    per_kind: dict[MemoryKind, int] = dict.fromkeys(KIND_QUOTAS, 0)

    for item in items:
        quota = KIND_QUOTAS.get(item.kind, 1.0)
        kind_ceiling = max(1, int(budget * quota))

        if used + item.token_estimate > budget:
            dropped += 1
            continue
        if per_kind.get(item.kind, 0) + item.token_estimate > kind_ceiling:
            dropped += 1
            continue

        packed.append(item)
        used += item.token_estimate
        per_kind[item.kind] = per_kind.get(item.kind, 0) + item.token_estimate

    return packed, dropped


def _push(found: list[MemoryId], seen: set[MemoryId], memory_id: MemoryId) -> None:
    if memory_id not in seen:
        seen.add(memory_id)
        found.append(memory_id)


def _to_match(query: str) -> str:
    """Turn free text into a safe FTS5 MATCH expression.

    User text reaches this directly, and FTS5's query language would otherwise treat
    punctuation as syntax — a quote or a bare `-` is a syntax error, not a search.
    """
    # Extract words rather than blocklisting punctuation: FTS5 treats a surprising amount
    # of punctuation as syntax, and an allowlist cannot be incomplete.
    words = [word for word in _WORD.findall(query) if len(word) > 1]
    if not words:
        return ""
    # OR rather than AND: recall matters more than precision at the candidate stage, and
    # fusion plus rescoring is what sorts the result out.
    return " OR ".join(f'"{word}"' for word in words[:12])
