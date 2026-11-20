"""Goals (GOA-01, GOA-02, docs/08 §6.1): the store, its lifecycle, the turn, reflection, API."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from typer.testing import CliRunner

from hedwig import cli
from hedwig.api.app import create_app
from hedwig.client import HedwigClient
from hedwig.core.clock import FakeClock
from hedwig.core.errors import InvalidRequestError
from hedwig.core.ports.goals import GoalKind, GoalStatus
from hedwig.core.token import TokenFile
from hedwig.llm.recorded import RecordedProvider, json_response
from hedwig.reflection import SessionReflector
from hedwig.wiring import Container

# =========================================================================
# The store and its lifecycle
# =========================================================================


async def test_a_goal_has_everything_the_vision_asks_for(container: Container) -> None:
    goal = await container.goals.create(
        "Help the user finish their Rust compiler",
        context="They are writing Fernweh in their evenings.",
        completion="Fernweh compiles its own test suite.",
        priority=0.8,
    )
    assert goal.status is GoalStatus.ACTIVE
    assert (goal.priority, goal.progress) == (0.8, 0.0)
    assert goal.context and goal.completion and goal.created_at


async def test_a_closed_goal_cannot_be_blocked_but_can_be_reopened(container: Container) -> None:
    goal = await container.goals.create("Learn Portuguese")
    await container.goals.update(goal.id, status=GoalStatus.DONE)

    with pytest.raises(InvalidRequestError):
        await container.goals.update(goal.id, status=GoalStatus.BLOCKED)
    reopened = await container.goals.update(goal.id, status=GoalStatus.ACTIVE)
    assert reopened.status is GoalStatus.ACTIVE


async def test_the_lifecycle_rules_keep_the_list_from_becoming_a_graveyard(
    container: Container, clock: FakeClock
) -> None:
    proposed = await container.goals.create("Read about memory", status=GoalStatus.PROPOSED)
    stale = await container.goals.create("Tidy the garage")
    lively = await container.goals.create("Finish the compiler")

    clock.advance(days=31)
    await container.goals.update(lively.id, progress=0.4)
    stats = await container.goals.sweep()

    assert (await container.goals.get(proposed.id)).status is GoalStatus.ABANDONED  # type: ignore[union-attr]
    assert (await container.goals.get(stale.id)).status is GoalStatus.BLOCKED  # type: ignore[union-attr]
    assert (await container.goals.get(lively.id)).status is GoalStatus.ACTIVE  # type: ignore[union-attr]
    assert stats["expired"] == 1 and stats["blocked"] == 1
    assert await container.goals.sweep() == {"expired": 0, "blocked": 0, "capped": 0}


async def test_at_most_twelve_are_active_and_the_least_important_give_way(
    container: Container,
) -> None:
    for index in range(14):
        await container.goals.create(f"goal number {index}", priority=index / 20)

    active = await container.goals.active(limit=50)

    assert len(active) == 12
    assert "goal number 0" not in {goal.description for goal in active}


# =========================================================================
# Goals in the turn
# =========================================================================


async def test_active_goals_reach_the_prompt_with_the_request_first(
    running_container: Container, llm_provider: RecordedProvider
) -> None:
    await running_container.goals.create(
        "Help the user finish their Rust compiler", completion="it compiles itself"
    )
    await running_container.goals.create("Not yet agreed", status=GoalStatus.PROPOSED)

    await running_container.brain.run_turn("What should I do this evening?", session_id="s")

    system = llm_provider.calls[-1].messages[0].content
    block = system.split("[goals", 1)[1]
    assert "otherwise just answer their message" in block
    assert "Help the user finish their Rust compiler; done when: it compiles itself" in block
    assert "Not yet agreed" not in system, "a proposal reached the prompt before the user agreed"


# =========================================================================
# Reflection proposes and progresses
# =========================================================================


def _reflector(container: Container) -> SessionReflector:
    service = container.registry.get("reflection")
    assert isinstance(service, SessionReflector)
    return service


async def test_reflection_proposes_a_project_and_moves_an_active_goal(
    running_container: Container, llm_provider: RecordedProvider
) -> None:
    compiler = await running_container.goals.create("Help the user finish their Rust compiler")
    llm_provider.script(
        "reflect.session",
        json_response(
            {
                "worth_remembering": True,
                "title": "Compiler and Portuguese",
                "summary": "The user got the parser working and started learning Portuguese.",
                "facts": [],
                "projects": [
                    {"goal": "Help the user learn Portuguese", "done_when": "basic conversation"},
                    {"goal": "Help the user finish their Rust compiler"},  # already a goal
                ],
                "progress": [{"goal": "G1", "progress": 0.4}],
            }
        ),
    )
    for line in ("The parser for my compiler finally works!", "Also I started Portuguese."):
        await running_container.brain.run_turn(line, session_id="s")
    await running_container.bus.drain()

    result = await _reflector(running_container).reflect("s")

    assert len(result.proposed_goals) == 1, "the existing goal was proposed again"
    proposal = await running_container.goals.get(result.proposed_goals[0])
    assert proposal is not None
    assert proposal.status is GoalStatus.PROPOSED
    assert proposal.kind is GoalKind.SELF_GENERATED and proposal.origin == "reflection"
    assert (await running_container.goals.get(compiler.id)).progress == 0.4  # type: ignore[union-attr]
    request = next(c for c in llm_provider.calls if c.purpose == "reflect.session")
    assert "G1: Help the user finish their Rust compiler" in request.messages[-1].content


async def test_reflection_never_moves_progress_backwards(
    running_container: Container, llm_provider: RecordedProvider
) -> None:
    goal = await running_container.goals.create("Ship HEDWIG")
    await running_container.goals.update(goal.id, progress=0.7)
    llm_provider.script(
        "reflect.session",
        json_response(
            {
                "worth_remembering": True,
                "title": "t",
                "summary": "Talked about shipping.",
                "facts": [],
                "progress": [{"goal": "G1", "progress": 0.2}],
            }
        ),
    )
    for line in ("Shipping is going okay I think.", "Still a few bugs to fix."):
        await running_container.brain.run_turn(line, session_id="s")
    await running_container.bus.drain()

    await _reflector(running_container).reflect("s")

    assert (await running_container.goals.get(goal.id)).progress == 0.7  # type: ignore[union-attr]


# =========================================================================
# API and CLI
# =========================================================================


@pytest.fixture
async def api(running_container: Container) -> AsyncIterator[AsyncClient]:
    token = TokenFile(running_container.config.runtime.token_path).ensure()
    async with AsyncClient(
        transport=ASGITransport(app=create_app(running_container)),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        yield client


async def test_the_goals_api(api: AsyncClient) -> None:
    created = await api.post(
        "/v1/goals", json={"description": "Learn to sail", "completion": "solo across the bay"}
    )
    assert created.status_code == 201
    goal_id = created.json()["id"]

    progressed = await api.patch(f"/v1/goals/{goal_id}", json={"progress": 0.5})
    assert progressed.json()["progress"] == 0.5
    done = await api.patch(f"/v1/goals/{goal_id}", json={"status": "done"})
    assert done.json()["status"] == "done"
    refused = await api.patch(f"/v1/goals/{goal_id}", json={"status": "blocked"})
    assert refused.status_code == 400

    open_only = (await api.get("/v1/goals", params={"status": "active"})).json()["goals"]
    assert open_only == []
    assert (await api.get("/v1/goals/goal_nope")).status_code == 404


@pytest.fixture
def http(container: Container) -> Iterator[TestClient]:
    with TestClient(create_app(container)) as client:
        yield client


def test_hedwig_goals_commands(
    http: TestClient, container: Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = TokenFile(container.config.runtime.token_path).read()
    assert token
    monkeypatch.setattr(cli, "_api", lambda url=None: HedwigClient("http://t", token, http=http))
    runner = CliRunner()

    added = runner.invoke(
        cli.app, ["goals", "add", "Finish the compiler", "--done-when", "it ships"]
    )
    assert added.exit_code == 0, added.output
    goal_id = added.stdout.split()[0]

    assert "40%" in runner.invoke(cli.app, ["goals", "progress", goal_id, "0.4"]).stdout
    assert "done" in runner.invoke(cli.app, ["goals", "done", goal_id]).stdout
    assert "No goals yet" in runner.invoke(cli.app, ["goals", "list"]).stdout
    assert goal_id in runner.invoke(cli.app, ["goals", "list", "--all"]).stdout
