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
from pathlib import Path
from typing import Annotated, Any, Literal

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


memory_app = typer.Typer(
    help="What HEDWIG remembers: list, search, show, pin, forget, restore (docs/06 §16).",
    no_args_is_help=True,
)
app.add_typer(memory_app, name="memory")


def _api(url: str | None = None) -> HedwigClient:
    """A client for the running backend, or a clear exit when there is no token yet."""
    config = load_config()
    token = TokenFile(config.runtime.token_path).read()
    if token is None:
        typer.secho(
            "No API token yet. Start the backend once (`uv run hedwig serve`).",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
    return HedwigClient(url or config.api.base_url, token)


def _call(action: Callable[[HedwigClient], object]) -> object:
    with _api() as client:
        try:
            return action(client)
        except ClientError as error:
            typer.secho(_explain(error), fg=typer.colors.RED, err=True)
            raise typer.Exit(1) from None


def _line(memory: dict[str, object]) -> str:
    flags = "".join(
        mark for mark, on in (("📌", memory["pinned"]), ("✗", memory["forgotten"])) if on
    )
    text = str(memory["text"]).replace("\n", " / ")
    return f"{memory['id']}  {memory['kind']:<7} {flags:<2} {text[:90]}"


@memory_app.command("list")
def memory_list(
    limit: Annotated[int, typer.Option(help="How many.")] = 20,
    forgotten: Annotated[bool, typer.Option(help="Include forgotten memories.")] = False,
) -> None:
    """Recent memories, newest first."""
    memories = _call(lambda c: c.memories(limit=limit, include_forgotten=forgotten))
    assert isinstance(memories, list)
    for memory in memories:
        print(_line(memory))
    if not memories:
        print("Nothing remembered yet.")


@memory_app.command("search")
def memory_search(query: str, limit: int = 10) -> None:
    """Search the way HEDWIG recalls, with scores. Searching never reinforces a memory."""
    hits = _call(lambda c: c.search_memory(query, limit=limit))
    assert isinstance(hits, list)
    for hit in hits:
        print(f"{hit['score']:.3f}  {_line(hit['memory'])}")
    if not hits:
        print("No memory matches.")


@memory_app.command("show")
def memory_show(memory_id: str) -> None:
    """One memory in full, including a forgotten one."""
    print(json.dumps(_call(lambda c: c.memory(memory_id)), indent=2))


def _act(memory_id: str, action: str) -> None:
    print(_line(_call(lambda c: c.memory_action(memory_id, action))))  # type: ignore[arg-type]


@memory_app.command("pin")
def memory_pin(memory_id: str) -> None:
    """Never forget this one."""
    _act(memory_id, "pin")


@memory_app.command("unpin")
def memory_unpin(memory_id: str) -> None:
    """Let it fade like any other memory."""
    _act(memory_id, "unpin")


@memory_app.command("forget")
def memory_forget(memory_id: str) -> None:
    """Forget it. Reversible with `restore`."""
    _act(memory_id, "forget")


@memory_app.command("restore")
def memory_restore(memory_id: str) -> None:
    """Bring a forgotten memory back."""
    _act(memory_id, "restore")


@memory_app.command("correct")
def memory_correct(
    memory_id: str,
    text: Annotated[str | None, typer.Option(help="What is actually true.")] = None,
    confidence: Annotated[
        float | None, typer.Option(min=0.0, max=1.0, help="How sure, 0 to 1.")
    ] = None,
) -> None:
    """Correct a memory. The old version is kept as history; yours is trusted as your word."""
    if text is None and confidence is None:
        raise typer.BadParameter("Give --text, --confidence, or both.")
    memory = _call(lambda c: c.memory_correct(memory_id, text=text, confidence=confidence))
    assert isinstance(memory, dict)
    print(f"corrected; now {_line(memory)}")


docs_app = typer.Typer(
    help="Your own notes, which HEDWIG can recall and cite (docs/13 §14).", no_args_is_help=True
)
app.add_typer(docs_app, name="docs")


def _report(result: object) -> None:
    assert isinstance(result, dict)
    counts = ", ".join(f"{k} {result[k]}" for k in ("added", "updated", "unchanged", "removed"))
    print(counts)
    for skipped in result.get("skipped", []):
        print(f"  skipped {skipped['path']}: {skipped['reason']}")


@docs_app.command("add")
def docs_add(path: str) -> None:
    """Read a folder (or one file) of notes. Text only; secrets are skipped and listed."""
    _report(_call(lambda c: c.add_documents(str(Path(path).expanduser().resolve()))))


@docs_app.command("list")
def docs_list() -> None:
    """The folders HEDWIG reads, and every file in them."""
    listing = _call(lambda c: c.documents())
    assert isinstance(listing, dict)
    for root in listing["roots"]:
        print(f"{root['path']}/")
    for doc in listing["documents"]:
        state = "set aside" if doc["removed_at"] else f"{doc['chunk_count']} passages"
        print(f"  {doc['id']}  {doc['path']}  ({state})")


@docs_app.command("reindex")
def docs_reindex() -> None:
    """Re-read every folder now: new, changed and deleted files."""
    _report(_call(lambda c: c.reindex_documents()))


@docs_app.command("remove")
def docs_remove(document_id: str) -> None:
    """Set one file aside; HEDWIG stops recalling it."""
    _call(lambda c: c.remove_document(document_id))
    print("set aside")


@docs_app.command("forget-folder")
def docs_forget_folder(path: str) -> None:
    """Stop reading a folder; everything from it leaves HEDWIG's index (your files stay)."""
    result = _call(lambda c: c.forget_folder(str(Path(path).expanduser().resolve())))
    assert isinstance(result, dict)
    print(f"removed {result['removed']} files from the index")


goals_app = typer.Typer(
    help="What HEDWIG is helping you with over time (docs/08 §6.1).", no_args_is_help=True
)
app.add_typer(goals_app, name="goals")

OPEN_GOALS = ("proposed", "active", "blocked")


def _goal_line(goal: dict[str, Any]) -> str:
    progress = f"{round(goal['progress'] * 100):>3}%"
    origin = " (proposed by HEDWIG)" if goal["origin"] == "reflection" else ""
    return f"{goal['id']}  {goal['status']:<9} {progress}  {goal['description']}{origin}"


@goals_app.command("list")
def goals_list(
    everything: Annotated[bool, typer.Option("--all", help="Include done and abandoned.")] = False,
) -> None:
    """Open goals, highest priority first."""
    goals = _call(lambda c: c.goals(statuses=() if everything else OPEN_GOALS))
    assert isinstance(goals, list)
    for goal in goals:
        print(_goal_line(goal))
    if not goals:
        print('No goals yet. Add one with `hedwig goals add "…"`.')


@goals_app.command("add")
def goals_add(
    description: str,
    done_when: Annotated[str, typer.Option(help="How you'll both know it is done.")] = "",
    priority: Annotated[float, typer.Option(min=0, max=1)] = 0.5,
) -> None:
    """Add an active goal."""
    goal = _call(lambda c: c.create_goal(description, completion=done_when, priority=priority))
    assert isinstance(goal, dict)
    print(_goal_line(goal))


def _set_goal(goal_id: str, **changes: Any) -> None:
    goal = _call(lambda c: c.update_goal(goal_id, **changes))
    assert isinstance(goal, dict)
    print(_goal_line(goal))


@goals_app.command("journal")
def goals_journal(goal_id: str) -> None:
    """A project's journal: what was decided, what moved, what is still open."""
    notes = _call(lambda c: c.goal_notes(goal_id))
    assert isinstance(notes, list)
    if not notes:
        print("nothing written yet")
    for note in reversed(notes):
        print(f"{note['created_at'][:10]}  {note['kind']:<9} {note['text']}  ({note['source']})")


@goals_app.command("note")
def goals_note(
    goal_id: str,
    text: str,
    kind: Annotated[
        str, typer.Option(help="decision, progress, question, blocker or done.")
    ] = "progress",
) -> None:
    """Write in a project's journal."""
    _call(lambda c: c.add_goal_note(goal_id, text, kind=kind))
    print("noted")


@goals_app.command("activate")
def goals_activate(goal_id: str) -> None:
    """Accept a goal HEDWIG proposed, or reopen one."""
    _set_goal(goal_id, status="active")


@goals_app.command("progress")
def goals_progress(goal_id: str, amount: Annotated[float, typer.Argument(min=0, max=1)]) -> None:
    """Record how far along a goal is, 0 to 1."""
    _set_goal(goal_id, progress=amount)


@goals_app.command("done")
def goals_done(goal_id: str) -> None:
    """Close a goal as achieved."""
    _set_goal(goal_id, status="done", progress=1.0)


@goals_app.command("drop")
def goals_drop(goal_id: str) -> None:
    """Abandon a goal."""
    _set_goal(goal_id, status="abandoned")


curiosity_app = typer.Typer(
    help="What HEDWIG wants to find out, and what it found (docs/11 §13).", no_args_is_help=True
)
app.add_typer(curiosity_app, name="curiosity")


@curiosity_app.command("gaps")
def curiosity_gaps() -> None:
    """Questions HEDWIG knows it cannot answer yet."""
    gaps = _call(lambda c: c.gaps())
    assert isinstance(gaps, list)
    for gap in gaps:
        print(f"{gap['id']}  {gap['origin']:<20} {gap['priority']:.2f}  {gap['question']}")
    if not gaps:
        print("No open questions.")


@curiosity_app.command("ask")
def curiosity_ask(question: str) -> None:
    """Give HEDWIG something to find out about, in general terms."""
    gap = _call(lambda c: c.ask_curiosity(question))
    assert isinstance(gap, dict)
    print(f"{gap['id']}  {gap['question']}")


@curiosity_app.command("findings")
def curiosity_findings() -> None:
    """What HEDWIG found while exploring. Always read on the web, so untrusted."""
    found = _call(lambda c: c.findings())
    assert isinstance(found, list)
    for finding in found:
        print(f"{finding['id']}  [{finding['status']}]  {finding['question']}")
        print(f"    {finding['summary']}")
        for source in finding["sources"][:3]:
            print(f"    - {source['url']}")
    if not found:
        print("Nothing found yet. (Exploring needs curiosity.enabled and knowledge.web_enabled.)")


@curiosity_app.command("explore")
def curiosity_explore() -> None:
    """Explore one open question now (still within the daily budget)."""
    result = _call(lambda c: c.explore())
    assert isinstance(result, dict)
    print(result["status"])


@curiosity_app.command("confirm")
def curiosity_confirm(finding_id: str) -> None:
    """Keep a finding as a tentative belief."""
    _call(lambda c: c.finding_action(finding_id, "confirm"))
    print("kept")


@curiosity_app.command("dismiss")
def curiosity_dismiss(finding_id: str) -> None:
    """Not interested; similar findings will be dropped."""
    _call(lambda c: c.finding_action(finding_id, "dismiss"))
    print("dismissed")


@app.command()
def reflect() -> None:
    """Run the nightly consolidation now: summarise days, merge and judge beliefs, promote, decay."""
    result = _call(lambda c: c.reflect())
    assert isinstance(result, dict)
    print(
        result["status"] + (f" (resumes at {result['next_stage']})" if result["next_stage"] else "")
    )
    for stage, stats in result["stats"].items():
        print(f"  {stage:<10} {json.dumps(stats)}")


@app.command()
def export(
    out: Annotated[
        Path, typer.Option(help="A file, or a folder for the dated zip HEDWIG names.")
    ] = Path(),
) -> None:
    """Download everything HEDWIG holds: the database, plus conversations and memories as files."""
    written = _call(lambda c: c.export(out))
    assert isinstance(written, Path)
    destination = written
    size = destination.stat().st_size
    print(f"wrote {destination} ({size / 1_048_576:.1f} MB). It contains everything; keep it safe.")


@app.command()
def purge(
    memory: Annotated[
        str | None, typer.Option(help="Erase one memory and what came from it.")
    ] = None,
    session: Annotated[str | None, typer.Option(help="Erase one conversation.")] = None,
    phrase: Annotated[
        str | None, typer.Option(help="Erase everything whose text contains this phrase.")
    ] = None,
    everything: Annotated[
        bool,
        typer.Option("--everything", help="Erase all of HEDWIG's data. HEDWIG must be stopped."),
    ] = False,
    confirm: Annotated[
        str | None, typer.Option(help="The preview's code, to erase without being asked.")
    ] = None,
) -> None:
    """Erase permanently. Unlike `memory forget`, this cannot be undone (docs/21 §7.3)."""
    chosen = [
        (k, v) for k, v in (("memory", memory), ("session", session), ("phrase", phrase)) if v
    ]
    if everything:
        if chosen:
            raise typer.BadParameter("--everything stands alone.")
        _erase_everything()
        return
    if len(chosen) != 1:
        raise typer.BadParameter("Choose one of --memory, --session, --phrase or --everything.")
    scope, value = chosen[0]
    preview = _call(lambda c: c.purge_preview(scope, value))
    assert isinstance(preview, dict)
    if not preview["confirm"]:
        print("nothing to erase")
        return
    print("This will permanently erase:")
    for label, count in preview["counts"].items():
        print(f"  {count:>5}  {label.removesuffix('s') if count == 1 else label}")
    for example in preview["examples"]:
        print(f"         e.g. {example}")
    if preview["backups"]:
        print(f"  and delete {preview['backups']} backup(s), which still contain it.")
    if confirm is None:
        typed = typer.prompt('Type "erase" to continue', default="", show_default=False)
        if typed.strip().lower() != "erase":
            print("nothing erased")
            raise typer.Exit(1)
        confirm = str(preview["confirm"])
    receipt = _call(lambda c: c.purge(scope, value, confirm))
    assert isinstance(receipt, dict)
    if receipt["verified"] == "failed":
        typer.secho(
            "Erased, but the check found traces left in the database file. Run "
            "`hedwig purge --everything` with HEDWIG stopped to be sure.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)
    note = (
        f"; {receipt['also_elsewhere']} piece(s) of it are also said elsewhere, outside what you chose"
        if receipt["also_elsewhere"]
        else ""
    )
    print(f"erased and verified against the database file{note}.")


def _erase_everything() -> None:
    import shutil
    import socket

    config = load_config()
    try:
        with socket.create_connection((config.api.host, config.api.port), timeout=1):
            running = True
    except OSError:
        running = False
    if running:
        typer.secho(
            "HEDWIG is running. Stop it first, then run this again.", fg=typer.colors.RED, err=True
        )
        raise typer.Exit(1)

    runtime = config.runtime
    database = config.database_path
    doomed = [
        p
        for p in (
            database,
            database.with_name(database.name + "-wal"),
            database.with_name(database.name + "-shm"),
            runtime.blobs_dir,
            runtime.backups_dir,
            runtime.logs_dir,
            runtime.token_path,
        )
        if p.exists()
    ]
    if not doomed:
        print("nothing to erase")
        return
    print("This will permanently delete:")
    for path in doomed:
        size = (
            sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
            if path.is_dir()
            else path.stat().st_size
        )
        print(f"  {path}  ({size / 1_048_576:.1f} MB)")
    print("Downloaded voices and your configuration files are kept.")
    typed = typer.prompt('Type "erase everything" to continue', default="", show_default=False)
    if typed.strip().lower() != "erase everything":
        print("nothing erased")
        raise typer.Exit(1)
    for path in doomed:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    print("erased. HEDWIG will start fresh next time.")


@app.command()
def disputes() -> None:
    """Beliefs HEDWIG thinks repeat or conflict. Settle one with `hedwig settle`."""
    found = _call(lambda c: c.disputes())
    assert isinstance(found, list)
    if not found:
        print("nothing needs your eye")
    for item in found:
        print(f"{item['pair']}  ({item['verdict']}?)")
        print(f"  first:  {item['first']['statement']}")
        print(f"  second: {item['second']['statement']}")


@app.command()
def settle(
    pair: Annotated[str, typer.Argument(help="The pair, as `hedwig disputes` prints it.")],
    choice: Annotated[
        str, typer.Argument(help="first or second (that one is true now), both, or same.")
    ],
) -> None:
    """Say which of two beliefs is true. What you keep counts as confirmed."""
    result = _call(lambda c: c.resolve_dispute(pair, choice))
    assert isinstance(result, dict)
    print(f"settled: {result['resolution']}")


@app.command()
def egress(limit: Annotated[int, typer.Option(help="How many.")] = 30) -> None:
    """Every request HEDWIG made to the internet, newest first, with what it sent."""
    rows = _call(lambda c: c.egress(limit=limit))
    assert isinstance(rows, list)
    for row in rows:
        sent = f'  sent: "{row["query"]}"' if row["query"] else ""
        print(
            f"{row['created_at'][:19]}  {row['purpose']:<7} {row['outcome']:<11} {row['url'][:70]}{sent}"
        )
    if not rows:
        print("Nothing has left this machine. (Web search is off unless knowledge.web_enabled.)")


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
