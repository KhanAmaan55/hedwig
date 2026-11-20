"""The prompt a real turn sends, for the conversation that first went wrong (docs/27 §6.3).

In the first in-app conversation, HEDWIG recalled and cited "my favourite tea is smoky lapsang
souchong", and the model answered that it could not know. Each cause was a line of the prompt.
This pins the composed prompt for that exact exchange, through the whole system: capture,
recall, personality, relationship, emotion and assembly.
"""

from __future__ import annotations

from hedwig.core.ports.llm import Role
from hedwig.llm.prompts import REPLY_RULE
from hedwig.llm.recorded import RecordedProvider
from hedwig.wiring import Container


async def _tea_prompt(hedwig: Container, provider: RecordedProvider) -> str:
    await hedwig.brain.run_turn(
        "Hi HEDWIG! Please remember that my favourite tea is smoky lapsang souchong.",
        session_id="sess_first",
    )
    await hedwig.bus.drain()
    await hedwig.brain.run_turn("What tea do I like?", session_id="sess_second")

    messages = provider.calls[-1].messages
    assert messages[0].role is Role.SYSTEM
    assert messages[-1].content == "What tea do I like?"
    return messages[0].content


async def test_the_recalled_preference_reaches_the_model_once_and_usable(
    running_container: Container, llm_provider: RecordedProvider
) -> None:
    system = await _tea_prompt(running_container, llm_provider)

    memory_block = system.split("[memory", 1)[1]
    assert "Use it when it is relevant" in memory_block
    assert memory_block.count("favourite tea") == 1, "the title repeated the content"
    assert "lapsang souchong." in memory_block, "the memory was cut short"


async def test_nothing_in_the_prompt_tells_the_model_to_ignore_it(
    running_container: Container, llm_provider: RecordedProvider
) -> None:
    system = await _tea_prompt(running_container, llm_provider)

    assert "presume shared history" not in system
    assert "lands" not in system
    assert "flag caveats" not in system
    assert system.endswith(REPLY_RULE)
