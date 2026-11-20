"""Versioned route modules. Everything lives under `/v1` (docs/16 §2)."""

from __future__ import annotations

from fastapi import APIRouter

from hedwig.api.routes import (
    conversation,
    curiosity,
    egress,
    explain,
    goals,
    health,
    memory,
    mind,
    persona,
    privacy,
    reflection,
    speech,
    stream,
)

v1 = APIRouter(prefix="/v1")
v1.include_router(health.router)
v1.include_router(mind.router)
v1.include_router(persona.router)
v1.include_router(conversation.router)
v1.include_router(memory.router)
v1.include_router(goals.router)
v1.include_router(egress.router)
v1.include_router(curiosity.router)
v1.include_router(speech.router)
v1.include_router(stream.router)
v1.include_router(explain.router)
v1.include_router(reflection.router)
v1.include_router(privacy.router)

__all__ = ["v1"]
