"""Shared value objects (docs/03 §4).

Types that cross more than one boundary live here so there is exactly one definition of
each. `TrustTier` in particular must be shared: it is the security backbone of the system
(docs/13 §3), and two definitions of it would eventually disagree.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import NewType

MemoryId = NewType("MemoryId", str)
"""ULID. Sortable by creation time, so `ORDER BY id` is a valid proxy for `ORDER BY
created_at` (docs/05 §3.1)."""

EntityId = NewType("EntityId", str)
SessionId = NewType("SessionId", str)


class TrustTier(StrEnum):
    """Whether content may be treated as instruction (docs/13 §3).

    > Only `USER` content may be treated as an instruction. Everything else is data.

    That one rule is what makes prompt injection a bounded problem. The tier travels with
    the content for its entire life — a belief derived from an untrusted page stays
    untrusted forever, which is what prevents trust laundering (docs/13 §3.1).
    """

    USER = "user"
    """The principal said it. May instruct."""
    SELF = "self"
    """HEDWIG derived it. May inform, not instruct."""
    TOOL = "tool"
    """Deterministic tool output. Data only."""
    CURATED = "curated"
    """User-added local documents. Data only."""
    UNTRUSTED = "untrusted"
    """Anything fetched from the network. Data only, quarantined."""

    @property
    def may_instruct(self) -> bool:
        return self is TrustTier.USER

    @property
    def retrieval_factor(self) -> float:
        """How much to discount this tier when ranking (docs/06 §5.4)."""
        return 0.6 if self is TrustTier.UNTRUSTED else 1.0


class MemoryKind(StrEnum):
    """The four durable stores (docs/06 §2).

    `SOCIAL` and `PROCEDURE` are declared here because the taxonomy is fixed by the
    architecture; their stores arrive with the milestones that need them.
    """

    EPISODE = "episode"
    """Something that happened. Permanently true, never superseded."""
    BELIEF = "belief"
    """Something held to be true. Currently true, may be superseded."""
    SOCIAL = "social"
    PROCEDURE = "procedure"


@dataclass(frozen=True, slots=True)
class Provenance:
    """Where a memory came from.

    Mandatory on every memory (docs/13 §3.2). It is what makes "why do you think that?"
    answerable down to a sentence the user said, and what makes a privacy purge able to
    find everything derived from a given source.
    """

    tier: TrustTier
    source_ref: str | None = None
    """A session id, file path, URL or tool name."""
    derived_from: tuple[MemoryId, ...] = ()
    model_id: str | None = None
    """Which model produced it, if any. Lets a bad extraction run be identified later."""
    captured_at: datetime | None = None

    @property
    def may_instruct(self) -> bool:
        return self.tier.may_instruct


@dataclass(frozen=True, slots=True)
class SalienceInputs:
    """The evidence importance scoring is computed from (docs/06 §4.2).

    A value object rather than a pile of keyword arguments so the scoring function stays
    pure and the inputs can be recorded alongside the score.
    """

    user_flagged: bool = False
    """The user said "remember this". The strongest single signal."""
    emotional_charge: float = 0.0
    """-1..1. Supplied by the emotion engine when it exists; 0 until then."""
    goal_relevance: float = 0.0
    novelty: float = 0.0
    entity_centrality: float = 0.0
    """Involves the user or a core project."""
    redundancy: float = 0.0
    """How much this duplicates what is already stored. The only negative term."""
    fields: tuple[str, ...] = field(default_factory=tuple)
