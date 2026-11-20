"""Real tools in a real turn (COG-02). Before this the model was shown `[stub] get_datetime()`."""

from __future__ import annotations

from hedwig.llm.recorded import RecordedProvider
from hedwig.wiring import Container


async def test_asking_the_time_puts_the_real_time_in_front_of_the_model(
    running_container: Container, llm_provider: RecordedProvider
) -> None:
    final = await running_container.brain.run_turn("What time is it?", session_id="s")

    system = llm_provider.calls[-1].messages[0].content
    assert "[tool output" in system
    assert "It is Thursday 01 January 2026," in system  # local zone: the machine's
    assert "stub" not in system.lower()
    assert final["status"] == "completed"
    audited = running_container.database.query("SELECT tool_name, status FROM tool_call")
    assert [(row["tool_name"], row["status"]) for row in audited] == [("get_datetime", "ok")]


async def test_arithmetic_in_a_message_is_computed_not_guessed(
    running_container: Container, llm_provider: RecordedProvider
) -> None:
    await running_container.brain.run_turn("Quick one: what is 1234 * 5678?", session_id="s")

    assert "1234 * 5678 = 7006652" in llm_provider.calls[-1].messages[0].content
