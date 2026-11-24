"""Goals into the turn, by composition (docs/08 §6.1).

Wraps whichever `MindReader` wiring chose and adds the active goals to its snapshot. Nothing
upstream — personality, emotion, the brain — knows goals exist.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from hedwig.core.ports.brain import MindReader, MindSnapshot
from hedwig.core.ports.goals import Goal, GoalNote, GoalReader, GoalStatus
from hedwig.knowledge.cues import refers_to_aims

MAX_IN_PROMPT = 5
"""Goals are context, not the agenda: a handful at most, highest priority first."""


ASKED_ABOUT_AIMS = (
    "They are asking how their goals are going without naming one. Answer about the goals "
    "listed under goals, using what you remember about them; do not ask which they mean."
)
"""Found live (docs/06 §20): recall found the 14 km run for "Am I on track?", and a 2B model
still answered "What are you referring to?". Like curiosity's share directive (docs/11 §13),
the moment is said through cognition's own channel."""

NOTES_PER_GOAL = 2
"""The latest decisions and open questions; the whole journal is in the Goals tab."""

CLOSED = {
    GoalStatus.DONE: "finished",
    GoalStatus.ABANDONED: "dropped",
    GoalStatus.BLOCKED: "blocked",
}


@dataclass(slots=True)
class GoalsMindReader:
    inner: MindReader
    goals: GoalReader

    async def snapshot(self, *, input: str = "") -> MindSnapshot:  # noqa: A002
        snapshot = await self.inner.snapshot(input=input)
        shown = list(await self.goals.active(limit=MAX_IN_PROMPT))
        # "How did the compiler go?" names a project that is no longer active (ADR-0025).
        if input:
            shown += [g for g in await self.goals.mentioned_in(input) if g not in shown]
        lines = [render(goal, await self.goals.notes(goal.id, limit=8)) for goal in shown]
        directives = snapshot.directives
        if lines and input and refers_to_aims(input):
            directives = (*directives, ASKED_ABOUT_AIMS)
        return replace(snapshot, goals=tuple(lines), directives=directives)


def render(goal: Goal, notes: Sequence[GoalNote] = ()) -> str:
    """One line per goal: what, how far along, how it ends, and what happened lately."""
    line = goal.description
    if goal.status in CLOSED:
        line = (
            f"{goal.description} ({CLOSED[goal.status]} {goal.last_touched_at.date().isoformat()})"
        )
    elif goal.progress > 0:
        line += f" ({round(goal.progress * 100)}% done)"
    if goal.completion and goal.status not in CLOSED:
        line += f"; done when: {goal.completion}"
    telling = [n for n in notes if not (n.source == "system" and n.text == "started")]
    if telling[:NOTES_PER_GOAL]:
        line += "; lately: " + "; ".join(
            f"{n.kind.value}: {n.text} ({n.created_at.date().isoformat()})"
            for n in telling[:NOTES_PER_GOAL]
        )
    return line
