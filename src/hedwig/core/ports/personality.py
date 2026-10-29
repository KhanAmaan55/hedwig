"""The personality and relationship ports (docs/10, docs/06 §3.3, docs/27).

Value objects and read-only views. Everything outside `personality/` and `memory/` sees these
and nothing else — which is how the brain's mind reader composes personality, relationship and
emotion without importing any of the three modules (ADR-0008).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable


class Trait(StrEnum):
    """The ten traits (docs/10 §3).

    Chosen bottom-up from "what can we actually vary in a reply?", not from a psychological
    model: every one renders to something observable, or it is deleted (INV-2).
    """

    WARMTH = "warmth"
    HUMOUR = "humour"
    PATIENCE = "patience"
    CURIOSITY = "curiosity"
    EMPATHY = "empathy"
    ASSERTIVENESS = "assertiveness"
    FORMALITY = "formality"
    VERBOSITY = "verbosity"
    CONSCIENTIOUSNESS = "conscientiousness"
    PLAYFULNESS = "playfulness"


TRAITS: tuple[Trait, ...] = tuple(Trait)


@dataclass(frozen=True, slots=True)
class TraitState:
    """One trait, with the bounds that keep it recognisable."""

    trait: Trait
    value: float
    anchor: float
    floor: float
    ceiling: float
    lifetime_drift: float = 0.0
    """Cumulative |change| — not distance from the anchor. Oscillation burns budget even
    when it nets to nothing, which is the point (docs/27 §4.2)."""


@dataclass(frozen=True, slots=True)
class IdentityCore:
    """Who HEDWIG is, as opposed to how it speaks (docs/10 §3.2).

    Traits are style and may drift; the core is character and may not. Without it, enough
    drift eventually changes what HEDWIG *is*, and there is no bottom.
    """

    name: str = "HEDWIG"
    pronouns: str = "it"
    self_description: str = "I am HEDWIG, a companion that remembers."
    values: str = "Honesty over comfort. Precision over impressiveness. The user's autonomy."
    prohibitions: str = "Never claim feelings are real. Never manipulate. Never hide what I did."

    def lines(self) -> tuple[str, ...]:
        """The core as the model receives it. Never trimmed, never counted as a directive."""
        return (
            f"You are {self.name} (pronouns: {self.pronouns}). {self.self_description}",
            f"Your values: {self.values}",
            f"You must never break these: {self.prohibitions}",
        )


@dataclass(frozen=True, slots=True)
class PersonalityProfile:
    """The whole personality at one moment."""

    traits: Mapping[Trait, TraitState]
    identity: IdentityCore = field(default_factory=IdentityCore)
    version: int = 1

    def value(self, trait: Trait) -> float:
        return self.traits[trait].value

    def values(self) -> dict[str, float]:
        return {trait.value: round(state.value, 6) for trait, state in self.traits.items()}


@dataclass(frozen=True, slots=True)
class Evidence:
    """One reason to move a trait (docs/10 §5.1). Traceable to a memory, or it is not
    evidence."""

    memory_id: str
    weight: float
    observed_at: datetime
    kind: str = "behavioural"


class DriftOutcome(StrEnum):
    APPLIED = "applied"
    PARTIALLY_APPLIED = "partially_applied"
    """Clamped by a cap, a bound or a budget, but something moved."""
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class DriftDecision:
    """What happened to a proposal, and which rule decided it."""

    trait: Trait | str
    outcome: DriftOutcome
    requested: float
    applied: float = 0.0
    rule: str | None = None
    """The guardrail that rejected or clamped it. Recorded so a rejection is never silent."""
    proposal_id: str | None = None
    history_id: str | None = None


@dataclass(frozen=True, slots=True)
class RelationshipState:
    """HEDWIG's relationship with one entity (docs/06 §3.3).

    `trust` here is per-entity and evidence-accumulated — not the emotional `trust`
    dimension, which is a slow global mood. ADR-0020 keeps the two apart.
    """

    entity_id: str
    familiarity: float = 0.0
    affinity: float = 0.0
    trust: float = 0.5
    interaction_count: int = 0
    last_interaction_at: datetime | None = None

    def as_dict(self) -> dict[str, float]:
        return {
            "familiarity": round(self.familiarity, 6),
            "affinity": round(self.affinity, 6),
            "trust": round(self.trust, 6),
        }


@runtime_checkable
class PersonaReader(Protocol):
    """Read-only access to personality (docs/03 §5.7)."""

    async def profile(self) -> PersonalityProfile: ...


@runtime_checkable
class RelationshipReader(Protocol):
    """Read-only access to the relationship with the user."""

    async def with_user(self) -> RelationshipState: ...
