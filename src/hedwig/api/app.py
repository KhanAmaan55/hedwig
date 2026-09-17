"""FastAPI application factory.

Two entry points:

* `create_app(container)` — used by tests and by anything that builds its own container.
* `create_app_from_env()` — the import string uvicorn uses, including under `--reload`,
  where the process is re-created on every code change and must rebuild everything.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from hedwig.api.errors import install_error_handlers
from hedwig.api.middleware import CorrelationMiddleware
from hedwig.api.routes import v1
from hedwig.core.logging import fields, get_logger, setup_logging
from hedwig.wiring import Container, build_container

logger = get_logger(__name__)

DESCRIPTION = """
HEDWIG's local API. Loopback only.

Milestone 1 exposes system endpoints. Conversation, memory, mind state and the WebSocket
stream arrive in later milestones; see `docs/16-api-structure.md` for the full surface.
""".strip()


def create_app(container: Container) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        config = container.config
        await container.start()
        logger.info(
            "hedwig started",
            extra=fields(
                version=container.version,
                environment=config.runtime.environment,
                bind=f"{config.api.host}:{config.api.port}",
                data_dir=str(config.runtime.data_dir),
                services=list(container.registry.names()),
            ),
        )
        try:
            yield
        finally:
            # Drains the bus, stops the scheduler and closes the database, in reverse
            # dependency order (docs/17 §4).
            await container.stop()
            logger.info("hedwig stopped", extra=fields(uptime_s=round(container.uptime_seconds())))

    app = FastAPI(
        title="HEDWIG",
        version=container.version,
        description=DESCRIPTION,
        lifespan=lifespan,
        docs_url="/docs" if container.config.is_development else None,
        redoc_url=None,
        openapi_url="/openapi.json" if container.config.is_development else None,
    )
    app.state.container = container

    # Outermost first: correlation must wrap everything, including CORS failures.
    app.add_middleware(CorrelationMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(container.config.api.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-Correlation-ID"],
        expose_headers=["X-Correlation-ID"],
    )

    install_error_handlers(app)
    app.include_router(v1)
    return app


def create_app_from_env() -> FastAPI:
    """Build configuration, logging and the container from the environment, then the app.

    This is the uvicorn factory target. It is also the only place logging is configured
    for a served process, so a `--reload` restart reconfigures cleanly.
    """
    container = build_container()
    container.config.ensure_directories()
    setup_logging(container.config.logging, container.config.runtime)
    return create_app(container)
