"""The emotion ports (docs/09, ADR-0019).

Six dimensions, a read-only view of them, and the behaviour parameters they bind to. The
brain never imports the emotion module; it reads `TurnPolicy`, and the adapter that turns
one into the other lives in `emotion/`.

`EmotionState` is a frozen value object with no methods that mutate. Every transition
returns a new one, which is what makes the update equation testable as arithmetic rather
than as a side effect (docs/09 §5.1).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class Dimension(StrEnum):
    """The state vector (docs/09 §3).

    Six, fixed by ADR-0019. `valence` and `arousal` are *derived* from these rather than
    stored beside them, so they cannot disagree with the state they summarise.
    """

    HAPPINESS = "happiness"
    TRUST = "trust"
    CURIOSITY = "curiosity"
    CONFIDENCE = "confidence"
    ENERGY = "energy"
    STRESS = "stress"


DIMENSIONS: tuple[Dimension, ...] = tuple(Dimension)


class AppraisalDimension(StrEnum):
    """How an event is read, before it touches the state (docs/09 §4.1).

    The interpretable layer: this is what the Mind Inspector shows and what a human tunes.
    Going straight from event to state deltas would work and would be unreadable.
    """

    NOVELTY = "novelty"
    GOAL_CONGRUENCE = "goal_congruence"
    CERTAINTY = "certainty"
    AGENCY = "agency"
    SOCIAL_VALENCE = "social_valence"
    EFFORT = "effort"
    NORM_FIT = "norm_fit"


@dataclass(frozen=True, slots=True)
class Appraisal:
    """One event, read as seven numbers.

    `significance` scales the whole appraisal when it is applied, so a rule can say "this
    is the same shape of event but it matters less" without restating every dimension.
    """

    novelty: float = 0.0
    goal_congruence: float = 0.0
    certainty: float = 0.0
    agency: float = 0.0
    social_valence: float = 0.0
    effort: float = 0.0
    norm_fit: float = 0.0
    significance: float = 1.0
    rationale: str = ""
    """One line, for the inspector. Never user text — a rule name and its cause."""

    @property
    def is_neutral(self) -> bool:
        """Whether this appraisal would change anything at all."""
        return self.significance == 0.0 or all(value == 0.0 for value in self.values().values())

    def values(self) -> dict[AppraisalDimension, float]:
        return {dimension: getattr(self, dimension.value) for dimension in AppraisalDimension}

    def scaled(self, factor: float) -> Appraisal:
        return replace(self, significance=self.significance * factor)


NEUTRAL = Appraisal(significance=0.0, rationale="no rule matched")
"""What an unmatched event produces. Not a state change (docs/09 §4.2)."""


@dataclass(frozen=True, slots=True)
class EmotionState:
    """Where HEDWIG is, right now.

    Defaults are the neutral midpoints, not zeros: a companion that boots with every drive
    at zero is not calm, it is switched off.
    """

    happiness: float = 0.55
    trust: float = 0.60
    curiosity: float = 0.55
    confidence: float = 0.60
    energy: float = 0.50
    stress: float = 0.15
    updated_at: datetime | None = None
    version: int = 1

    def __getitem__(self, dimension: Dimension) -> float:
        value: float = getattr(self, dimension.value)
        return value

    def values(self) -> dict[Dimension, float]:
        return {dimension: self[dimension] for dimension in DIMENSIONS}

    def with_dimension(self, dimension: Dimension, value: float) -> EmotionState:
        """One dimension changed, the rest untouched.

        Exists so callers that vary a single dimension — the inspector, and every test that
        asks "what does this one number do?" — do not have to build keyword arguments
        dynamically and lose type checking in the process.
        """
        values = self.values()
        values[dimension] = value
        return replace(
            self,
            happiness=values[Dimension.HAPPINESS],
            trust=values[Dimension.TRUST],
            curiosity=values[Dimension.CURIOSITY],
            confidence=values[Dimension.CONFIDENCE],
            energy=values[Dimension.ENERGY],
            stress=values[Dimension.STRESS],
        )

    def with_values(self, values: Mapping[Dimension, float], *, at: datetime) -> EmotionState:
        return replace(
            self,
            **{dimension.value: values[dimension] for dimension in values},
            updated_at=at,
            version=self.version + 1,
        )

    def distance(self, other: EmotionState) -> float:
        """L1 distance. What `min_publish_delta` is measured against (docs/09 §5.2)."""
        return sum(abs(self[dimension] - other[dimension]) for dimension in DIMENSIONS)

    # -- derived core affect (docs/09 §3.2) --------------------------------

    @property
    def valence(self) -> float:
        """Positive/negative tone, -1..1. Derived, never stored."""
        raw = (
            0.40 * self.happiness + 0.25 * self.trust + 0.20 * self.confidence - 0.35 * self.stress
        )
        # The positive terms sum to 0.85 and the negative reaches -0.35, so the raw range
        # is [-0.35, 0.85]. Rescale that onto -1..1 rather than clipping, which would flatten
        # the top of the range into a plateau.
        return round(max(-1.0, min(1.0, (raw + 0.35) / 0.60 - 1.0)), 6)

    @property
    def arousal(self) -> float:
        """Activation, 0…1. Derived, never stored."""
        return round(
            max(0.0, min(1.0, 0.45 * self.energy + 0.35 * self.stress + 0.20 * self.curiosity)),
            6,
        )

    def as_dict(self) -> dict[str, float]:
        return {dimension.value: round(self[dimension], 6) for dimension in DIMENSIONS}


@dataclass(frozen=True, slots=True)
class BehaviourParameters:
    """What the state means for behaviour (docs/09 §6).

    Kept separate from `TurnPolicy` because not every binding is a turn parameter:
    `admits_background_work` is read by the scheduler's governor, which has no turn.
    """

    diversity: float
    token_budget_multiplier: float
    max_tokens_multiplier: float
    style: tuple[str, ...]
    admits_background_work: bool


@dataclass(frozen=True, slots=True)
class EmotionSnapshot:
    """A state reading with its derived values and bindings, for the API and the inspector."""

    state: EmotionState
    valence: float
    arousal: float
    behaviour: BehaviourParameters
    baselines: Mapping[str, float] = field(default_factory=dict)
    reference: str | None = None
    """The `emotion_history` row this reading corresponds to. A reply can then be joined
    back to the mood that produced it (docs/05 §5.1, `message.emotion_ref`)."""


@runtime_checkable
class EmotionReader(Protocol):
    """Read-only access to emotional state (docs/03 §5.7).

    Writes go through the owning module's own commands. This is the port everything outside
    `emotion/` sees, which is why the brain does not import the emotion module.
    """

    async def state(self) -> EmotionState: ...

    async def snapshot(self) -> EmotionSnapshot: ...

    async def history(
        self, *, since: datetime | None = None, limit: int = 200
    ) -> list[Mapping[str, Any]]: ...
