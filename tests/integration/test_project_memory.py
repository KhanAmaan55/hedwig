"""Project memory: goals with a journal (MEM-06, ADR-0025, docs/08 §13)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from hedwig.api.app import create_app
from hedwig.core.clock import FakeClock
from hedwig.core.ports.goals import GoalStatus, NoteKind
from hedwig.core.token import TokenFile
from hedwig.llm.recorded import RecordedProvider, json_response
from hedwig.reflection import SessionReflector
from hedwig.wiring import Container

COMPILER = "Help the user finish their Rust compiler"


def _reflector(container: Container) -> SessionReflector:
    service = container.registry.get("reflection")
    assert isinstance(service, SessionReflector)
    return service


async def test_the_journal_records_every_change_without_a_model(
    running_container: Container, clock: FakeClock
) -> None:
    goals = running_container.goals
    goal = await goals.create(COMPILER)
    clock.advance(days=1)
    await goals.update(goal.id, progress=0.4)
    clock.advance(days=1)
    await goals.update(goal.id, status=GoalStatus.DONE)

    notes = await goals.notes(goal.id)

    assert [(n.kind, n.text, n.source) for n in reversed(notes)] == [
        (NoteKind.PROGRESS, "progress 0% → 40%", "system"),
        (NoteKind.DONE, "finished", "system"),
    ]


async def test_reflection_writes_what_a_conversation_decided(
    running_container: Container, llm_provider: RecordedProvider
) -> None:
    goal = await running_container.goals.create(COMPILER)
    llm_provider.script(
        "reflect.session",
        json_response(
            {
                "worth_remembering": True,
                "title": "Parser",
                "summary": "The user worked on the parser.",
                "facts": [],
                "journal": [
                    {"goal": "G1", "kind": "decision", "text": "The user chose a Pratt parser."},
                    {"goal": "G1", "kind": "question", "text": "Error recovery is still open."},
                    {"goal": "G9", "kind": "decision", "text": "A goal it was never shown."},
                    {"goal": "G1", "kind": "blocker", "text": "short"},
                ],
            }
        ),
    )
    for line in ("I went with a Pratt parser in the end.", "Not sure about error recovery yet."):
        await running_container.brain.run_turn(line, session_id="s")
    await running_container.bus.drain()

    await _reflector(running_container).reflect("s")

    notes = await running_container.goals.notes(goal.id)
    assert [(n.kind.value, n.text, n.source, n.session_id) for n in reversed(notes)] == [
        ("decision", "The user chose a Pratt parser.", "reflection", "s"),
        ("question", "Error recovery is still open.", "reflection", "s"),
    ]
    request = next(c for c in llm_provider.calls if c.purpose == "reflect.session")
    assert "journal:" in request.messages[0].content


async def test_how_did_that_project_go_brings_back_a_finished_one(
    running_container: Container, llm_provider: RecordedProvider
) -> None:
    goals = running_container.goals
    compiler = await goals.create(COMPILER)
    await goals.note(compiler.id, NoteKind.DECISION, "Chose a Pratt parser.")
    await goals.update(compiler.id, status=GoalStatus.DONE)
    garden = await goals.create("Help the user plan the garden")
    await goals.note(garden.id, NoteKind.QUESTION, "Where should the tomatoes go?")

    await running_container.brain.run_turn("How did the compiler project go?", session_id="s")
    system = llm_provider.calls[-1].messages[0].content
    block = system.split("[goals", 1)[1].split("\n\n", 1)[0]
    assert "Help the user finish their Rust compiler (finished" in block
    assert "decision: Chose a Pratt parser." in block
    assert "question: Where should the tomatoes go?" in block, "active goals carry their notes"

    await running_container.brain.run_turn("How is the project going?", session_id="t")
    system = llm_provider.calls[-1].messages[0].content
    assert "Rust compiler" not in system, "generic words name no project"


async def test_erasing_a_conversation_erases_what_it_wrote_in_a_journal(
    running_container: Container,
) -> None:
    goal = await running_container.goals.create(COMPILER)
    session = await running_container.brain.open_session(channel="web")
    await running_container.brain.run_turn("Pratt parser it is.", session_id=session)
    await running_container.goals.note(
        goal.id, NoteKind.DECISION, "Chose a Pratt parser.", source="reflection", session_id=session
    )
    preview = await running_container.purger.preview("session", session)
    await running_container.purger.purge("session", session, preview["confirm"])
    assert await running_container.goals.notes(goal.id) == []


@pytest.fixture
async def api(running_container: Container) -> AsyncIterator[AsyncClient]:
    token = TokenFile(running_container.config.runtime.token_path).ensure()
    async with AsyncClient(
        transport=ASGITransport(app=create_app(running_container)),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        yield client


async def test_the_journal_api(running_container: Container, api: AsyncClient) -> None:
    goal = await running_container.goals.create(COMPILER)
    added = await api.post(
        f"/v1/goals/{goal.id}/notes", json={"kind": "blocker", "text": "Waiting on LLVM 19."}
    )
    assert added.status_code == 201 and added.json()["source"] == "user"
    listed = (await api.get(f"/v1/goals/{goal.id}/notes")).json()
    assert [n["text"] for n in listed] == ["Waiting on LLVM 19."]
    assert (await api.get("/v1/goals/goal_nope/notes")).status_code == 404
    bad = await api.post(f"/v1/goals/{goal.id}/notes", json={"kind": "gossip", "text": "x"})
    assert bad.status_code == 422


async def test_a_goal_named_by_its_words_instead_of_its_label_is_still_found(
    running_container: Container, llm_provider: RecordedProvider
) -> None:
    """Measured live: the model writes the description, not "G1" — and progress was lost."""
    goal = await running_container.goals.create(COMPILER)
    await running_container.goals.create("Help the user plan the garden")
    llm_provider.script(
        "reflect.session",
        json_response(
            {
                "worth_remembering": True,
                "title": "Compiler",
                "summary": "The user is stuck on generics.",
                "facts": [],
                "progress": [{"goal": COMPILER, "progress": 0.3}],
                "journal": [
                    {"goal": COMPILER, "kind": "blocker", "text": "Generics are unsolved."},
                    {
                        "goal": "Help the",
                        "kind": "question",
                        "text": "Ambiguous: two goals start so.",
                    },
                ],
            }
        ),
    )
    for line in ("Generics are killing me.", "Still stuck on them."):
        await running_container.brain.run_turn(line, session_id="s")
    await running_container.bus.drain()

    await _reflector(running_container).reflect("s")

    notes = [(n.kind.value, n.text) for n in reversed(await running_container.goals.notes(goal.id))]
    assert notes == [("progress", "progress 0% → 30%"), ("blocker", "Generics are unsolved.")]
