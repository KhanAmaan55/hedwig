"""The brain's `MindReader`, backed by real emotional state (docs/03 §5.7).

The same seam Milestone 5 used for memory, cashed in for emotion: the `snapshot` node was
written against a narrow port with `DefaultMindReader` behind it, and switching to real state
is this file plus one line in `wiring.py`. `nodes.py` does not change.

It is also the boundary that keeps the brain from importing the emotion module. The brain
knows what a `TurnPolicy` is; it does not know what a mood is, and the day emotion is
replaced by something else, this file is what gets rewritten.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hedwig.core.logging import fields, get_logger
from hedwig.core.ports.brain import TurnPolicy
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

    async def policy(self) -> TurnPolicy:
        state = await self.emotion.state()
        policy = turn_policy(state, base=self.base)
        logger.debug(
            "turn policy from emotional state",
            extra=fields(
                diversity=policy.diversity,
                max_tokens=policy.max_tokens,
                token_budget=policy.token_budget,
                directives=len(policy.style),
            ),
        )
        return policy
