"""Versioned route modules. Everything lives under `/v1` (docs/16 §2)."""

from __future__ import annotations

from fastapi import APIRouter

from hedwig.api.routes import (
    conversation,
    curiosity,
    egress,
    goals,
    health,
    memory,
    mind,
    persona,
    speech,
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

__all__ = ["v1"]
