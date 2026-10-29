"""The brain's `MindReader`: personality, relationship and emotion in one reading (docs/27 §3).

Replaces `EmotionMindReader` when personality is enabled. One call, one moment, one
internally consistent turn — and the turn learns not just its mood but who is speaking and to
whom.

**It composes three sources through ports and imports none of their modules.** Emotion
arrives as an `EmotionReader`, the relationship as a `RelationshipReader`; this module never
imports `hedwig.emotion` or `hedwig.memory`. ADR-0008 forbids cognition siblings importing
each other, and `.importlinter` declares `emotion` and `personality` independent, so this is
enforced rather than intended.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hedwig.core.logging import fields, get_logger
from hedwig.core.ports.brain import MindSnapshot, TurnPolicy
from hedwig.core.ports.emotion import EmotionReader
from hedwig.core.ports.personality import PersonaReader, RelationshipReader
from hedwig.personality.instructions import acknowledgement, detect_instructions
from hedwig.personality.rendering import (
    generation_policy,
    merge,
    relationship_directives,
    trait_directives,
)

logger = get_logger(__name__)


@dataclass(slots=True)
class PersonaMindReader:
    """Adapts personality, relationship and emotion to the brain's `MindReader` port."""

    persona: PersonaReader
    emotion: EmotionReader | None = None
    relationship: RelationshipReader | None = None
    base: TurnPolicy = field(default_factory=TurnPolicy)

    async def snapshot(self, *, input: str = "") -> MindSnapshot:  # noqa: A002
        """Everything the turn will know about who it is and how it should speak.

        Order of precedence when the twelve-directive cap bites (docs/10 §4): an
        acknowledgement of an explicit request first, then character, then the relationship,
        then the mood of the moment. What goes first is what HEDWIG is least willing to lose.
        """
        profile = await self.persona.profile()
        relation = await self.relationship.with_user() if self.relationship else None
        reading = await self.emotion.snapshot() if self.emotion else None

        # Personality sets the generation parameters; emotion then modulates them through
        # its own published parameters. Neither computes the other's arithmetic.
        policy = generation_policy(profile, self.base)
        if reading is not None:
            policy = reading.behaviour.apply_to(policy)

        acknowledged = acknowledgement(detect_instructions(input))
        directives = merge(
            (acknowledged,) if acknowledged else (),
            trait_directives(profile),
            relationship_directives(relation),
            reading.behaviour.style if reading else (),
        )
        policy = TurnPolicy(
            token_budget=policy.token_budget,
            max_tokens=policy.max_tokens,
            temperature=policy.temperature,
            diversity=policy.diversity,
            style=directives,
            allow_tools=policy.allow_tools,
        )

        logger.debug(
            "mind snapshot",
            extra=fields(
                directives=len(directives),
                max_tokens=policy.max_tokens,
                temperature=policy.temperature,
                acknowledged=acknowledged is not None,
            ),
        )
        return MindSnapshot(
            policy=policy,
            mood=reading.state.as_dict() if reading else {},
            valence=reading.valence if reading else 0.0,
            arousal=reading.arousal if reading else 0.0,
            directives=directives,
            reference=reading.reference if reading else None,
            identity=profile.identity.lines(),
            traits=profile.values(),
            relationship=relation.as_dict() if relation else {},
        )
