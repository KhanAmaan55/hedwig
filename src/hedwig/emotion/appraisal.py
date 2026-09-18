"""Events, read as appraisals (docs/09 §4).

**Rules only. No model, ever.** An earlier draft routed ambiguous conversational content to
the utility model for a structured appraisal; that path is not built and is not planned, for
three reasons in order of weight:

1. Determinism is what makes this testable at all. The same event stream and the same clock
   must produce the same state, bit for bit. A model call in the loop makes emotional state
   unreproducible and every scenario test advisory.
2. A model appraising a person's tone, continuously, stored, and used to modulate behaviour
   is sentiment analysis of the user. That deserves a far higher bar than "richer mood".
3. It would cost more than the conversation itself.

The cost, stated rather than hidden: HEDWIG's mood responds to *what happened* and to coarse
explicit markers. It does not respond to how something was said. An unmatched event produces
`NEUTRAL`, which is not a state change.

Each rule is a pure function of the payload, so the whole table is testable as arithmetic.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any

from hedwig.core.ports.emotion import NEUTRAL, Appraisal
from hedwig.core.ports.event_bus import Event

Rule = Callable[[Mapping[str, Any]], Appraisal]

THANKS = re.compile(r"\b(thanks|thank you|appreciate it|cheers)\b", re.IGNORECASE)
GREETING = re.compile(r"^\s*(hi|hey|hello|good (morning|afternoon|evening))\b", re.IGNORECASE)
QUESTION = re.compile(r"\?\s*$")
"""Deliberately crude. These are explicit markers, not sentiment: a rule that tried to infer
mood from phrasing would be the model path in §4.2 wearing a regex costume."""

LONG_MESSAGE_CHARS = 600
"""Above this, a message implies real work. Length is a weak signal but an honest one."""

CONSECUTIVE_FAILURE_THRESHOLD = 3


def appraise_session_started(payload: Mapping[str, Any]) -> Appraisal:
    """Someone came back. docs/09 §4.2 additionally scales this by the gap since the last
    session; that needs a last-seen timestamp this module does not own, so the base rule
    fires uniformly until relationship memory can supply it."""
    return Appraisal(
        novelty=0.20,
        social_valence=0.30,
        rationale="a session started",
    )


def appraise_message_received(payload: Mapping[str, Any]) -> Appraisal:
    text = str(payload.get("text", ""))
    if not text.strip():
        return NEUTRAL

    social = 0.0
    if THANKS.search(text):
        social = 0.35
    elif GREETING.search(text):
        social = 0.15

    effort = 0.30 if len(text) > LONG_MESSAGE_CHARS else 0.05
    certainty = -0.15 if QUESTION.search(text.strip()) else 0.0

    return Appraisal(
        social_valence=social,
        effort=effort,
        certainty=certainty,
        significance=0.6,
        rationale="a message arrived",
    )


def appraise_turn_completed(payload: Mapping[str, Any]) -> Appraisal:
    status = str(payload.get("status", ""))
    tool_calls = _number(payload.get("tool_calls"))
    effort = min(0.4, 0.10 * tool_calls)

    if status == "completed":
        return Appraisal(
            goal_congruence=0.30,
            certainty=0.20,
            effort=effort,
            rationale="a turn completed",
        )
    if status == "truncated":
        return Appraisal(
            goal_congruence=-0.20,
            certainty=-0.20,
            effort=0.40,
            rationale="a turn hit a cap",
        )
    if status == "refused":
        # Declining correctly is *consistent with* the identity core. A refusal must not
        # read to the emotion engine as a failure, or HEDWIG learns to dread its own
        # safety behaviour (docs/09 §6.1).
        return Appraisal(
            goal_congruence=-0.30,
            social_valence=-0.10,
            norm_fit=0.30,
            rationale="a turn was refused",
        )
    if status == "failed":
        return Appraisal(
            goal_congruence=-0.50,
            certainty=-0.30,
            agency=-0.20,
            effort=effort,
            rationale="a turn failed",
        )
    return NEUTRAL


def appraise_belief_formed(payload: Mapping[str, Any]) -> Appraisal:
    return Appraisal(novelty=0.40, certainty=0.10, rationale="a belief formed")


def appraise_entity_discovered(payload: Mapping[str, Any]) -> Appraisal:
    return Appraisal(novelty=0.50, rationale="a new entity appeared")


def appraise_episode_stored(payload: Mapping[str, Any]) -> Appraisal:
    """Scaled by salience: remembering something trivial is not an event."""
    salience = _number(payload.get("salience"), default=0.5)
    return Appraisal(
        novelty=0.30,
        significance=max(0.0, min(1.0, salience)),
        rationale="a memory was stored",
    )


def appraise_service_failed(payload: Mapping[str, Any]) -> Appraisal:
    return Appraisal(
        goal_congruence=-0.50,
        certainty=-0.40,
        agency=-0.20,
        rationale="a service failed",
    )


def appraise_task_completed(payload: Mapping[str, Any]) -> Appraisal:
    if str(payload.get("status", "")) != "failed":
        return NEUTRAL
    return Appraisal(
        goal_congruence=-0.30,
        agency=-0.20,
        effort=0.20,
        significance=0.6,
        rationale="a background task failed",
    )


def appraise_model_switched(payload: Mapping[str, Any]) -> Appraisal:
    return Appraisal(certainty=-0.15, significance=0.5, rationale="the model changed")


RULES: dict[str, Rule] = {
    "conversation.session.started": appraise_session_started,
    "conversation.message.received": appraise_message_received,
    "conversation.turn.completed": appraise_turn_completed,
    "memory.belief.formed": appraise_belief_formed,
    "memory.entity.discovered": appraise_entity_discovered,
    "memory.episode.stored": appraise_episode_stored,
    "system.service.failed": appraise_service_failed,
    "scheduler.task.completed": appraise_task_completed,
    "llm.model.switched": appraise_model_switched,
}
"""Every event type that moves the mood.

docs/09 §4.2 also specifies rules for `tools.call.completed`, `goals.goal.closed`,
`curiosity.finding.produced` and `conversation.feedback.given`. Nothing publishes those
yet, so they are absent rather than guessed at — they are the first rules to add when their
publishers land.
"""

SUBSCRIPTION_PATTERNS: tuple[str, ...] = tuple(sorted(RULES))


class Appraiser:
    """Turns events into appraisals, and remembers just enough to escalate.

    The only state it keeps is a consecutive-failure count, which is what lets the third
    failure in a row read differently from the first — the one piece of context that a
    per-event pure function cannot have (docs/09 §4.2).
    """

    def __init__(self) -> None:
        self._consecutive_failures = 0

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    def appraise(self, event: Event) -> Appraisal:
        rule = RULES.get(event.type)
        if rule is None:
            return NEUTRAL

        appraisal = rule(event.payload)
        return self._escalate(event, appraisal)

    def _escalate(self, event: Event, appraisal: Appraisal) -> Appraisal:
        """Repeated failure is worse than the sum of its parts.

        A single failed turn is a mishap; the third in a row is a situation, and a companion
        that reacted identically to both would feel inattentive.
        """
        if event.type not in {"conversation.turn.completed", "scheduler.task.completed"}:
            return appraisal

        failed = str(event.payload.get("status", "")) == "failed"
        if not failed:
            self._consecutive_failures = 0
            return appraisal

        self._consecutive_failures += 1
        if self._consecutive_failures < CONSECUTIVE_FAILURE_THRESHOLD:
            return appraisal

        return Appraisal(
            novelty=appraisal.novelty,
            goal_congruence=appraisal.goal_congruence - 0.20,
            certainty=appraisal.certainty,
            agency=appraisal.agency - 0.30,
            social_valence=appraisal.social_valence,
            effort=appraisal.effort,
            norm_fit=appraisal.norm_fit,
            significance=appraisal.significance,
            rationale=f"{appraisal.rationale} ({self._consecutive_failures} in a row)",
        )


def _number(value: object, *, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, int | float):
        return float(value)
    return default
