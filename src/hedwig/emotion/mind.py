"""The brain's `MindReader`, backed by real emotional state (docs/03 §5.7, docs/07 §14.2).

The same seam Milestone 5 used for memory, cashed in for emotion: the `snapshot` node was
written against a narrow port with `DefaultMindReader` behind it, and switching to real state
is this file plus one line in `wiring.py`. `nodes.py` does not change.

It is also the boundary that keeps the brain from importing the emotion module. The brain
knows what a `MindSnapshot` is; it does not know what a mood is, and the day emotion is
replaced by something else, this file is what gets rewritten.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hedwig.core.logging import fields, get_logger
from hedwig.core.ports.brain import MindSnapshot, TurnPolicy
from hedwig.core.ports.emotion import EmotionReader
from hedwig.emotion.bindings import turn_policy

logger = get_logger(__name__)


@dataclass(slots=True)
class EmotionMindReader:
    """Adapts `EmotionReader` to the brain's `MindReader` port."""

    emotion: EmotionReader
    base: TurnPolicy = field(default_factory=TurnPolicy)
    """The configured defaults. Emotion *modulates* these rather than replacing them, so a
    flat mood produces exactly the behaviour the system would have had without an emotion
    engine at all."""

    async def snapshot(self) -> MindSnapshot:
        """One read, one moment. Everything the turn will know about its own mood.

        Taken as a single call rather than several, because two reads a millisecond apart
        could disagree — and a turn whose policy came from one mood and whose recorded state
        came from another is a turn nobody can explain afterwards.
        """
        reading = await self.emotion.snapshot()
        policy = turn_policy(reading.state, base=self.base)

        logger.debug(
            "mind snapshot",
            extra=fields(
                valence=reading.valence,
                arousal=reading.arousal,
                diversity=policy.diversity,
                max_tokens=policy.max_tokens,
                directives=len(policy.style),
            ),
        )
        return MindSnapshot(
            policy=policy,
            mood=reading.state.as_dict(),
            valence=reading.valence,
            arousal=reading.arousal,
            directives=policy.style,
            reference=reading.reference,
        )
