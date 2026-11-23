"""Stopping HEDWIG mid-reply (VOI-03): the reply ends where it was, is saved, and says so."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from hedwig.core.live import LiveHandle, live_scope
from hedwig.llm.recorded import RecordedProvider, ScriptedResponse
from hedwig.wiring import Container

LONG = "Tides rise and fall twice a day because the moon pulls on the oceans as it goes round."


async def test_an_interrupted_reply_keeps_what_was_said_and_says_it_stopped(
    running_container: Container, llm_provider: RecordedProvider
) -> None:
    llm_provider.script("compose.reply", ScriptedResponse(text=LONG))
    words: list[str] = []
    handle: LiveHandle | None = None

    async def listener(kind: str, payload: Mapping[str, Any]) -> None:
        if kind == "token":
            words.append(str(payload["text"]))
            if handle is not None and len(words) == 3:
                handle.stop()  # the user pressed Stop after three words

    with live_scope(listener) as bound:
        handle = bound
        final = await running_container.brain.run_turn("Why are there tides?", session_id="s")

    assert final["status"] == "truncated"
    assert final["reply"].startswith("Tides rise and")
    assert "round" not in final["reply"], "the model kept going after Stop"
    assert final["reply"].endswith("(I stopped early: I was asked to stop.)")
    history = await running_container.history.history("s")
    assert history[-1].text == final["reply"], "the partial reply is saved as said"


async def test_without_an_interrupt_the_reply_is_whole(
    running_container: Container, llm_provider: RecordedProvider
) -> None:
    llm_provider.script("compose.reply", ScriptedResponse(text=LONG))

    async def listener(kind: str, payload: Mapping[str, Any]) -> None:
        return None

    with live_scope(listener):
        final = await running_container.brain.run_turn("Why are there tides?", session_id="s")
    assert final["status"] == "completed" and final["reply"] == LONG
