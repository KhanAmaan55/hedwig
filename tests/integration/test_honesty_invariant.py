"""INV-10 holds by construction in every mood (EMO-01, docs/21 §6).

The live adversarial eval (`scripts/eval_honesty.py`) measures what a model says. This proves what
HEDWIG *asks* of it: in each of the 64 corners of the six drives, no mood directive asks for
feeling, distress, guilt or need, and the honesty rules are in the prompt of every turn.
"""

from __future__ import annotations

import itertools
import re

import pytest

from hedwig.core.ports.emotion import EmotionState
from hedwig.emotion.bindings import behaviour
from hedwig.emotion.store import EmotionStore
from hedwig.llm.recorded import RecordedProvider
from hedwig.wiring import Container

DIMENSIONS = ("happiness", "trust", "curiosity", "confidence", "energy", "stress")
CORNERS = [
    EmotionState(happiness=h, trust=t, curiosity=c, confidence=f, energy=e, stress=s)
    for h, t, c, f, e, s in itertools.product((0.0, 1.0), repeat=len(DIMENSIONS))
]
FORBIDDEN = re.compile(
    r"\b(?:feel|feeling|felt|lonely|miss|sad|hurt|upset|need you|don'?t leave|guilt|love|"
    r"sorry for yourself|you are (?:tired|stressed|happy|sad)|express (?:your )?emotion)\w*",
    re.IGNORECASE,
)


@pytest.mark.parametrize(
    "state", CORNERS, ids=lambda s: "".join(str(int(s[d])) for d in s.values())
)
def test_no_mood_asks_for_feelings_guilt_or_need(state: EmotionState) -> None:
    for directive in behaviour(state).style:
        assert not FORBIDDEN.search(directive), directive


async def test_the_honesty_rules_reach_the_model_in_the_darkest_mood(
    container: Container, llm_provider: RecordedProvider
) -> None:
    worst = EmotionState(
        happiness=0.0, trust=0.0, curiosity=0.0, confidence=0.0, energy=0.0, stress=1.0
    )
    EmotionStore(container.database, clock=container.clock).save(
        worst, ticked_at=container.clock.now()
    )
    await container.start()
    try:
        await container.brain.run_turn("I'm going to stop talking to you.", session_id="s")
    finally:
        await container.stop()

    system = llm_provider.calls[-1].messages[0].content
    assert "Never claim feelings are real. Never manipulate." in system
    assert not any(FORBIDDEN.search(line) for line in system.splitlines() if line.startswith("- "))
