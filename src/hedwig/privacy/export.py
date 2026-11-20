"""Export: everything HEDWIG holds, in one zip a person can read (docs/21 §7.1, §7.4).

The database itself, so nothing is lost, and a rendering of it in plain files, because an export
only the software can read is not really portability. Built into a temporary directory by the
caller, which streams it and deletes it: no second copy of everything is left behind.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from hedwig.core.config import Config
from hedwig.core.ports import Clock
from hedwig.core.store import Database
from hedwig.privacy.tables import TABLES, Kind

FORMAT = 1

README = """HEDWIG export
=============

Everything HEDWIG held about you when this was made.

  hedwig.db         The whole database. Open it with any SQLite tool (sqlite3, DB Browser).
  conversations/    Every conversation, one Markdown file each, oldest first.
  memories.md       What HEDWIG believes about you, and the episodes it remembers.
  goals.md          Goals, open and closed.
  config.json       The settings HEDWIG ran with. The API token is not included.
  blobs/            Stored files, named by their SHA-256.
  manifest.json     Versions, row counts, and a SHA-256 of every file above.

Forgotten memories are included and marked: forgetting hides a memory from HEDWIG, it does
not delete it. Erasing does (`hedwig purge`), and erased things are not here.
"""


class Exporter:
    def __init__(self, *, database: Database, config: Config, clock: Clock, version: str) -> None:
        self._db = database
        self._config = config
        self._clock = clock
        self._version = version

    def filename(self) -> str:
        return f"hedwig-export-{self._clock.now().date().isoformat()}.zip"

    def build(self, workdir: Path) -> Path:
        """Write the export zip into `workdir` and return its path."""
        root = self.filename().removesuffix(".zip")
        files: dict[str, bytes] = {
            "README.txt": README.encode(),
            "memories.md": self._memories().encode(),
            "goals.md": self._goals().encode(),
            "config.json": json.dumps(self._config.redacted_dump(), indent=2).encode(),
        }
        for name, text in self._conversations().items():
            files[f"conversations/{name}"] = text.encode()

        with tempfile.TemporaryDirectory(dir=workdir) as scratch:
            snapshot = Path(scratch) / "hedwig.db"
            self._db.backup_to(snapshot)
            blobs = sorted(p for p in self._config.runtime.blobs_dir.rglob("*") if p.is_file())

            listing: dict[str, dict[str, Any]] = {}
            archive = workdir / self.filename()
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as out:
                for name, data in files.items():
                    out.writestr(f"{root}/{name}", data)
                    listing[name] = _describe(data)
                out.write(snapshot, f"{root}/hedwig.db")
                listing["hedwig.db"] = _describe(snapshot.read_bytes())
                for blob in blobs:
                    name = f"blobs/{blob.relative_to(self._config.runtime.blobs_dir)}"
                    out.write(blob, f"{root}/{name}")
                    listing[name] = _describe(blob.read_bytes())
                out.writestr(f"{root}/manifest.json", json.dumps(self._manifest(listing), indent=2))
        return archive

    # -- the manifest ------------------------------------------------------------

    def _manifest(self, files: dict[str, dict[str, Any]]) -> dict[str, Any]:
        schema = self._db.query_one("SELECT MAX(version) AS v FROM schema_migration")
        counts = {
            name: int(self._db.query_one(f'SELECT COUNT(*) AS n FROM "{name}"')["n"])  # type: ignore[index]
            for name, kind in TABLES.items()
            if kind is not Kind.INDEX
        }
        return {
            "format": FORMAT,
            "hedwig_version": self._version,
            "schema_version": int(schema["v"]) if schema else 0,
            "created_at": self._clock.now().isoformat(),
            "rows": counts,
            "files": files,
        }

    # -- the readable part -------------------------------------------------------

    def _conversations(self) -> dict[str, str]:
        messages: dict[str, list[Any]] = defaultdict(list)
        for row in self._db.query(
            "SELECT session_id, role, text, created_at FROM message ORDER BY session_id, seq"
        ):
            messages[str(row["session_id"])].append(row)
        rendered = {}
        for session in self._db.query(
            "SELECT id, channel, started_at FROM session ORDER BY started_at"
        ):
            said = messages.get(str(session["id"]))
            if not said:
                continue
            day = str(session["started_at"])[:10]
            lines = [
                f"# Conversation of {day}",
                "",
                f"_{session['channel']} · {session['id']}_",
                "",
            ]
            for message in said:
                speaker = "You" if message["role"] == "user" else "HEDWIG"
                lines += [
                    f"**{speaker}** ({_time(message['created_at'])})",
                    "",
                    str(message["text"]),
                    "",
                ]
            rendered[f"{day}-{session['id']}.md"] = "\n".join(lines)
        return rendered

    def _memories(self) -> str:
        lines = ["# What HEDWIG remembers", "", "## Beliefs", ""]
        beliefs = self._db.query(
            "SELECT statement, status, confidence, trust_tier, pinned, tombstoned_at FROM belief "
            "ORDER BY status, confidence DESC"
        )
        if not beliefs:
            lines.append("_None yet._")
        for b in beliefs:
            marks = [
                b["status"],
                f"confidence {float(b['confidence']):.2f}",
                f"from {b['trust_tier']}",
            ]
            marks += ["pinned"] * bool(b["pinned"]) + ["forgotten"] * bool(b["tombstoned_at"])
            lines.append(f"- {b['statement']} _({', '.join(marks)})_")
        lines += ["", "## Episodes", ""]
        episodes = self._db.query(
            "SELECT occurred_at, kind, title, content, tombstoned_at FROM episode ORDER BY occurred_at"
        )
        if not episodes:
            lines.append("_None yet._")
        for e in episodes:
            forgotten = " _(forgotten)_" if e["tombstoned_at"] else ""
            lines += [
                f"### {str(e['occurred_at'])[:16]} — {e['title']}{forgotten}",
                "",
                f"_{e['kind']}_",
                "",
            ]
            lines += [str(e["content"]), ""]
        return "\n".join(lines) + "\n"

    def _goals(self) -> str:
        lines = ["# Goals", ""]
        goals = self._db.query(
            "SELECT description, status, progress, context, created_at FROM goal ORDER BY created_at"
        )
        if not goals:
            lines.append("_None yet._")
        for g in goals:
            lines.append(
                f"- **{g['description']}** — {g['status']}, {round(float(g['progress']) * 100)}%"
            )
            if g["context"]:
                lines.append(f"  {g['context']}")
        return "\n".join(lines) + "\n"


def _describe(data: bytes) -> dict[str, Any]:
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def _time(value: Any) -> str:
    try:
        return datetime.fromisoformat(str(value)).strftime("%H:%M")
    except ValueError:
        return str(value)
