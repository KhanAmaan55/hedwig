"""Capture: deciding what is worth remembering (docs/06 §4).

**Not everything is remembered.** A memory system that stores everything retrieves nothing,
so candidates pass four gates, cheapest first (docs/06 §4.1):

1. **Substance** — is there a fact, a decision, a preference, an event? Pure
   acknowledgement is dropped.
2. **Novelty** — near-duplicates *reinforce the existing memory* instead of adding one.
   Reinforcing rather than duplicating is what keeps the store clean.
3. **Confidence** — below a floor, a candidate is dropped rather than stored as tentative.
   Garbage tentative beliefs are worse than no beliefs.
4. **Budget** — at most N per turn. A turn that seems to yield twelve memories has usually
   had a bad extraction.

The extraction that *produces* candidates is a model call and belongs to reflection
(docs/12 §3). This module takes candidates it is given and decides their fate, which is why
it is testable without a model.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum

from hedwig.core.ids import new_id
from hedwig.core.logging import fields as log_fields
from hedwig.core.logging import get_logger
from hedwig.core.ports import Clock
from hedwig.core.ports.memory import (
    Belief,
    BeliefStatus,
    Episode,
    EpisodeKind,
)
from hedwig.core.types import (
    EntityId,
    MemoryId,
    Provenance,
    SalienceInputs,
    SessionId,
    TrustTier,
)
from hedwig.memory.retrieval import _tokens  # shared tokeniser; one definition of "word"
from hedwig.memory.salience import base_importance, reinforcement
from hedwig.memory.store import SqliteMemoryStore

logger = get_logger(__name__)

MIN_SUBSTANCE_WORDS = 4
MIN_CONFIDENCE = 0.3
DUPLICATE_THRESHOLD = 0.92
"""Word-overlap similarity above which a candidate is treated as the same memory. docs/06
§4.1 specifies cosine 0.92 on embeddings; this is the same threshold on the lexical
similarity available today (docs/06 §13.2)."""
MAX_CAPTURES_PER_TURN = 5

ACKNOWLEDGEMENTS = frozenset(
    {
        "ok",
        "okay",
        "thanks",
        "thank you",
        "sure",
        "yes",
        "no",
        "yep",
        "nope",
        "got it",
        "cool",
        "nice",
        "great",
        "sounds good",
        "hello",
        "hi",
        "hey",
    }
)


class Verdict(StrEnum):
    STORED = "stored"
    REINFORCED = "reinforced"
    """A near-duplicate already existed, so it got stronger instead."""
    DROPPED_SUBSTANCE = "dropped_substance"
    DROPPED_CONFIDENCE = "dropped_confidence"
    DROPPED_BUDGET = "dropped_budget"


@dataclass(frozen=True, slots=True)
class EpisodeCandidate:
    """A proposed episodic memory, before the gates."""

    title: str
    content: str
    kind: EpisodeKind = EpisodeKind.INTERACTION
    confidence: float = 1.0
    entities: tuple[EntityId, ...] = ()
    session_id: SessionId | None = None
    occurred_at: datetime | None = None
    salience_inputs: SalienceInputs = field(default_factory=SalienceInputs)
    trust: TrustTier = TrustTier.SELF
    source_ref: str | None = None


@dataclass(frozen=True, slots=True)
class BeliefCandidate:
    """A proposed semantic memory."""

    statement: str
    confidence: float = 0.5
    subject_id: EntityId | None = None
    predicate: str | None = None
    object_text: str | None = None
    derived_from: tuple[MemoryId, ...] = ()
    salience_inputs: SalienceInputs = field(default_factory=SalienceInputs)
    trust: TrustTier = TrustTier.SELF
    source_ref: str | None = None


@dataclass(frozen=True, slots=True)
class CaptureOutcome:
    """What happened to one candidate. Recorded so capture decisions are auditable."""

    verdict: Verdict
    memory_id: MemoryId | None = None
    reason: str = ""


@dataclass(frozen=True, slots=True)
class CaptureReport:
    outcomes: tuple[CaptureOutcome, ...] = ()

    @property
    def stored(self) -> tuple[MemoryId, ...]:
        return tuple(
            outcome.memory_id
            for outcome in self.outcomes
            if outcome.verdict is Verdict.STORED and outcome.memory_id
        )

    def count(self, verdict: Verdict) -> int:
        return sum(1 for outcome in self.outcomes if outcome.verdict is verdict)


class CaptureService:
    """Applies the gates and writes what survives."""

    def __init__(
        self,
        store: SqliteMemoryStore,
        *,
        clock: Clock,
        max_per_turn: int = MAX_CAPTURES_PER_TURN,
    ) -> None:
        self._store = store
        self._clock = clock
        self._max_per_turn = max_per_turn

    async def capture(
        self,
        *,
        episodes: Sequence[EpisodeCandidate] = (),
        beliefs: Sequence[BeliefCandidate] = (),
    ) -> CaptureReport:
        outcomes: list[CaptureOutcome] = []
        stored = 0

        for candidate in episodes:
            if stored >= self._max_per_turn:
                outcomes.append(
                    CaptureOutcome(
                        verdict=Verdict.DROPPED_BUDGET,
                        reason=f"more than {self._max_per_turn} captures in one turn",
                    )
                )
                continue
            outcome = await self._capture_episode(candidate)
            outcomes.append(outcome)
            stored += outcome.verdict is Verdict.STORED

        for belief in beliefs:
            if stored >= self._max_per_turn:
                outcomes.append(
                    CaptureOutcome(verdict=Verdict.DROPPED_BUDGET, reason="capture budget")
                )
                continue
            outcome = await self._capture_belief(belief)
            outcomes.append(outcome)
            stored += outcome.verdict is Verdict.STORED

        report = CaptureReport(outcomes=tuple(outcomes))
        logger.debug(
            "capture complete",
            extra=log_fields(
                stored=report.count(Verdict.STORED),
                reinforced=report.count(Verdict.REINFORCED),
                dropped=len(outcomes)
                - report.count(Verdict.STORED)
                - report.count(Verdict.REINFORCED),
            ),
        )
        return report

    # -- gates -------------------------------------------------------------

    async def _capture_episode(self, candidate: EpisodeCandidate) -> CaptureOutcome:
        text = f"{candidate.title} {candidate.content}".strip()

        if not has_substance(text):
            return CaptureOutcome(
                verdict=Verdict.DROPPED_SUBSTANCE, reason="no fact, decision or event"
            )

        if candidate.confidence < MIN_CONFIDENCE:
            return CaptureOutcome(
                verdict=Verdict.DROPPED_CONFIDENCE,
                reason=f"extraction confidence {candidate.confidence:.2f} below floor",
            )

        # Novelty: reinforce rather than duplicate.
        recent = await self._store.recent_episodes(limit=40)
        for existing in recent:
            if similarity(text, f"{existing.title} {existing.content}") >= DUPLICATE_THRESHOLD:
                await self._store.reinforce(
                    [existing.id], reinforcement(corroborated=True), cause="duplicate capture"
                )
                return CaptureOutcome(
                    verdict=Verdict.REINFORCED,
                    memory_id=existing.id,
                    reason="near-duplicate of an existing memory",
                )

        inputs = replace(
            candidate.salience_inputs,
            novelty=max(candidate.salience_inputs.novelty, novelty_against(text, recent)),
        )
        importance = base_importance(inputs)
        now = self._clock.now()

        episode = Episode(
            id=MemoryId(new_id("ep")),
            occurred_at=candidate.occurred_at or now,
            title=candidate.title,
            content=candidate.content,
            kind=candidate.kind,
            session_id=candidate.session_id,
            entities=candidate.entities,
            # Salience starts at base importance and diverges from it thereafter
            # (docs/06 §4.2).
            salience=importance,
            base_importance=importance,
            emotional_charge=inputs.emotional_charge,
            confidence=candidate.confidence,
            provenance=Provenance(
                tier=candidate.trust, source_ref=candidate.source_ref, captured_at=now
            ),
        )
        memory_id = await self._store.add_episode(episode)
        return CaptureOutcome(verdict=Verdict.STORED, memory_id=memory_id)

    async def _capture_belief(self, candidate: BeliefCandidate) -> CaptureOutcome:
        if not has_substance(candidate.statement):
            return CaptureOutcome(verdict=Verdict.DROPPED_SUBSTANCE, reason="not a proposition")

        if candidate.confidence < MIN_CONFIDENCE:
            return CaptureOutcome(
                verdict=Verdict.DROPPED_CONFIDENCE,
                reason=f"confidence {candidate.confidence:.2f} below floor",
            )

        importance = base_importance(candidate.salience_inputs)
        now = self._clock.now()

        belief = Belief(
            id=MemoryId(new_id("bel")),
            statement=candidate.statement,
            valid_from=now,
            # Everything enters tentative. Promotion needs corroboration or user
            # confirmation — the guard against confabulation hardening into fact
            # (docs/06 §3.2).
            status=BeliefStatus.TENTATIVE,
            subject_id=candidate.subject_id,
            predicate=candidate.predicate,
            object_text=candidate.object_text,
            confidence=min(candidate.confidence, 0.5),
            salience=importance,
            base_importance=importance,
            provenance=Provenance(
                tier=candidate.trust,
                source_ref=candidate.source_ref,
                derived_from=candidate.derived_from,
                captured_at=now,
            ),
        )
        memory_id = await self._store.add_belief(belief)
        return CaptureOutcome(verdict=Verdict.STORED, memory_id=memory_id)


# -- pure gate helpers -----------------------------------------------------


def has_substance(text: str) -> bool:
    """Whether there is anything here worth keeping (docs/06 §4.1 gate 1)."""
    stripped = text.strip().lower().rstrip(".!?")
    if not stripped:
        return False
    if stripped in ACKNOWLEDGEMENTS:
        return False
    return len(stripped.split()) >= MIN_SUBSTANCE_WORDS


def similarity(left: str, right: str) -> float:
    """Word-overlap similarity, the stand-in for cosine until embeddings exist."""
    a, b = _tokens(left), _tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def novelty_against(text: str, existing: Sequence[Episode]) -> float:
    """How new this is: 1 minus its closest resemblance to anything already stored."""
    if not existing:
        return 1.0
    closest = max(similarity(text, f"{episode.title} {episode.content}") for episode in existing)
    return max(0.0, 1.0 - closest)
