"""Versioned route modules. Everything lives under `/v1` (docs/16 §2)."""

from __future__ import annotations

from fastapi import APIRouter

from hedwig.api.routes import health, mind

v1 = APIRouter(prefix="/v1")
v1.include_router(health.router)
v1.include_router(mind.router)

__all__ = ["v1"]
