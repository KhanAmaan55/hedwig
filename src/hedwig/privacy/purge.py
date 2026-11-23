"""Purge: delete for real, everywhere, and prove it (docs/21 §7.3-7.4, ADR-0024).

Forgetting hides a memory and can be undone. Purging cannot. It removes the targets, every memory
derived from them, every log row and event that mentions them, the FTS entries, the freed pages
and the WAL frames that still hold their bytes, and every backup made before it. Then it reads
the database file and looks for what it deleted.

Two steps, so nothing is erased that the user has not seen: `preview` resolves the targets and
returns a code; `purge` resolves them again and refuses unless the code still matches.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hedwig.core.errors import ConflictError, InvalidRequestError, NotFoundError
from hedwig.core.ids import new_id
from hedwig.core.logging import fields, get_logger
from hedwig.core.ports import Clock
from hedwig.core.store import Database
from hedwig.privacy.tables import TABLES, Kind, of

logger = get_logger(__name__)

SCOPES = ("memory", "session", "phrase")
CONTENT_RELATIONS = ("summarised_from", "extracted_from", "promoted_from")
"""Edges along which content is copied or paraphrased. `merged_from`, `corroborated_by` and
`contradicted_by` join two memories that each stand on their own: purging one leaves the other."""
MIN_PHRASE = 3
FRAGMENT = 32
MIN_SCANNABLE = 16
"""Shorter texts cannot be told apart from coincidence in a byte scan; queries verify them."""
CHUNK = 400

# What each content table is searched by, and what is shown and verified of it.
TEXT_COLUMNS: dict[str, tuple[str, ...]] = {
    "message": ("text",),
    "episode": ("title", "content"),
    "belief": ("statement",),
    "goal": ("description", "context", "completion"),
    "gap": ("question",),
    "finding": ("summary",),
    "document_chunk": ("heading_path", "text"),
}
LABELS = {
    "session": "conversations",
    "message": "messages",
    "episode": "episodes",
    "belief": "beliefs",
    "goal": "goals",
    "gap": "open questions",
    "finding": "findings",
    "document_chunk": "passages of your documents",
}


@dataclass
class Targets:
    scope: str
    ids: dict[str, set[str]] = field(default_factory=dict)
    linked: set[str] = field(default_factory=set)
    """Ids that are not rows to delete but mark rows to sweep: turns and their correlations."""
    texts: list[str] = field(default_factory=list)
    documents: set[str] = field(default_factory=set)
    """Files whose passages are erased: set aside, so reindexing does not read them back in.
    The files themselves are the user's and are never touched."""

    def add(self, table: str, values: Iterable[Any]) -> set[str]:
        new = {str(v) for v in values if v} - self.ids.setdefault(table, set())
        self.ids[table] |= new
        return new

    def every_id(self) -> set[str]:
        return set().union(*self.ids.values(), self.linked) if self.ids else set(self.linked)

    def counts(self) -> dict[str, int]:
        return {LABELS[t]: len(v) for t, v in self.ids.items() if v}

    def empty(self) -> bool:
        return not any(self.ids.values())

    def code(self) -> str:
        listing = json.dumps({t: sorted(v) for t, v in sorted(self.ids.items())})
        return hashlib.sha256(f"{self.scope}:{listing}".encode()).hexdigest()[:12]


class Purger:
    def __init__(
        self,
        *,
        database: Database,
        clock: Clock,
        backups_dir: Path,
        is_busy: Callable[[], bool],
        drain: Callable[[], Awaitable[None]],
    ) -> None:
        self._db = database
        self._clock = clock
        self._backups = backups_dir
        self._is_busy = is_busy
        self._drain = drain

    # -- the two steps ---------------------------------------------------------------

    async def preview(self, scope: str, value: str) -> dict[str, Any]:
        targets = self._resolve(scope, value)
        examples = sorted({_clip(text) for text in targets.texts})[:5]
        return {
            "scope": scope,
            "counts": targets.counts(),
            "examples": examples,
            "backups": len(self._backup_files()),
            "confirm": None if targets.empty() else targets.code(),
        }

    async def purge(self, scope: str, value: str, confirm: str) -> dict[str, Any]:
        if self._is_busy():
            raise ConflictError("HEDWIG is in the middle of a reply. Try again in a moment.")
        await self._drain()  # nothing in flight may re-create what is about to go
        targets = self._resolve(scope, value)
        if targets.empty():
            raise NotFoundError("There is nothing to erase for that.", scope=scope)
        if confirm != targets.code():
            raise ConflictError(
                "What would be erased has changed since the preview. Preview again.", scope=scope
            )

        started = self._clock.now()
        swept = self._delete(targets, phrase=value if scope == "phrase" else None)
        self._compact()
        verified, elsewhere = self._verify(targets)
        backups = self._delete_backups()

        counts = {**targets.counts(), "log rows": swept}
        receipt = {
            "id": new_id("purge"),
            "scope_kind": scope,
            "created_at": started.isoformat(),
            "counts": counts,
            "verified": verified,
            "also_elsewhere": elsewhere,
            "backups_deleted": backups,
        }
        self._db.execute(
            "INSERT INTO purge_receipt (id, scope_kind, created_at, counts, verified, "
            "also_elsewhere, backups_deleted) VALUES (?,?,?,?,?,?,?)",
            (
                receipt["id"],
                scope,
                receipt["created_at"],
                json.dumps(counts),
                verified,
                elsewhere,
                backups,
            ),
        )
        log = logger.error if verified == "failed" else logger.info
        log("purge", extra=fields(scope=scope, verified=verified, **counts))
        return receipt

    def receipts(self, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._db.query(
            "SELECT * FROM purge_receipt ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,)
        )
        return [{**dict(row), "counts": json.loads(row["counts"])} for row in rows]

    # -- resolving: read only ----------------------------------------------------------

    def _resolve(self, scope: str, value: str) -> Targets:
        value = value.strip()
        if scope not in SCOPES:
            raise InvalidRequestError("Erase a memory, a session or a phrase.", scope=scope)
        if scope == "phrase" and len(value) < MIN_PHRASE:
            raise InvalidRequestError(f"A phrase needs at least {MIN_PHRASE} characters.")
        targets = Targets(scope)

        if scope == "memory":
            targets.add("episode", self._ids("SELECT id FROM episode WHERE id = ?", value))
            targets.add("belief", self._ids("SELECT id FROM belief WHERE id = ?", value))
        elif scope == "session":
            sessions = targets.add(
                "session", self._ids("SELECT id FROM session WHERE id = ?", value)
            )
            if sessions:
                ref = f"session:{value}"
                targets.add(
                    "message", self._ids("SELECT id FROM message WHERE session_id = ?", value)
                )
                targets.add(
                    "episode",
                    self._ids(
                        "SELECT id FROM episode WHERE session_id = ? OR source_ref = ?", value, ref
                    ),
                )
                targets.add("belief", self._ids("SELECT id FROM belief WHERE source_ref = ?", ref))
        else:
            for table, columns in TEXT_COLUMNS.items():
                where = " OR ".join(f"instr(lower({c}), lower(?)) > 0" for c in columns)
                targets.add(
                    table,
                    self._ids(f"SELECT id FROM {table} WHERE {where}", *[value] * len(columns)),
                )

        self._follow(targets)
        targets.texts = self._texts(targets)
        return targets

    def _follow(self, targets: Targets) -> None:
        """Everything that copies or came from a target is a target (docs/21 §7.3)."""
        memories = targets.ids.get("episode", set()) | targets.ids.get("belief", set())
        frontier = set(memories)
        relations = ",".join("?" * len(CONTENT_RELATIONS))
        while frontier:
            derived = set()
            for chunk in _chunks(frontier):
                derived |= self._ids(
                    f"SELECT derived_id FROM memory_derivation WHERE relation IN ({relations}) "
                    f"AND source_id IN ({_marks(chunk)})",
                    *CONTENT_RELATIONS,
                    *chunk,
                )
            frontier = derived - memories
            memories |= frontier
        targets.add("episode", self._existing("episode", memories))
        targets.add("belief", self._existing("belief", memories))

        sessions = targets.ids.get("session", set())
        messages = targets.ids.get("message", set())
        turns: set[str] = set()
        for chunk in _chunks(sessions | messages):
            marks = _marks(chunk)
            for row in self._db.query(
                f"SELECT id, correlation_id FROM turn WHERE session_id IN ({marks}) "
                f"OR user_message_id IN ({marks}) OR reply_message_id IN ({marks})",
                (*chunk, *chunk, *chunk),
            ):
                turns.add(str(row["id"]))
                # Short ids (an HTTP request's correlation) would match by coincidence.
                targets.linked.update(
                    str(v) for v in (row["id"], row["correlation_id"]) if v and len(str(v)) >= 10
                )

        beliefs = targets.ids.get("belief", set())
        goals = self._where("goal", "origin_memory_id", memories)
        targets.add("goal", goals)
        gaps = self._where("gap", "origin_ref", turns | sessions | targets.ids.get("goal", set()))
        targets.add("gap", gaps)
        targets.add("finding", self._where("finding", "gap_id", targets.ids.get("gap", set())))

        # A file with an erased passage is set aside whole: half a note, with the erased part
        # missing, would read as something the user never wrote.
        chunks = targets.ids.get("document_chunk", set())
        targets.documents = {
            str(r["document_id"])
            for chunk in _chunks(chunks)
            for r in self._db.query(
                f"SELECT DISTINCT document_id FROM document_chunk WHERE id IN ({_marks(chunk)})",
                chunk,
            )
        }
        for chunk in _chunks(targets.documents):
            targets.add(
                "document_chunk",
                self._ids(
                    f"SELECT id FROM document_chunk WHERE document_id IN ({_marks(chunk)})", *chunk
                ),
            )
        targets.add("finding", self._where("finding", "promoted_belief_id", beliefs))

    def _texts(self, targets: Targets) -> list[str]:
        texts = []
        for table, columns in TEXT_COLUMNS.items():
            for chunk in _chunks(targets.ids.get(table, set())):
                for row in self._db.query(
                    f"SELECT {', '.join(columns)} FROM {table} WHERE id IN ({_marks(chunk)})", chunk
                ):
                    texts += [str(row[c]) for c in columns if row[c]]
        return texts

    # -- deleting: one transaction -------------------------------------------------------

    def _delete(self, targets: Targets, *, phrase: str | None) -> int:
        ids = targets.every_id()
        connection = self._db.connection
        connection.execute("PRAGMA secure_delete = ON")  # freed pages are zeroed, not left
        swept = 0
        with self._db.transaction() as tx:
            tx.execute("PRAGMA defer_foreign_keys = ON")
            for table, values in targets.ids.items():
                for chunk in _chunks(values):
                    tx.execute(f"DELETE FROM {table} WHERE id IN ({_marks(chunk)})", chunk)
            # Events first: rows that point at a deleted event (the delivery ledger) go too.
            removed_events = self._sweep(tx, "event", ids, phrase)
            swept += len(removed_events)
            ids |= removed_events
            for table in of(Kind.RECORD):
                if table != "event":
                    swept += len(self._sweep(tx, table, ids, phrase))
            for chunk in _chunks(targets.documents):
                tx.execute(
                    f"UPDATE document SET removed_at = ?, chunk_count = 0 "
                    f"WHERE id IN ({_marks(chunk)})",
                    (self._clock.now().isoformat(), *chunk),
                )
            self._unpoint(tx, targets.every_id())
            self._release_dangling(tx)
        return swept

    def _unpoint(self, tx: Any, ids: set[str]) -> None:
        """Clear a surviving content row's plain-text pointer to a target (no FK to release it)."""
        for table in of(Kind.CONTENT):
            for column in tx.execute(f"PRAGMA table_info({table})").fetchall():
                name = column["name"]
                if name == "id" or name in TEXT_COLUMNS.get(table, ()) or column["notnull"]:
                    continue
                if not _texty(column["type"]):
                    continue
                for chunk in _chunks(ids):
                    tx.execute(
                        f"UPDATE {table} SET {name} = NULL WHERE {name} IN ({_marks(chunk)})", chunk
                    )

    def _sweep(self, tx: Any, table: str, ids: set[str], phrase: str | None) -> set[str]:
        """Delete every row of a record table that mentions a target id (or the phrase)."""
        columns = [
            r["name"] for r in tx.execute(f"PRAGMA table_info({table})") if _texty(r["type"])
        ]
        if not columns or (not ids and not phrase):
            return set()
        pattern = (
            re.compile("|".join(re.escape(i) for i in sorted(ids, key=len, reverse=True)))
            if ids
            else None
        )
        wording = re.compile(re.escape(phrase), re.IGNORECASE) if phrase else None
        doomed: list[int] = []
        keys: set[str] = set()
        for row in tx.execute(f"SELECT rowid AS _r, {', '.join(columns)} FROM {table}"):
            values = [str(row[c]) for c in columns if row[c] is not None]
            if any(
                (pattern and pattern.search(v)) or (wording and wording.search(v)) for v in values
            ):
                doomed.append(int(row["_r"]))
                if "id" in columns and row["id"]:
                    keys.add(str(row["id"]))
        for chunk in _chunks(doomed):
            tx.execute(f"DELETE FROM {table} WHERE rowid IN ({_marks(chunk)})", chunk)
        return keys if table == "event" else {str(r) for r in doomed}

    def _release_dangling(self, tx: Any) -> None:
        """A survivor that only pointed at something erased loses the pointer, not its content."""
        for _ in range(5):
            violations = tx.execute("PRAGMA foreign_key_check").fetchall()
            if not violations:
                return
            for table, rowid, _parent, fkid in violations:
                column = next(
                    r["from"]
                    for r in tx.execute(f"PRAGMA foreign_key_list({table})")
                    if r["id"] == fkid
                )
                nullable = next(
                    not r["notnull"]
                    for r in tx.execute(f"PRAGMA table_info({table})")
                    if r["name"] == column
                )
                if nullable:
                    tx.execute(f"UPDATE {table} SET {column} = NULL WHERE rowid = ?", (rowid,))
                elif TABLES.get(table) is Kind.RECORD:
                    tx.execute(f"DELETE FROM {table} WHERE rowid = ?", (rowid,))
                else:
                    raise ConflictError(
                        "Erasing that would break what HEDWIG still holds; nothing was erased.",
                        table=table,
                    )
        raise ConflictError("Erasing that left references that could not be released.")

    def _compact(self) -> None:
        """Push the deletions through every place the bytes could still be.

        `secure_delete` zeroes the cells it deletes, but not the slack of a page that was
        rebuilt earlier (a leaf turned interior page keeps its old cells' bytes in unused space)
        — the completeness test found a purged sentence exactly there. Only rebuilding the file
        removes slack, so a purge ends with a full VACUUM, then empties the WAL.
        """
        for index in ("episode_fts", "belief_fts", "document_chunk_fts"):
            self._db.execute(f"INSERT INTO {index} ({index}) VALUES ('optimize')")
        connection = self._db.connection
        connection.execute("VACUUM")
        if not self._db.is_memory:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()

    # -- verifying ----------------------------------------------------------------------

    def _verify(self, targets: Targets) -> tuple[str, int]:
        """`file` when the file's bytes were checked, `query` for an in-memory database."""
        ids = targets.every_id()
        pattern = re.compile("|".join(re.escape(i) for i in sorted(ids, key=len, reverse=True)))
        for table in of(Kind.CONTENT) + of(Kind.RECORD):
            columns = [
                r["name"]
                for r in self._db.query(f"PRAGMA table_info({table})")
                if _texty(r["type"])
            ]
            if not columns:
                continue
            for row in self._db.query(f"SELECT {', '.join(columns)} FROM {table}"):
                if any(row[c] is not None and pattern.search(str(row[c])) for c in columns):
                    logger.error("purge left a reference", extra=fields(table=table))
                    return "failed", 0
        if self._db.is_memory:
            return "query", 0

        raw = b"".join(
            path.read_bytes()
            for path in (self._db.path, self._db.path.with_name(self._db.path.name + "-wal"))
            if path.exists()
        )
        elsewhere = 0
        for fragment in {f for text in targets.texts for f in _fragments(text)}:
            if fragment.encode() not in raw:
                continue
            if self._survives(fragment):
                elsewhere += 1
            else:
                logger.error("purge left bytes in the file", extra=fields(length=len(fragment)))
                return "failed", elsewhere
        return "file", elsewhere

    def _survives(self, fragment: str) -> bool:
        for table, kind in TABLES.items():
            if kind is Kind.INDEX:
                continue
            for column in (
                r["name"]
                for r in self._db.query(f"PRAGMA table_info({table})")
                if _texty(r["type"])
            ):
                if self._db.query_one(
                    f"SELECT 1 FROM {table} WHERE instr({column}, ?) > 0 LIMIT 1", (fragment,)
                ):
                    return True
        return False

    # -- backups --------------------------------------------------------------------------

    def _backup_files(self) -> list[Path]:
        return (
            sorted(p for p in self._backups.glob("*") if p.is_file())
            if self._backups.exists()
            else []
        )

    def _delete_backups(self) -> int:
        """Every backup predates the purge, so every backup still holds what was erased."""
        files = self._backup_files()
        for path in files:
            path.unlink(missing_ok=True)
        return len(files)

    # -- small helpers ----------------------------------------------------------------

    def _ids(self, sql: str, *parameters: Any) -> set[str]:
        return {str(row[0]) for row in self._db.query(sql, parameters)}

    def _existing(self, table: str, ids: set[str]) -> set[str]:
        found: set[str] = set()
        for chunk in _chunks(ids):
            found |= self._ids(f"SELECT id FROM {table} WHERE id IN ({_marks(chunk)})", *chunk)
        return found

    def _where(self, table: str, column: str, values: set[str]) -> set[str]:
        found: set[str] = set()
        for chunk in _chunks(values):
            found |= self._ids(
                f"SELECT id FROM {table} WHERE {column} IN ({_marks(chunk)})", *chunk
            )
        return found


def _chunks(values: Iterable[Any]) -> Iterable[list[Any]]:
    items = sorted(values, key=str)
    for start in range(0, len(items), CHUNK):
        yield items[start : start + CHUNK]


def _marks(chunk: list[Any]) -> str:
    return ",".join("?" * len(chunk))


def _texty(declared: str) -> bool:
    return not declared or "TEXT" in declared.upper()


def _fragments(text: str) -> list[str]:
    if len(text) < MIN_SCANNABLE:
        return []
    if len(text) <= FRAGMENT + 16:
        return [text]
    middle = len(text) // 2
    return [
        text[:FRAGMENT],
        text[middle - FRAGMENT // 2 : middle + FRAGMENT // 2],
        text[-FRAGMENT:],
    ]


def _clip(text: str, limit: int = 140) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "\N{HORIZONTAL ELLIPSIS}"
