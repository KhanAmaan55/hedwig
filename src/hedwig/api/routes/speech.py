"""Speech (docs/15 §13). The reply's words are user content, so behind the token."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel, Field

from hedwig.api.auth import require_token
from hedwig.api.deps import ContainerDep
from hedwig.speech import length_scale, speakable

router = APIRouter(prefix="/speech", tags=["speech"], dependencies=[Depends(require_token)])


class SpeechRead(BaseModel):
    available: bool
    engine: str
    voice: str
    reason: str


class SpeechCreate(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


@router.get("", response_model=SpeechRead, summary="Whether HEDWIG can speak, and with what voice")
async def status(container: ContainerDep) -> SpeechRead:
    current = await container.speaker.status()
    return SpeechRead(
        available=current.available,
        engine=current.engine,
        voice=current.voice,
        reason=current.reason,
    )


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
