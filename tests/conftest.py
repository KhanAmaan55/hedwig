"""Shared test fixtures.

Every test above the unit layer builds its system through `build_container`, with a
`FakeClock`, an in-memory database and a temporary data directory. No patching, no
monkeypatching of time, no global state — that is the payoff for the composition root in
`wiring.py` (docs/20 §3.4).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from hedwig.api.app import create_app
from hedwig.core.bus import InProcessBus, platform_catalogue
from hedwig.core.clock import FakeClock
from hedwig.core.config import Config, load_config
from hedwig.core.registry import InMemoryServiceRegistry
from hedwig.core.state import SqliteStateManager
from hedwig.core.storage import ContentAddressedStorage
from hedwig.core.store import Database, MigrationRunner
from hedwig.wiring import MIGRATIONS_DIR, Container, build_container


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock("2026-01-01T09:00:00+00:00")


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "hedwig-data"
    directory.mkdir(parents=True)
    return directory


@pytest.fixture
def config(data_dir: Path) -> Config:
    return load_config(
        runtime={"environment": "test", "data_dir": str(data_dir)},
        api={"host": "127.0.0.1", "port": 8731},
        logging={"level": "debug", "format": "json", "file_enabled": False},
        scheduler={"tick_seconds": 0.01},
        # The scripted provider, so the suite is hermetic: no daemon, no 8 GB model, and
        # the same answers on every machine (docs/20 §3.2).
        llm={"provider": "recorded", "retry_base_delay_seconds": 0.0},
    )


@pytest.fixture
def database(clock: FakeClock) -> Iterator[Database]:
    """A migrated, in-memory database.

    In-memory keeps the suite fast and hermetic; the migrations are the real ones, so the
    schema under test is the schema that ships.
    """
    db = Database(":memory:")
    MigrationRunner(db, MIGRATIONS_DIR, clock=clock).run()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
async def bus(database: Database, clock: FakeClock) -> AsyncIterator[InProcessBus]:
    instance = InProcessBus(database, clock=clock, catalogue=platform_catalogue())
    await instance.start()
    try:
        yield instance
    finally:
        await instance.stop()


@pytest.fixture
def registry() -> InMemoryServiceRegistry:
    return InMemoryServiceRegistry()


@pytest.fixture
def state(database: Database, clock: FakeClock) -> SqliteStateManager:
    return SqliteStateManager(database, clock=clock)


@pytest.fixture
def storage(database: Database, clock: FakeClock, tmp_path: Path) -> ContentAddressedStorage:
    return ContentAddressedStorage(tmp_path / "blobs", database, clock=clock)


@pytest.fixture
def container(config: Config, clock: FakeClock, database: Database) -> Container:
    """A fully wired but **not started** container.

    Construction and lifecycle are separate (docs/03 §6), which is exactly what lets a test
    inspect the graph without running it.
    """
    return build_container(config, clock=clock, database=database, migrate=False)


@pytest.fixture
async def running_container(container: Container) -> AsyncIterator[Container]:
    await container.start()
    try:
        yield container
    finally:
        await container.stop()


@pytest.fixture
def app(container: Container) -> FastAPI:
    return create_app(container)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Stop a developer's own HEDWIG_* variables from changing test outcomes."""
    for key in [key for key in os.environ if key.startswith("HEDWIG_")]:
        monkeypatch.delenv(key, raising=False)
    yield
