"""The tool registry: the brain's `ToolRunner` (docs/13 §5, §12).

Validates arguments against each tool's JSON Schema, runs it under a timeout, and writes every
call to `tool_call`. Failures become outcomes, never exceptions (docs/07 §8).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import jsonschema

from hedwig.core.context import current_correlation_id
from hedwig.core.ids import new_id
from hedwig.core.logging import fields, get_logger
from hedwig.core.ports import Clock
from hedwig.core.ports.brain import ToolOutcome, ToolRequest, TrustTier
from hedwig.core.store import Database

logger = get_logger(__name__)

ToolFunction = Callable[[Mapping[str, Any]], Awaitable[str]]


class ToolError(Exception):
    """A tool's own, expected failure — reported to the model as the error text."""


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    description: str
    schema: Mapping[str, Any]
    run: ToolFunction
    side_effects: str = "none"
    """`none`, `local_write`, `network` (docs/13 §5.1)."""
    trust: TrustTier = TrustTier.TOOL
    timeout_seconds: float = 10.0
    requires_approval: bool = False


@dataclass(slots=True)
class ToolRegistry:
    database: Database
    clock: Clock
    tools: dict[str, Tool] = field(default_factory=dict)

    def register(self, tool: Tool) -> None:
        if tool.name in self.tools:
            raise ValueError(f"tool {tool.name!r} is already registered")
        jsonschema.Draft202012Validator.check_schema(dict(tool.schema))
        self.tools[tool.name] = tool

    def names(self) -> Sequence[str]:
        return tuple(self.tools)

    def requires_approval(self, name: str) -> bool:
        tool = self.tools.get(name)
        return bool(tool and tool.requires_approval)

    async def run(self, request: ToolRequest) -> ToolOutcome:
        started = time.monotonic()
        tool = self.tools.get(request.name)
        arguments = dict(request.arguments)

        def outcome(
            ok: bool, *, status: str, output: str = "", error: str | None = None
        ) -> ToolOutcome:
            elapsed = round((time.monotonic() - started) * 1000, 3)
            self._audit(
                request.name, arguments, status=status, output=output, error=error, ms=elapsed
            )
            return ToolOutcome(
                name=request.name,
                ok=ok,
                output=output,
                error=error,
                duration_ms=elapsed,
                trust=tool.trust if tool else TrustTier.TOOL,
            )

        if tool is None:
            return outcome(False, status="invalid", error=f"no tool called {request.name!r}")
        try:
            jsonschema.validate(arguments, dict(tool.schema))
        except jsonschema.ValidationError as error:
            return outcome(False, status="invalid", error=f"invalid arguments: {error.message}")

        try:
            output = await asyncio.wait_for(tool.run(arguments), timeout=tool.timeout_seconds)
        except TimeoutError:
            return outcome(
                False, status="timeout", error=f"took longer than {tool.timeout_seconds:g}s"
            )
        except ToolError as error:
            return outcome(False, status="failed", error=str(error))
        except Exception as error:  # a tool bug must not become a failed turn
            logger.exception("tool crashed", extra=fields(tool=request.name))
            return outcome(False, status="failed", error=f"{type(error).__name__}: {error}")
        return outcome(True, status="ok", output=output)

    def _audit(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        status: str,
        output: str,
        error: str | None,
        ms: float,
    ) -> None:
        try:
            self.database.execute(
                "INSERT INTO tool_call (id, tool_name, args, result_digest, status, error, "
                "duration_ms, correlation_id, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    new_id("tc"),
                    name,
                    json.dumps(dict(arguments), default=str)[:4000],
                    hashlib.sha256(output.encode()).hexdigest()[:16] if output else None,
                    status,
                    error,
                    int(ms),
                    current_correlation_id() or "none",
                    self.clock.now().isoformat(timespec="milliseconds"),
                ),
            )
        except Exception:  # the audit must never be what breaks a turn
            logger.exception("could not record tool call", extra=fields(tool=name))
