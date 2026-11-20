"""Goals into the turn, by composition (docs/08 §6.1).

Wraps whichever `MindReader` wiring chose and adds the active goals to its snapshot. Nothing
upstream — personality, emotion, the brain — knows goals exist.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from hedwig.core.ports.brain import MindReader, MindSnapshot
from hedwig.core.ports.goals import Goal, GoalReader

MAX_IN_PROMPT = 5
"""Goals are context, not the agenda: a handful at most, highest priority first."""


@dataclass(slots=True)
class GoalsMindReader:
    inner: MindReader
    goals: GoalReader

    async def snapshot(self, *, input: str = "") -> MindSnapshot:  # noqa: A002
        snapshot = await self.inner.snapshot(input=input)
        active = await self.goals.active(limit=MAX_IN_PROMPT)
        return replace(snapshot, goals=tuple(render(goal) for goal in active))


def render(goal: Goal) -> str:
    """One line per goal: what, how far along, how it ends."""
    line = goal.description
    if goal.progress > 0:
        line += f" ({round(goal.progress * 100)}% done)"
    if goal.completion:
        line += f"; done when: {goal.completion}"
    return line
