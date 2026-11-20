"""The built-in, read-only tools (docs/13 §5.1, §12)."""

from __future__ import annotations

import ast
import operator
from collections.abc import Callable, Mapping
from datetime import tzinfo
from typing import Any
from zoneinfo import ZoneInfo

from hedwig.core.ports import Clock
from hedwig.core.ports.brain import TrustTier
from hedwig.core.ports.memory import RetrievalPolicy
from hedwig.memory.retrieval import HybridRetrieval
from hedwig.tools.registry import Tool, ToolError

MAX_EXPONENT = 64
MAX_MAGNITUDE = 10**30

_BINARY: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY: dict[type[ast.unaryop], Callable[[Any], Any]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def evaluate(expression: str) -> float | int:
    """Arithmetic only, by walking the AST. Never `eval` (docs/13 §12)."""
    try:
        tree = ast.parse(
            expression.replace("^", "**")
            .replace("\N{MULTIPLICATION SIGN}", "*")
            .replace("\N{DIVISION SIGN}", "/"),
            mode="eval",
        )
    except SyntaxError as error:
        raise ToolError(f"not an arithmetic expression: {expression!r}") from error

    def walk(node: ast.AST) -> float | int:
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
            if isinstance(node.value, bool):
                raise ToolError("only numbers are allowed")
            return node.value
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
            return _UNARY[type(node.op)](walk(node.operand))  # type: ignore[no-any-return]
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
            left, right = walk(node.left), walk(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > MAX_EXPONENT:
                raise ToolError("that exponent is too large to compute safely")
            try:
                value = _BINARY[type(node.op)](left, right)
            except ZeroDivisionError as error:
                raise ToolError("division by zero") from error
            if abs(value) > MAX_MAGNITUDE:
                raise ToolError("the result is too large")
            return value  # type: ignore[no-any-return]
        raise ToolError("only numbers and + - * / // % ** are allowed")

    return walk(tree)


def calculate_tool() -> Tool:
    async def run(arguments: Mapping[str, Any]) -> str:
        expression = str(arguments["expression"])
        value = evaluate(expression)
        shown = int(value) if isinstance(value, float) and value.is_integer() else value
        if isinstance(shown, float):
            shown = round(shown, 10)
        return f"{expression} = {shown}"

    return Tool(
        name="calculate",
        description="Evaluate an arithmetic expression exactly.",
        schema={
            "type": "object",
            "properties": {"expression": {"type": "string", "minLength": 1, "maxLength": 200}},
            "required": ["expression"],
            "additionalProperties": False,
        },
        run=run,
        timeout_seconds=2.0,
    )


def datetime_tool(clock: Clock, *, timezone: str = "") -> Tool:
    zone: tzinfo | None = ZoneInfo(timezone) if timezone else None

    async def run(arguments: Mapping[str, Any]) -> str:
        now = clock.now()
        local = now.astimezone(zone) if zone else now.astimezone()
        label = timezone or local.tzname() or "local time"
        return f"It is {local.strftime('%A %d %B %Y, %H:%M')} ({label})."

    return Tool(
        name="get_datetime",
        description="The current date and time where the user is.",
        schema={"type": "object", "properties": {}, "additionalProperties": False},
        run=run,
        timeout_seconds=1.0,
    )


def search_memory_tool(retrieval: HybridRetrieval) -> Tool:
    async def run(arguments: Mapping[str, Any]) -> str:
        found = await retrieval.search(
            [str(arguments["query"])], policy=RetrievalPolicy(max_items=5)
        )
        if not found.items:
            return "Nothing in memory matches."
        return "\n".join(
            f"- ({item.occurred_at.date().isoformat()}) {item.text}" for item in found.items
        )

    return Tool(
        name="search_memory",
        description="Search HEDWIG's own memory of past conversations.",
        schema={
            "type": "object",
            "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 500}},
            "required": ["query"],
            "additionalProperties": False,
        },
        run=run,
        trust=TrustTier.SELF,
        timeout_seconds=5.0,
    )
