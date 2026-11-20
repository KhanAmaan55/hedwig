"""The tool layer (COG-02, docs/13 §12)."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import pytest

from hedwig.core.clock import FakeClock
from hedwig.core.ports.brain import ToolRequest
from hedwig.core.store import Database
from hedwig.tools import Tool, ToolError, ToolRegistry, calculate_tool, datetime_tool, evaluate


@pytest.fixture
def registry(database: Database, clock: FakeClock) -> ToolRegistry:
    tools = ToolRegistry(database=database, clock=clock)
    tools.register(calculate_tool())
    tools.register(datetime_tool(clock, timezone="Europe/Lisbon"))
    return tools


def _audit(database: Database) -> list[dict[str, Any]]:
    return [dict(row) for row in database.query("SELECT * FROM tool_call ORDER BY created_at")]


@pytest.mark.parametrize(
    ("expression", "value"),
    [
        ("2 + 2", 4),
        ("(3 + 4) * 5", 35),
        ("2 ** 10", 1024),
        ("7 / 2", 3.5),
        ("-3 + 1", -2),
        ("10 % 4", 2),
        ("2^3", 8),
    ],
)
def test_arithmetic_is_exact(expression: str, value: float) -> None:
    assert evaluate(expression) == value


@pytest.mark.parametrize(
    "hostile",
    [
        "__import__('os').system('true')",
        "open('/etc/passwd').read()",
        "(lambda: 1)()",
        "[1, 2][0]",
        "True + 1",
        "9 ** 9 ** 9",
        "10 ** 100",
        "1 / 0",
    ],
)
def test_anything_but_bounded_arithmetic_is_refused(hostile: str) -> None:
    """`calculate` walks the AST; it never evaluates code (docs/13 §12)."""
    with pytest.raises(ToolError):
        evaluate(hostile)


async def test_a_good_call_answers_and_is_audited(
    registry: ToolRegistry, database: Database
) -> None:
    outcome = await registry.run(ToolRequest(name="calculate", arguments={"expression": "6 * 7"}))

    assert outcome.ok and outcome.output == "6 * 7 = 42"
    [row] = _audit(database)
    assert row["tool_name"] == "calculate" and row["status"] == "ok"
    assert '"6 * 7"' in row["args"]


async def test_the_clock_tool_reads_the_injected_clock_in_the_users_zone(
    registry: ToolRegistry,
) -> None:
    outcome = await registry.run(ToolRequest(name="get_datetime"))
    # FakeClock starts 2026-01-01T09:00Z; Lisbon is UTC+0 in winter.
    assert outcome.output == "It is Thursday 01 January 2026, 09:00 (Europe/Lisbon)."


@pytest.mark.parametrize(
    ("request_", "status"),
    [
        (ToolRequest(name="calculate", arguments={}), "invalid"),
        (ToolRequest(name="calculate", arguments={"expression": "1", "extra": 2}), "invalid"),
        (ToolRequest(name="launch_rockets"), "invalid"),
        (ToolRequest(name="calculate", arguments={"expression": "1/0"}), "failed"),
    ],
)
async def test_failures_are_outcomes_not_exceptions(
    registry: ToolRegistry, database: Database, request_: ToolRequest, status: str
) -> None:
    outcome = await registry.run(request_)
    assert not outcome.ok and outcome.error
    assert _audit(database)[-1]["status"] == status


async def test_a_tool_that_hangs_is_cut_off(registry: ToolRegistry, database: Database) -> None:
    async def hang(_: Mapping[str, Any]) -> str:
        await asyncio.sleep(10)
        return "never"

    registry.register(
        Tool(name="slow", description="", schema={"type": "object"}, run=hang, timeout_seconds=0.01)
    )
    outcome = await registry.run(ToolRequest(name="slow"))
    assert not outcome.ok and "longer" in (outcome.error or "")
    assert _audit(database)[-1]["status"] == "timeout"


async def test_a_tool_that_crashes_does_not_crash_the_turn(registry: ToolRegistry) -> None:
    async def broken(_: Mapping[str, Any]) -> str:
        raise RuntimeError("bug")

    registry.register(Tool(name="broken", description="", schema={"type": "object"}, run=broken))
    outcome = await registry.run(ToolRequest(name="broken"))
    assert not outcome.ok and "RuntimeError" in (outcome.error or "")
