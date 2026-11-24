"""`hedwig doctor`: is everything HEDWIG needs in place, and if not, what to do (SEC-03).

Each check says what it found and, when something is wrong, the one thing to do about it. It
reads; it never changes anything. It works with HEDWIG stopped (it opens the database read-only)
and with it running (it also asks the API).
"""

from __future__ import annotations

import os
import shutil
import socket
import sqlite3
import stat
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import httpx

from hedwig.core.config import Config
from hedwig.core.store import Database
from hedwig.core.store.backups import NIGHTLY, check
from hedwig.core.store.migrations import discover
from hedwig.core.token import TokenFile

SYNCED = ("Dropbox", "OneDrive", "Google Drive", "Mobile Documents", "iCloud Drive", "Box Sync")
MIN_FREE_BYTES = 1_000_000_000


@dataclass(frozen=True)
class Finding:
    name: str
    status: str
    """ok, warn or fail."""
    detail: str
    fix: str = ""


def run_checks(config: Config, *, migrations: Path, today: date) -> list[Finding]:
    data = config.runtime.data_dir
    found = [
        _folder(data),
        _synced(data),
        _token(config),
        *_database(config.database_path, migrations),
        _recent(config.database_path, today),
        _vectors(),
        *_ollama(config),
        *_voice(config),
        _backups(config.runtime.backups_dir, today),
        _disk(data),
        _api(config),
    ]
    return found


def _folder(data: Path) -> Finding:
    if not data.exists():
        return Finding("data folder", "warn", f"{data} does not exist yet", "start HEDWIG once")
    mode = stat.S_IMODE(data.stat().st_mode)
    if mode & 0o077:
        return Finding(
            "data folder",
            "warn",
            f"{data} is readable by others ({oct(mode)})",
            f"chmod 700 {data}",
        )
    return Finding("data folder", "ok", str(data))


def _synced(data: Path) -> Finding:
    hit = next((s for s in SYNCED if s in str(data.resolve())), None)
    if hit:
        return Finding(
            "not in a synced folder",
            "warn",
            f"{data} is inside {hit}: everything HEDWIG knows is copied to that service",
            "move it (runtime.data_dir) somewhere that is not synced",
        )
    return Finding("not in a synced folder", "ok", "")


def _token(config: Config) -> Finding:
    path = config.runtime.token_path
    if not path.exists():
        return Finding("API token", "warn", "none yet", "start HEDWIG once")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        return Finding(
            "API token", "fail", f"readable by others ({oct(mode)})", f"chmod 600 {path}"
        )
    return Finding("API token", "ok", "only you can read it")


def _database(path: Path, migrations: Path) -> list[Finding]:
    if not path.exists():
        return [Finding("database", "warn", "none yet", "start HEDWIG once")]
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            integrity = [r[0] for r in connection.execute("PRAGMA integrity_check")]
            dangling = connection.execute("PRAGMA foreign_key_check").fetchall()
            applied = {r[0] for r in connection.execute("SELECT version FROM schema_migration")}
        finally:
            connection.close()
    except sqlite3.Error as error:
        return [Finding("database", "fail", f"cannot be read: {error}", "hedwig restore <backup>")]
    found = []
    if integrity != ["ok"]:
        found.append(
            Finding(
                "database", "fail", "; ".join(integrity[:2]), "hedwig restore <a recent backup>"
            )
        )
    elif dangling:
        found.append(Finding("database", "warn", f"{len(dangling)} dangling references"))
    else:
        found.append(Finding("database", "ok", f"{path.stat().st_size / 1_048_576:.1f} MB, intact"))
    pending = [m for m in discover(migrations) if m.version not in applied]
    found.append(
        Finding(
            "schema", "warn", f"{len(pending)} migrations to apply", "start HEDWIG to apply them"
        )
        if pending
        else Finding("schema", "ok", f"up to date (version {max(applied, default=0)})")
    )
    return found


def _recent(path: Path, today: date) -> Finding:
    """The last week from the records `/v1/metrics` reads: turns, failures, dead letters."""
    if not path.exists():
        return Finding("last 7 days", "ok", "nothing yet")
    since = (today - timedelta(days=7)).isoformat()
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            rows = connection.execute(
                "SELECT status, latency_ms FROM turn WHERE started_at >= ?", (since,)
            ).fetchall()
            dead = connection.execute(
                "SELECT COUNT(*) FROM event_dead_letter WHERE created_at >= ?", (since,)
            ).fetchone()[0]
        finally:
            connection.close()
    except sqlite3.Error:
        return Finding("last 7 days", "ok", "not recorded by this version yet")
    if not rows and not dead:  # dead letters with no turns are background work failing
        return Finding("last 7 days", "ok", "no conversations")
    failed = sum(status == "failed" for status, _ in rows)
    latencies = sorted(ms for _, ms in rows if ms is not None)
    p95 = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))] / 1000 if latencies else 0
    detail = f"{len(rows)} turns, {failed} failed, p95 {p95:.1f} s, {dead} dead letters"
    if dead or failed / len(rows) > 0.1:
        return Finding(
            "last 7 days", "warn", detail, "hedwig trace --recent, then hedwig trace <id>"
        )
    return Finding("last 7 days", "ok", detail)


def _vectors() -> Finding:
    probe = Database(":memory:")
    try:
        _ = probe.connection
        if probe.vector_search:
            return Finding("recall by meaning", "ok", "sqlite-vec loads")
        return Finding("recall by meaning", "warn", probe.vector_problem, "uv sync (sqlite-vec)")
    finally:
        probe.close()


def _ollama(config: Config) -> list[Finding]:
    if config.llm.provider != "ollama":
        return [Finding("language model", "ok", f"provider {config.llm.provider}")]
    try:
        tags = httpx.get(f"{config.llm.endpoint}/api/tags", timeout=3.0).json()
    except (httpx.HTTPError, ValueError):
        return [
            Finding(
                "Ollama",
                "fail",
                f"not reachable at {config.llm.endpoint}",
                "start Ollama (ollama serve)",
            )
        ]
    installed = {m.get("name", "") for m in tags.get("models", [])}
    installed |= {name.removesuffix(":latest") for name in installed}
    found = [Finding("Ollama", "ok", config.llm.endpoint)]
    for role, model in (
        ("conversation model", config.llm.conversational),
        ("utility model", config.llm.utility),
        ("embedding model", config.llm.embedding),
    ):
        found.append(
            Finding(role, "ok", model)
            if model in installed
            else Finding(role, "fail", f"{model} is not installed", f"ollama pull {model}")
        )
    return found


def _voice(config: Config) -> list[Finding]:
    """Optional: HEDWIG works without either, but should say what is missing."""
    voices = config.speech.voices_dir or config.runtime.data_dir / "voices"
    speaking = (voices / f"{config.speech.voice}.onnx").exists()
    model = config.speech.listen_model
    hearing = (voices / "whisper").exists() and any(
        (voices / "whisper").glob(f"**/*{model}*/**/model.bin")
    )
    return [
        Finding("voice (speaking)", "ok", config.speech.voice)
        if speaking
        else Finding(
            "voice (speaking)", "warn", "no Piper voice", "see docs/15 §13 to install one"
        ),
        Finding("voice (listening)", "ok", model)
        if hearing
        else Finding("voice (listening)", "warn", f"no {model} model", "see docs/15 §13.2"),
    ]


def _backups(directory: Path, today: date) -> Finding:
    nightly = sorted(
        (date.fromisoformat(m.group(1)), p)
        for p in (directory.glob("hedwig-*.db") if directory.exists() else [])
        if (m := NIGHTLY.match(p.name))
    )
    if not nightly:
        return Finding("backups", "warn", "none yet", "one is made each night while HEDWIG runs")
    newest_day, newest = nightly[-1]
    age = (today - newest_day).days
    problem = check(newest)
    if problem:
        return Finding("backups", "fail", f"the newest ({newest.name}) is unsound: {problem}")
    if age > 2:
        return Finding(
            "backups", "warn", f"the newest is {age} days old", "leave HEDWIG running overnight"
        )
    return Finding("backups", "ok", f"{len(nightly)} kept, newest {newest.name}, checked")


def _disk(data: Path) -> Finding:
    where = data if data.exists() else data.parent
    free = shutil.disk_usage(where if where.exists() else Path(os.sep)).free
    if free < MIN_FREE_BYTES:
        return Finding("disk space", "warn", f"{free / 1e9:.1f} GB free", "free some space")
    return Finding("disk space", "ok", f"{free / 1e9:.0f} GB free")


def _api(config: Config) -> Finding:
    try:
        with socket.create_connection((config.api.host, config.api.port), timeout=1):
            pass
    except OSError:
        return Finding("HEDWIG", "ok", "not running (that is fine for these checks)")
    token = TokenFile(config.runtime.token_path).read() or ""
    try:
        response = httpx.get(
            f"{config.api.base_url}/v1/health",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5.0,
        )
        status = response.json().get("status", "unknown")
    except (httpx.HTTPError, ValueError) as error:
        return Finding("HEDWIG", "fail", f"running but not answering: {error}")
    return Finding("HEDWIG", "ok" if status == "ok" else "warn", f"running, health {status}")
