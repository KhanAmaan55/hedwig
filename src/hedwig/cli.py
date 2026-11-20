"""The `hedwig` command line.

A first-class client, not a debugging afterthought (docs/17 §9). Building it first is what
forces the API to be complete before any UI exists.

Commands: `serve`, `chat`, `token`, `config`, `models`, `version`. `memory`, `mind`,
`reflect` and `db` arrive with the subsystems they drive (docs/29).

`chat` is a client of the running backend, not a second backend: it reaches HEDWIG only
through `hedwig.client`, which only speaks the public API (docs/16 §12.3).
"""

from __future__ import annotations

import contextlib
import json
import sys
from collections.abc import Callable
from typing import Annotated, Literal

import typer

from hedwig import __version__
from hedwig.client import ClientError, HedwigClient
from hedwig.core.config import load_config
from hedwig.core.logging import setup_logging
from hedwig.core.token import TokenFile

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


Kind = Literal["hedwig", "info", "error"]
Writer = Callable[[str, Kind], None]

CHAT_HELP = "/new starts a new conversation · /quit ends this one and leaves (or Ctrl-D)"
RESUME_PREVIEW = 6


def run_chat(
    client: HedwigClient,
    *,
    session_id: str | None,
    read: Callable[[str], str],
    write: Writer,
) -> int:
    """The chat loop, separated from the terminal so it can be tested without one.

    Returns the process exit code. Errors that a retry could fix (the model is still
    loading, the backend restarted) leave the loop running; errors that cannot be fixed from
    inside it (the token was rotated, the session does not exist) end it.
    """
    try:
        if session_id is None:
            session_id = client.open_session(channel="cli")
        elif not client.session_exists(session_id):
            write(f"No conversation called {session_id}.", "error")
            return 1
        else:
            for message in client.history(session_id, limit=RESUME_PREVIEW):
                write(f"{_speaker(message.role)}: {message.text}", "info")
    except ClientError as error:
        write(_explain(error), "error")
        return 1

    write(f"Talking to HEDWIG ({session_id}). {CHAT_HELP}", "info")
    while True:
        try:
            line = read("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            write("", "info")
            break
        if not line:
            continue
        if line in {"/quit", "/exit"}:
            break
        if line == "/help":
            write(CHAT_HELP, "info")
            continue
        if line == "/new":
            try:
                _end_quietly(client, session_id)
                session_id = client.open_session(channel="cli")
            except ClientError as error:
                write(_explain(error), "error")
                continue
            write(f"New conversation ({session_id}).", "info")
            continue

        try:
            reply = client.send(session_id, line)
        except ClientError as error:
            # A failed turn still carries HEDWIG's own honest sentence (docs/16 §12.3).
            if error.detail.get("reply"):
                write(str(error.detail["reply"]), "hedwig")
            write(_explain(error), "error")
            if error.code == "unauthorized":
                return 1
            continue
        write(reply.text, "hedwig")

    # Leaving ends the conversation now, so the relationship update does not wait for the idle
    # timeout (FND-03). Resuming later is still possible: it becomes a new conversation.
    _end_quietly(client, session_id)
    write(f"Resume with: hedwig chat --session {session_id}", "info")
    return 0


def _end_quietly(client: HedwigClient, session_id: str) -> None:
    """Best effort: the idle reaper ends it anyway if the backend is gone."""
    with contextlib.suppress(ClientError):
        client.end_session(session_id)


def _speaker(role: str) -> str:
    return "HEDWIG" if role == "hedwig" else "you" if role == "user" else role


def _explain(error: ClientError) -> str:
    if error.code == "unreachable":
        return "HEDWIG is not running. Start it with `uv run hedwig serve` (or `npm run dev`)."
    if error.code == "unauthorized":
        return "The API token was refused. If it was rotated, start `hedwig chat` again."
    if error.code == "capability_unavailable":
        return "The language model is unavailable. `hedwig models` shows what is missing."
    return f"{error.code}: {error.message}"


@app.command()
def chat(
    session: Annotated[
        str | None, typer.Option(help="Resume a conversation by id instead of starting one.")
    ] = None,
    url: Annotated[str | None, typer.Option(help="The backend. Defaults to the config.")] = None,
) -> None:
    """Talk to HEDWIG in the terminal. Needs the backend running (`hedwig serve`)."""
    config = load_config()
    token = TokenFile(config.runtime.token_path).read()
    if token is None:
        typer.secho(
            f"No API token at {config.runtime.token_path}. Start the backend once "
            "(`uv run hedwig serve`) and it will create one.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)

    colours: dict[Kind, str | None] = {
        "hedwig": typer.colors.CYAN,
        "info": typer.colors.BRIGHT_BLACK,
        "error": typer.colors.YELLOW,
    }

    def write(text: str, kind: Kind) -> None:
        prefix = "HEDWIG> " if kind == "hedwig" else ""
        typer.secho(f"{prefix}{text}", fg=colours[kind], err=kind == "error")

    with HedwigClient(url or config.api.base_url, token) as client:
        code = run_chat(client, session_id=session, read=input, write=write)
    raise typer.Exit(code)


token_app = typer.Typer(
    help="The API token that guards your conversations (ADR-0022).", no_args_is_help=True
)
app.add_typer(token_app, name="token")


@token_app.command("show")
def token_show() -> None:
    """Print the token, creating it if this is the first run."""
    config = load_config()
    print(TokenFile(config.runtime.token_path).ensure())


@token_app.command("rotate")
def token_rotate() -> None:
    """Replace the token. A running backend honours the new one on its next request."""
    config = load_config()
    token = TokenFile(config.runtime.token_path).rotate()
    print(token)
    typer.secho(
        "Clients holding the old token will be refused until they reconnect.",
        fg=typer.colors.YELLOW,
        err=True,
    )


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
