"""What every reflection tier shares: asking the utility model, and stepping aside for the user.

T2 (nightly), T3 (weekly) and T4 (monthly) all run in the background, all ask the utility model
for structured output at temperature 0, and all yield to a conversation in progress (§5.1).
One copy of each, so a tier cannot drift from the others.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any, Protocol

from hedwig.core.errors import HedwigError
from hedwig.core.logging import fields, get_logger
from hedwig.core.ports import Clock, ConversationHistory
from hedwig.core.ports.llm import (
    GenerationRequest,
    GenerationResult,
    Message,
    RequestPriority,
    Role,
)

logger = get_logger(__name__)

ACTIVE_WINDOW = timedelta(minutes=2)
"""A user who spoke within this long is still talking; reflection waits for them."""


class Generator(Protocol):
    async def generate(self, request: GenerationRequest) -> GenerationResult: ...


async def ask_structured(
    gateway: Generator,
    purpose: str,
    instructions: str,
    content: str,
    schema: Mapping[str, Any],
    *,
    max_tokens: int = 900,
) -> dict[str, Any] | None:
    """The model's parsed answer, or `None` when it is unavailable — reflection then waits."""
    request = GenerationRequest(
        messages=(
            Message(role=Role.SYSTEM, content=instructions),
            Message(role=Role.USER, content=content),
        ),
        purpose=purpose,
        json_schema=schema,
        max_tokens=max_tokens,
        temperature=0.0,
        priority=RequestPriority.BACKGROUND,
    )
    try:
        result = await gateway.generate(request)
    except HedwigError as error:
        logger.warning(
            "reflection waits for the model", extra=fields(purpose=purpose, error=str(error))
        )
        return None
    return result.parsed if isinstance(result.parsed, dict) else None


async def user_is_talking(history: ConversationHistory, clock: Clock) -> bool:
    recent = await history.list_sessions(limit=1)
    if not recent or recent[0].ended_at is not None:
        return False
    return clock.now() - datetime.fromisoformat(recent[0].last_active_at) < ACTIVE_WINDOW
