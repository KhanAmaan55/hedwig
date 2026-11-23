"""The conversation surface (docs/16 §4.1, §12).

The first routes that carry what the user said, so the first behind the token (ADR-0022).
Non-streaming: `POST …/messages` returns the complete reply. The WebSocket in docs/16 §5 is
the primary path once it exists (FND-05); this one stays as the simple, scriptable path.

No domain logic here (docs/16 §2): the brain opens sessions and runs turns, the history port
reads the log back. This module validates, translates and maps outcomes to status codes.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field, field_validator

from hedwig.api.auth import require_token
from hedwig.api.deps import ContainerDep
from hedwig.core.context import current_correlation_id
from hedwig.core.errors import CapabilityUnavailableError, NotFoundError
from hedwig.core.ports import SessionInfo

MAX_MESSAGE_CHARS = 32_768
"""docs/16 §7: message text caps at 32 KB."""

router = APIRouter(
    prefix="/sessions",
    tags=["conversation"],
    dependencies=[Depends(require_token)],
)


# -- schemas ---------------------------------------------------------------


class SessionCreate(BaseModel):
    channel: Literal["cli", "web", "api"] = "api"
    """Where the conversation is happening. `internal` is reserved for HEDWIG's own sessions."""


class SessionRead(BaseModel):
    id: str
    channel: str
    started_at: str
    ended_at: str | None
    end_reason: str | None
    turn_count: int
    last_active_at: str
    is_open: bool


class SessionList(BaseModel):
    sessions: list[SessionRead]


class MessageCreate(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    client_message_id: str | None = Field(
        default=None,
        max_length=100,
        description="Your id for this message. Sending it again returns the same turn (FND-06).",
    )

    @field_validator("text")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must contain something other than whitespace")
        return value


class TurnReply(BaseModel):
    """One completed turn, as the client needs it."""

    turn_id: str
    session_id: str
    user_message_id: str
    message_id: str = Field(description="The reply's message id; what feedback and /explain take.")
    reply: str
    status: Literal["completed", "refused", "truncated"]
    intent: str | None
    cited_memory_ids: list[str] = Field(description="Memories shown to the model for this reply.")
    mood: dict[str, float] = Field(description="The emotional state the reply was composed under.")


class MessageRead(BaseModel):
    id: str
    seq: int
    role: str
    text: str
    created_at: str


class MessagePage(BaseModel):
    messages: list[MessageRead] = Field(description="Oldest first.")
    next_before: str | None = Field(
        description="Pass as `before` for the next, older page. Null when there is none."
    )


# -- routes ----------------------------------------------------------------


@router.post(
    "",
    response_model=SessionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Start a conversation",
)
async def open_session(container: ContainerDep, body: SessionCreate | None = None) -> SessionRead:
    session_id = await container.brain.open_session(channel=(body or SessionCreate()).channel)
    return _session(await _require_session(container, session_id))


@router.get("", response_model=SessionList, summary="Recent conversations")
async def list_sessions(
    container: ContainerDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> SessionList:
    sessions = await container.history.list_sessions(limit=limit)
    return SessionList(sessions=[_session(info) for info in sessions])


@router.get("/{session_id}", response_model=SessionRead, summary="One conversation")
async def get_session(session_id: str, container: ContainerDep) -> SessionRead:
    return _session(await _require_session(container, session_id))


@router.post(
    "/{session_id}/messages",
    response_model=TurnReply,
    summary="Say something and receive the complete reply",
    responses={
        404: {"description": "No such session"},
        503: {"description": "The language model is unavailable; `detail.reply` says so"},
    },
)
async def send_message(session_id: str, body: MessageCreate, container: ContainerDep) -> TurnReply:
    await _require_session(container, session_id)

    final = await container.brain.run_turn(
        body.text,
        session_id=session_id,
        correlation_id=current_correlation_id(),
        client_message_id=body.client_message_id,
    )
    return turn_reply(final, session_id=session_id)


def turn_reply(final: Mapping[str, Any], *, session_id: str) -> TurnReply:
    """The reply a client receives, from a finished turn. Shared by REST and the stream.

    Raises the typed outage error for a failed turn, carrying HEDWIG's own honest sentence
    (docs/16 §12.3).
    """
    turn_status = final.get("status", "failed")
    reply = final.get("reply", "")

    if turn_status not in ("completed", "refused", "truncated"):
        raise CapabilityUnavailableError(
            "HEDWIG could not compose a reply: the language model is unavailable.",
            turn_id=final.get("turn_id"),
            session_id=session_id,
            status=turn_status,
            reply=reply,
        )

    context = final.get("response_context")
    mind = final.get("mind")
    plan = final.get("plan")
    intent = plan.intent if plan is not None else None
    return TurnReply(
        turn_id=str(final.get("turn_id")),
        session_id=session_id,
        user_message_id=str(final.get("user_message_id")),
        message_id=str(final.get("reply_message_id")),
        reply=reply,
        status=turn_status,
        intent=intent.value if intent is not None else None,
        cited_memory_ids=list(context.cited_memory_ids) if context is not None else [],
        mood=dict(mind.mood) if mind is not None else {},
    )


@router.post(
    "/{session_id}/end",
    response_model=SessionRead,
    summary="End a conversation now",
    responses={404: {"description": "No such session"}},
)
async def end_session(session_id: str, container: ContainerDep) -> SessionRead:
    """Applies the relationship update at once rather than after the idle timeout (FND-03).

    Waits for a turn in flight on this session. Idempotent: ending an ended session returns it
    unchanged. A later message resumes it as a new conversation (docs/27 §5.1).
    """
    await _require_session(container, session_id)
    await container.brain.end_session(session_id)
    return _session(await _require_session(container, session_id))


@router.get(
    "/{session_id}/messages",
    response_model=MessagePage,
    summary="The conversation log, paginated backwards",
)
async def history(
    session_id: str,
    container: ContainerDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    before: Annotated[
        str | None, Query(description="A message id; returns older messages.")
    ] = None,
) -> MessagePage:
    await _require_session(container, session_id)
    messages = await container.history.history(session_id, limit=limit, before=before)
    return MessagePage(
        messages=[
            MessageRead(
                id=message.id,
                seq=message.seq,
                role=message.role,
                text=message.text,
                created_at=message.created_at,
            )
            for message in messages
        ],
        # A full page may have more behind it; a short one cannot.
        next_before=messages[0].id if len(messages) == limit and messages[0].seq > 1 else None,
    )


# -- helpers ---------------------------------------------------------------


async def _require_session(container: ContainerDep, session_id: str) -> SessionInfo:
    info = await container.history.get_session(session_id)
    if info is None:
        raise NotFoundError("No such session.", session_id=session_id)
    return info


def _session(info: SessionInfo) -> SessionRead:
    return SessionRead(
        id=info.id,
        channel=info.channel,
        started_at=info.started_at,
        ended_at=info.ended_at,
        end_reason=info.end_reason,
        turn_count=info.turn_count,
        last_active_at=info.last_active_at,
        is_open=info.is_open,
    )
