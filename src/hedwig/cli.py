"""The `hedwig` command line.

A first-class client, not a debugging afterthought (docs/17 §9). Building it first is what
forces the API to be complete before any UI exists.

Milestone 1 provides the commands that exist without a database: `serve`, `config`,
`version`. `chat`, `memory`, `mind`, `reflect` and `db` arrive with the subsystems they
drive.
"""

from __future__ import annotations

import json
import sys
from typing import Annotated

import typer

from hedwig import __version__
from hedwig.core.config import load_config
from hedwig.core.logging import setup_logging

app = typer.Typer(
    name="hedwig",
    help="HEDWIG — a persistent digital companion. Local-first, private by default.",
    no_args_is_help=True,
    add_completion=False,
)


@app.command()
def serve(
    host: Annotated[str | None, typer.Option(help="Bind address. Loopback by default.")] = None,
    port: Annotated[int | None, typer.Option(help="Bind port.")] = None,
    reload: Annotated[bool, typer.Option(help="Restart on source changes.")] = False,
    log_level: Annotated[str | None, typer.Option(help="debug | info | warning | error")] = None,
) -> None:
    """Run the backend."""
    import uvicorn

    overrides: dict[str, object] = {}
    if host is not None or port is not None:
        api: dict[str, object] = {}
        if host is not None:
            api["host"] = host
        if port is not None:
            api["port"] = port
        overrides["api"] = api
    if log_level is not None:
        overrides["logging"] = {"level": log_level}

    config = load_config(**overrides)
    config.ensure_directories()
    setup_logging(config.logging, config.runtime)

    if config.api.host not in {"127.0.0.1", "localhost", "::1"}:
        # docs/21 §4: exposing HEDWIG beyond loopback is a decision with a threat model.
        typer.secho(
            f"WARNING: binding to {config.api.host}. HEDWIG has no authentication yet and "
            "should not be reachable from a network.",
            fg=typer.colors.YELLOW,
            err=True,
        )

    uvicorn.run(
        "hedwig.api.app:create_app_from_env",
        factory=True,
        host=config.api.host,
        port=config.api.port,
        reload=reload,
        reload_dirs=["src/hedwig"] if reload else None,
        log_config=None,  # our logging owns the loggers; see core/logging.py
        access_log=False,  # the correlation middleware logs requests instead
    )


@app.command(name="config")
def show_config(
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Print the effective configuration after all layers are resolved."""
    config = load_config()
    data = config.redacted_dump()

    if as_json:
        print(json.dumps(data, indent=2, sort_keys=True))
        return

    for section, values in data.items():
        typer.secho(f"[{section}]", fg=typer.colors.CYAN, bold=True)
        for key, value in values.items():
            print(f"  {key} = {value!r}")


@app.command()
def models(
    check: Annotated[bool, typer.Option(help="Probe the backend and report health.")] = True,
) -> None:
    """List the configured models and whether the backend can serve them.

    Uses the gateway the same way any other module does — through the container — which is
    what makes "reusable service" a fact rather than a claim.
    """
    import asyncio

    from hedwig.wiring import build_container

    config = load_config()
    setup_logging(config.logging, config.runtime)
    container = build_container(config, migrate=False)

    async def report() -> None:
        gateway = container.llm
        for tier, model in gateway.models().items():
            print(f"  {tier:<16} {model}")

        if not check:
            return
        health = await gateway.provider_health()
        print()
        if not health.available:
            typer.secho(f"  backend unreachable at {health.endpoint}", fg=typer.colors.RED)
            typer.secho(f"  {health.error}", fg=typer.colors.RED, err=True)
            return

        typer.secho(
            f"  backend {health.endpoint} (ollama {health.version}) in {health.latency_ms:.0f} ms",
            fg=typer.colors.GREEN,
        )
        print(f"  installed: {', '.join(health.installed) or '(none)'}")
        if health.missing:
            typer.secho(f"  missing:   {', '.join(health.missing)}", fg=typer.colors.YELLOW)
            typer.secho(f"  fix with:  ollama pull {health.missing[0]}", fg=typer.colors.YELLOW)

    try:
        asyncio.run(report())
    finally:
        container.database.close()


@app.command()
def version() -> None:
    """Print the version."""
    print(f"hedwig {__version__}")


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:  # pragma: no cover - interactive path
        sys.exit(130)


if __name__ == "__main__":  # pragma: no cover
    main()
