"""Query planning for goals and time (docs/06 §20)."""

from __future__ import annotations

import pytest

from hedwig.brain.planner import RulePlanner, goal_subject
from hedwig.core.ports.brain import TurnPolicy
from hedwig.memory.retrieval import about_recent

GOALS = (
    "Help the user run the Lisbon half marathon in October (40% done); lately: progress: 14 km",
    "Help the user learn the Bach minuet on the cello",
)


def test_a_goal_line_is_reduced_to_what_it_is_about() -> None:
    assert goal_subject(GOALS[0]) == "run the Lisbon half marathon in October"
    assert goal_subject("Learn Portuguese; done when: a conversation") == "Learn Portuguese"
    assert goal_subject("Learn (Rust) (40% done)") == "Learn (Rust)"
    assert goal_subject("Learn (Rust)") == "Learn (Rust)"
    assert goal_subject("Ship v2 (finished 2026-02-01); lately: done: shipped") == "Ship v2"


@pytest.mark.parametrize(
    "text",
    [
        "Am I on track?",
        "Any update on my project?",
        "How's practice going?",
        "What am I working towards?",
        "How's my training going?",
        "Am I making progress?",
    ],
)
async def test_a_message_about_the_users_aims_searches_their_goals(text: str) -> None:
    queries = await RulePlanner().plan_queries(text, policy=TurnPolicy(), goals=GOALS)
    assert queries[0] == text
    assert "run the Lisbon half marathon in October" in queries
    assert "learn the Bach minuet on the cello" in queries


@pytest.mark.parametrize(
    "text",
    [
        "Do I like seafood?",
        "Tell me about tides.",
        "Explain neural network training.",  # a topic, not the user's marathon training
        "What is progress in philosophy?",
    ],
)
async def test_other_messages_do_not(text: str) -> None:
    assert await RulePlanner().plan_queries(text, policy=TurnPolicy(), goals=GOALS) == (text,)


@pytest.mark.parametrize(
    ("text", "recent"),
    [
        ("What were we talking about yesterday?", True),
        ("What did I tell you recently?", True),
        ("Catch me up", True),
        ("What did we just do?", True),
        ("What have we done today?", True),
        ("Do I like seafood?", False),
        ("Where does Ana live?", False),
    ],
)
def test_only_a_question_about_lately_brings_in_the_latest_episodes(
    text: str, recent: bool
) -> None:
    assert about_recent([text]) is recent


async def test_asked_about_their_aims_the_reply_is_told_to_answer_about_the_goals() -> None:
    """Found live: recall found the run, and a 2B model still asked which goal was meant."""
    from datetime import UTC, datetime

    from hedwig.brain.stubs import DefaultMindReader
    from hedwig.core.ports.goals import Goal, GoalKind, GoalStatus
    from hedwig.goals.mind import ASKED_ABOUT_AIMS, GoalsMindReader

    now = datetime(2026, 11, 24, tzinfo=UTC)
    marathon = Goal(
        id="goal_1",
        description="Help the user run the Lisbon half marathon in October",
        kind=GoalKind.USER_STATED,
        status=GoalStatus.ACTIVE,
        priority=0.5,
        progress=0.0,
        context="",
        completion="",
        origin="user",
        created_at=now,
        last_touched_at=now,
    )

    class Goals:
        async def active(self, *, limit: int = 12) -> list[Goal]:
            return [marathon]

        async def mentioned_in(self, text: str, *, limit: int = 2) -> list[Goal]:
            return []

        async def notes(self, goal_id: str, *, limit: int = 20) -> list[object]:
            return []

    reader = GoalsMindReader(DefaultMindReader(), Goals())  # type: ignore[arg-type]
    assert ASKED_ABOUT_AIMS in (await reader.snapshot(input="Am I on track?")).directives
    assert ASKED_ABOUT_AIMS not in (await reader.snapshot(input="Do I like seafood?")).directives
