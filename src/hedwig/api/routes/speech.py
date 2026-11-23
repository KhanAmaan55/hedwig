"""Speech (docs/15 §13). Replies spoken, and the user heard (VOI-02). User content: behind the token."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from hedwig.api.auth import require_token
from hedwig.api.deps import ContainerDep
from hedwig.core.errors import InvalidRequestError
from hedwig.speech import length_scale, speakable

router = APIRouter(prefix="/speech", tags=["speech"], dependencies=[Depends(require_token)])


class SpeechRead(BaseModel):
    available: bool
    engine: str
    voice: str
    reason: str
    listening: bool = False
    """Whether HEDWIG can also hear (VOI-02): a speech-recognition model is installed."""
    listening_reason: str = ""


class TranscriptRead(BaseModel):
    text: str
    seconds: float


MAX_AUDIO_BYTES = 20_000_000


class SpeechCreate(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


@router.get("", response_model=SpeechRead, summary="Whether HEDWIG can speak, and with what voice")
async def status(container: ContainerDep) -> SpeechRead:
    current = await container.speaker.status()
    hearing = await container.listener.status()
    return SpeechRead(
        available=current.available,
        engine=current.engine,
        voice=current.voice,
        reason=current.reason,
        listening=hearing.available,
        listening_reason=hearing.reason,
    )


@router.post(
    "/transcribe",
    response_model=TranscriptRead,
    summary="What was said in this recording (audio/webm, wav, m4a…)",
    responses={503: {"description": "No speech-recognition model installed"}},
)
async def transcribe(request: Request, container: ContainerDep) -> TranscriptRead:
    """Transcribed on this machine; the audio is not stored. The text is returned for the user
    to send or discard — it becomes a message only if they send it."""
    audio = await request.body()
    if not audio:
        raise InvalidRequestError("No audio was sent.")
    if len(audio) > MAX_AUDIO_BYTES:
        raise InvalidRequestError("That recording is too large.")
    heard = await container.listener.transcribe(audio)
    return TranscriptRead(text=heard.text, seconds=heard.seconds)


@router.post(
    "",
    response_class=Response,
    responses={200: {"content": {"audio/wav": {}}}, 503: {"description": "No voice installed"}},
    summary="Say this, as audio/wav",
)
async def speak(body: SpeechCreate, container: ContainerDep) -> Response:
    state = await container.emotion.state() if container.emotion is not None else None
    audio = await container.speaker.synthesize(
        speakable(body.text), length_scale=length_scale(state)
    )
    return Response(content=audio, media_type="audio/wav")
