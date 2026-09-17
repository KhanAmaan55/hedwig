from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from hedwig.api.app import create_app
from hedwig.core.clock import FakeClock
from hedwig.core.config import load_config
from hedwig.wiring import Container, build_container


async def test_health_reports_the_documented_shape(client: AsyncClient) -> None:
    response = await client.get("/v1/health")
    assert response.status_code == 200

    body = response.json()
    # Degraded, not ok: the brain reports its capabilities as stubbed, and a system that
    # cannot yet converse must say so rather than claim health (docs/19 §7).
    assert body["status"] == "degraded"
    assert body["subsystems"]["brain"]["status"] == "degraded"
    assert body["version"]
    assert body["environment"] == "test"
    assert body["started_at"].endswith("+00:00")
    assert body["uptime_s"] >= 0
    assert {"api", "data_dir", "database"} <= set(body["subsystems"])
    assert "schema_version" in body  # null until the store lands


async def test_health_uptime_follows_the_injected_clock(
    client: AsyncClient, clock: FakeClock
) -> None:
    clock.advance(hours=3)
    body = (await client.get("/v1/health")).json()
    assert body["uptime_s"] == 10800.0


async def test_health_degrades_when_the_data_dir_disappears(
    client: AsyncClient, container: Container
) -> None:
    """Construction creates the data directory, so this is a *runtime* guard: an unmounted
    volume or a deleted folder must show up as degraded rather than as silence."""
    shutil.rmtree(container.config.runtime.data_dir)

    response = await client.get("/v1/health")

    # Storage cannot serve a blob whose root has gone, so the system really is down and
    # says so with a 503 rather than reporting a cheerful 200.
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "down"
    assert body["subsystems"]["data_dir"]["status"] == "degraded"
    assert body["subsystems"]["storage"]["status"] == "down"
    assert any(notice["code"] == "data_dir_missing" for notice in body["notices"])


async def test_health_reports_every_registered_service(client: AsyncClient) -> None:
    body = (await client.get("/v1/health")).json()

    assert {"config", "bus", "state", "storage", "scheduler", "llm"} <= set(body["subsystems"])
    assert body["subsystems"]["bus"]["status"] == "ok"
    assert body["subsystems"]["llm"]["detail"]["models"]["utility"]


async def test_every_response_carries_a_correlation_id(client: AsyncClient) -> None:
    response = await client.get("/v1/health")
    assert response.headers["x-correlation-id"].startswith("req_")


async def test_a_supplied_correlation_id_is_honoured(client: AsyncClient) -> None:
    response = await client.get("/v1/health", headers={"X-Correlation-ID": "turn_01JQ8Z"})
    assert response.headers["x-correlation-id"] == "turn_01JQ8Z"


async def test_unknown_routes_use_the_error_envelope(client: AsyncClient) -> None:
    response = await client.get("/v1/nope")
    assert response.status_code == 404

    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert error["retryable"] is False
    assert error["correlation_id"].startswith("req_")


async def test_unhandled_exceptions_do_not_leak_internals(app: FastAPI) -> None:
    @app.get("/v1/_boom")
    async def _boom() -> None:
        raise RuntimeError("database password is hunter2")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/v1/_boom")

    assert response.status_code == 500
    body = response.text
    assert "hunter2" not in body
    assert response.json()["error"]["code"] == "internal"


async def test_cors_allows_the_dev_frontend(client: AsyncClient) -> None:
    response = await client.get("/v1/health", headers={"Origin": "http://localhost:5173"})
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


async def test_docs_are_available_in_development(tmp_path: Path) -> None:
    config = load_config(
        runtime={"environment": "development", "data_dir": str(tmp_path)},
        logging={"file_enabled": False},
    )
    app = create_app(build_container(config, clock=FakeClock()))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        assert (await client.get("/openapi.json")).status_code == 200
