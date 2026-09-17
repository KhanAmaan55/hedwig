"""The memory ports (docs/03 §5.1-5.2, docs/06).

Two ports, and the split between them matters: `MemoryStore` is addressed **by identity** —
write this, fetch that, reinforce these — while `RetrievalEngine` is addressed **by
situation**. Retrieval is where nearly all the difficulty lives, and keeping it separate is
what lets it be replaced (a learned reranker, a graph expansion) without touching the write
path.

Two things the port shapes enforce rather than merely encourage:

* **`tombstone`/`restore`, never `delete`.** Forgetting is reversible by construction
  (docs/06 §7.3). Hard deletion exists only in the privacy purge, which is a separate,
  explicitly-invoked operation (docs/21 §7).
* **`RetrievalPolicy` is the only channel** by which cognition influences retrieval. The
  knowledge layer never learns what an emotion is; it receives weights (docs/02 §3).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from hedwig.core.types import EntityId, MemoryId, MemoryKind, Provenance, SessionId, TrustTier


class EpisodeKind(StrEnum):
    """What sort of thing happened (docs/06 §3.1)."""

    INTERACTION = "interaction"
    OBSERVATION = "observation"
    REFLECTION = "reflection"
    SESSION_SUMMARY = "session_summary"
    PERIOD_SUMMARY = "period_summary"
    SELF_ACTION = "self_action"
    MILESTONE = "milestone"


class BeliefStatus(StrEnum):
    """Belief lifecycle (docs/06 §3.2).

    `TENTATIVE` is the guard against the most dangerous failure a memory system has:
    confabulation hardening into fact. An extraction from one ambiguous sentence enters
    here and is never presented as certain.
    """

    TENTATIVE = "tentative"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    RETRACTED = "retracted"


@dataclass(frozen=True, slots=True)
class Episode:
    """A durable record of something that happened. The unit of episodic memory."""

    id: MemoryId
    occurred_at: datetime
    title: str
    content: str
    kind: EpisodeKind = EpisodeKind.INTERACTION
    ended_at: datetime | None = None
    session_id: SessionId | None = None
    entities: tuple[EntityId, ...] = ()
    salience: float = 0.5
    base_importance: float = 0.5
    """Frozen at capture, never changed. Without it, an important memory nobody has touched
    for a year is indistinguishable from noise (docs/05 §5.2)."""
    emotional_charge: float = 0.0
    confidence: float = 1.0
    access_count: int = 0
    last_accessed_at: datetime | None = None
    pinned: bool = False
    """The user said never forget this. A column, not a convention (docs/05 §5.2)."""
    provenance: Provenance = field(default_factory=lambda: Provenance(tier=TrustTier.SELF))
    tombstoned_at: datetime | None = None

    @property
    def kind_of_memory(self) -> MemoryKind:
        return MemoryKind.EPISODE


@dataclass(frozen=True, slots=True)
class Belief:
    """A proposition HEDWIG holds. The unit of semantic memory.

    Natural language is the primary representation; the subject/predicate/object fields are
    opportunistic structure used only when extraction is confident. Building a real
    ontology is a multi-year project that fails long before it helps (docs/06 §3.2).
    """

    id: MemoryId
    statement: str
    valid_from: datetime
    status: BeliefStatus = BeliefStatus.TENTATIVE
    subject_id: EntityId | None = None
    predicate: str | None = None
    object_text: str | None = None
    confidence: float = 0.5
    salience: float = 0.5
    base_importance: float = 0.5
    access_count: int = 0
    last_accessed_at: datetime | None = None
    pinned: bool = False
    valid_to: datetime | None = None
    superseded_by: MemoryId | None = None
    provenance: Provenance = field(default_factory=lambda: Provenance(tier=TrustTier.SELF))
    tombstoned_at: datetime | None = None

    @property
    def is_current(self) -> bool:
        return self.status in (BeliefStatus.TENTATIVE, BeliefStatus.ACTIVE)


@dataclass(frozen=True, slots=True)
class Entity:
    """A person, place, topic or project that memories refer to."""

    id: EntityId
    name: str
    kind: str = "topic"
    aliases: tuple[str, ...] = ()
    mention_count: int = 0
    first_seen_at: datetime | None = None
    merged_into: EntityId | None = None


class DerivationRelation(StrEnum):
    """How one memory came from another (docs/05 §5.2).

    The lineage this records is what makes "why do you believe that?" answerable, and what
    stops the sole justification for a belief being forgotten (docs/06 §7.3).
    """

    SUMMARISED_FROM = "summarised_from"
    EXTRACTED_FROM = "extracted_from"
    MERGED_FROM = "merged_from"
    CORROBORATED_BY = "corroborated_by"
    CONTRADICTED_BY = "contradicted_by"
    PROMOTED_FROM = "promoted_from"


class ForgetReason(StrEnum):
    DECAYED = "decayed"
    MERGED = "merged"
    CONTRADICTED = "contradicted"
    USER_REQUEST = "user_request"
    PRIVACY_PURGE = "privacy_purge"


@dataclass(frozen=True, slots=True)
class RetrievalPolicy:
    """The only way cognition influences retrieval (docs/03 §5.2).

    Emotion and personality produce these weights; the knowledge layer never learns what
    they mean. Defaults are the documented ones, so a caller with no cognitive state gets
    sensible behaviour rather than zeros.
    """

    token_budget: int = 3000
    lexical_weight: float = 1.0
    structural_weight: float = 0.8
    temporal_weight: float = 0.6
    semantic_weight: float = 1.0
    salience_weight: float = 1.0
    recency_weight: float = 1.0
    diversity: float = 0.3
    """MMR λ. 0 = most relevant, 1 = maximally diverse (docs/06 §5.5)."""
    include_kinds: frozenset[MemoryKind] = frozenset({MemoryKind.EPISODE, MemoryKind.BELIEF})
    entity_focus: tuple[EntityId, ...] = ()
    min_confidence: float = 0.0
    candidates_per_channel: int = 30
    max_items: int = 12


@dataclass(frozen=True, slots=True)
class RetrievedItem:
    """One item in a working set, with the arithmetic that put it there.

    `score_breakdown` exists so `/v1/explain` can show *why* something was recalled
    (docs/16 §6). A ranking nobody can explain is a ranking nobody can debug.
    """

    memory_id: MemoryId
    kind: MemoryKind
    text: str
    score: float
    occurred_at: datetime
    provenance: Provenance
    score_breakdown: Mapping[str, float] = field(default_factory=dict)
    confidence: float = 1.0
    token_estimate: int = 0


@dataclass(frozen=True, slots=True)
class WorkingSet:
    """Everything assembled for one turn. Ephemeral, never persisted as-is (docs/06 §2)."""

    items: tuple[RetrievedItem, ...] = ()
    queries: tuple[str, ...] = ()
    token_count: int = 0
    dropped: int = 0
    """Candidates the budget excluded. Counted so starvation is visible rather than silent."""
    candidates: int = 0


@runtime_checkable
class MemoryStore(Protocol):
    """Durable memory, addressed by identity."""

    # -- episodic ----------------------------------------------------------

    async def add_episode(self, episode: Episode) -> MemoryId: ...

    async def get_episode(self, memory_id: MemoryId) -> Episode | None: ...

    async def recent_episodes(
        self, *, limit: int = 20, before: datetime | None = None
    ) -> Sequence[Episode]: ...

    # -- semantic ----------------------------------------------------------

    async def add_belief(self, belief: Belief) -> MemoryId: ...

    async def get_belief(self, memory_id: MemoryId) -> Belief | None: ...

    async def supersede_belief(self, old: MemoryId, new: Belief) -> MemoryId:
        """Replace a belief without erasing it.

        Supersession, not mutation: it is what lets HEDWIG say "I used to think you were in
        Berlin", which is impossible with in-place updates (docs/06 §3.2).
        """
        ...

    async def promote_belief(self, memory_id: MemoryId, *, confidence: float) -> None:
        """Move a tentative belief to active, once corroborated or confirmed."""
        ...

    async def beliefs_about(
        self, entity: EntityId, *, include_historical: bool = False
    ) -> Sequence[Belief]: ...

    # -- entities ----------------------------------------------------------

    async def upsert_entity(self, entity: Entity) -> EntityId: ...

    async def resolve_entity(self, name: str) -> Entity | None: ...

    async def link(
        self, memory_id: MemoryId, entity: EntityId, *, role: str = "mentioned"
    ) -> None: ...

    # -- lineage -----------------------------------------------------------

    async def record_derivation(
        self, derived: MemoryId, source: MemoryId, relation: DerivationRelation
    ) -> None: ...

    async def sources_of(self, memory_id: MemoryId) -> Sequence[MemoryId]: ...

    # -- lifecycle ---------------------------------------------------------

    async def reinforce(self, ids: Sequence[MemoryId], amount: float, *, cause: str) -> None:
        """Raise salience because a memory proved useful (docs/06 §7.2)."""
        ...

    async def record_access(
        self, items: Sequence[RetrievedItem], *, used_in_reply: bool, turn_id: str | None
    ) -> None:
        """Note that these memories were surfaced. Drives reinforcement and decay."""
        ...

    async def set_salience(self, memory_id: MemoryId, salience: float) -> None: ...

    async def set_pinned(self, memory_id: MemoryId, pinned: bool) -> None: ...

    async def tombstone(self, memory_id: MemoryId, reason: ForgetReason) -> bool:
        """Forget, reversibly. Returns `False` if it was protected."""
        ...

    async def restore(self, memory_id: MemoryId) -> bool: ...

    async def iter_for_consolidation(
        self, *, since: datetime, kinds: frozenset[MemoryKind] | None = None
    ) -> AsyncIterator[Episode | Belief]: ...


@runtime_checkable
class RetrievalEngine(Protocol):
    """Memory, addressed by situation.

    A perfect memory store with mediocre retrieval behaves exactly like amnesia
    (docs/06 §5), which is why this is the highest-leverage component in the system.
    """

    async def search(
        self, queries: Sequence[str], *, policy: RetrievalPolicy | None = None
    ) -> WorkingSet: ...
