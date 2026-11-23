"""Local documents: the user's own notes, retrievable with attribution (docs/13 §4, §14; KNW-01).

The best knowledge source there is — private, curated, no network — and the one that needs the
most care at its edge, because it reads the user's filesystem. So the edge is narrow on purpose:

* **Only under folders the user added**, read-only. A folder is resolved once; nothing outside
  it is read, symlinks that leave it are skipped, hidden files and folders are skipped, and the
  filesystem root, the home folder itself and HEDWIG's own data folder are refused as roots.
* **Text formats only**, with a size cap per file and a count cap per folder. A folder over the
  cap is refused rather than half-read.
* **Secrets are skipped and reported**, by name (`.env`, keys) and by content (private keys,
  API tokens). A note containing a key is not indexed at all; nothing of it reaches a prompt.

Documents are CURATED: information, never instructions, retrieved as *your documents* with the
file and heading, and never promoted into HEDWIG's own beliefs (docs/13 §4).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from hedwig.core.errors import InvalidRequestError, NotFoundError
from hedwig.core.ids import new_id
from hedwig.core.logging import fields, get_logger
from hedwig.core.ports import Clock
from hedwig.core.ports.scheduler import TaskContext, TaskResult
from hedwig.core.store import Database

logger = get_logger(__name__)

TEXT_SUFFIXES = frozenset({".md", ".markdown", ".txt", ".text", ".rst", ".org", ".html", ".htm"})
SKIP_DIRS = frozenset({"node_modules", "__pycache__", "venv", "site-packages", "dist", "build"})
CHUNK_CHARS = 1600
OVERLAP_CHARS = 200

SECRET_NAMES = re.compile(
    r"(?:^\.env|\.pem$|\.key$|\.p12$|\.pfx$|^id_(?:rsa|dsa|ecdsa|ed25519)|credentials|secrets?\b"
    r"|\.kdbx$|password)",
    re.IGNORECASE,
)
SECRET_CONTENT = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|\bAKIA[0-9A-Z]{16}\b"
    r"|\bgh[pousr]_[A-Za-z0-9]{30,}\b"
    r"|\bsk-[A-Za-z0-9_-]{20,}\b"
    r"|\bxox[abpr]-[A-Za-z0-9-]{10,}\b"
    r"|\b(?:api[_-]?key|secret|token|password)\s*[:=]\s*['\"]?[A-Za-z0-9/+_-]{16,}",
    re.IGNORECASE,
)
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


@dataclass
class IngestReport:
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    removed: int = 0
    skipped: list[dict[str, str]] = field(default_factory=list)
    """Files not read, with why: a secret, too big, not text. Shown to the user."""

    def as_dict(self) -> dict[str, Any]:
        return {
            "added": self.added,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "removed": self.removed,
            "skipped": self.skipped,
        }


class DocumentLibrary:
    def __init__(
        self,
        database: Database,
        *,
        clock: Clock,
        data_dir: Path,
        home: Path | None = None,
        max_bytes: int = 2_000_000,
        max_files: int = 5_000,
    ) -> None:
        self._db = database
        self._clock = clock
        self._data_dir = data_dir.expanduser().resolve()
        self._home = (home or Path.home()).resolve()
        self._max_bytes = max_bytes
        self._max_files = max_files

    # -- the user's hand ------------------------------------------------------------

    async def add(self, path: str) -> dict[str, Any]:
        root = self._admissible(path)
        self._db.execute(
            "INSERT OR IGNORE INTO document_root (path, added_at) VALUES (?, ?)",
            (str(root), self._now()),
        )
        # Adding a folder again is the user saying "include it": lift earlier exclusions.
        self._db.execute(
            "UPDATE document SET removed_at = NULL, content_hash = '' "
            "WHERE root = ? AND removed_at IS NOT NULL",  # '' forces a fresh read
            (str(root),),
        )
        report = self._ingest_root(root)
        logger.info(
            "documents added",
            extra=fields(**{k: v for k, v in report.as_dict().items() if k != "skipped"}),
        )
        return {"root": str(root), **report.as_dict()}

    async def remove(self, document_id: str) -> None:
        """Exclude one file: its passages are deleted, and reindexing leaves it alone."""
        row = self._db.query_one("SELECT id FROM document WHERE id = ?", (document_id,))
        if row is None:
            raise NotFoundError("No such document.", document_id=document_id)
        with self._db.transaction() as tx:
            self._drop_chunks(tx, document_id)
            tx.execute(
                "UPDATE document SET removed_at = ?, chunk_count = 0 WHERE id = ?",
                (self._now(), document_id),
            )

    async def forget_root(self, path: str) -> int:
        """Stop reading a folder: every file under it is removed from HEDWIG's index."""
        root = str(Path(path).expanduser().resolve())
        ids = [
            str(r["id"]) for r in self._db.query("SELECT id FROM document WHERE root = ?", (root,))
        ]
        if (
            not ids
            and self._db.query_one("SELECT 1 FROM document_root WHERE path = ?", (root,)) is None
        ):
            raise NotFoundError("HEDWIG is not reading that folder.", path=root)
        with self._db.transaction() as tx:
            for document_id in ids:
                self._drop_chunks(tx, document_id)
                tx.execute("DELETE FROM document WHERE id = ?", (document_id,))
            tx.execute("DELETE FROM document_root WHERE path = ?", (root,))
        return len(ids)

    async def reindex(self) -> dict[str, Any]:
        total = IngestReport()
        for row in self._db.query("SELECT path FROM document_root ORDER BY path"):
            root = Path(str(row["path"]))
            if not root.exists():
                total.skipped.append({"path": str(root), "reason": "folder no longer exists"})
                continue
            report = self._ingest_root(root)
            total.added += report.added
            total.updated += report.updated
            total.unchanged += report.unchanged
            total.removed += report.removed
            total.skipped += report.skipped
        return total.as_dict()

    async def task(self, context: TaskContext) -> TaskResult:
        report = await self.reindex()
        return TaskResult(stats={k: v if k != "skipped" else len(v) for k, v in report.items()})

    def listing(self) -> dict[str, Any]:
        roots = [dict(r) for r in self._db.query("SELECT * FROM document_root ORDER BY path")]
        documents = [
            dict(r)
            for r in self._db.query(
                "SELECT id, root, path, title, bytes, modified_at, ingested_at, chunk_count, "
                "removed_at FROM document ORDER BY path"
            )
        ]
        return {"roots": roots, "documents": documents}

    # -- reading ------------------------------------------------------------------

    def _admissible(self, path: str) -> Path:
        if not path.strip():
            raise InvalidRequestError("Name a folder or a file.")
        root = Path(path).expanduser().resolve()
        if not root.exists():
            raise InvalidRequestError("That path does not exist.", path=str(root))
        refused = (
            root == Path(root.anchor)
            or root == self._home
            or root == self._data_dir
            or self._data_dir in root.parents
            or root in self._data_dir.parents
            or any(part.startswith(".") for part in root.relative_to(root.anchor).parts)
        )
        if refused:
            raise InvalidRequestError(
                "HEDWIG reads only a specific folder of notes, not the whole disk, your whole "
                "home folder, a hidden folder, or its own data.",
                path=str(root),
            )
        files = list(self._walk(root))
        if len(files) > self._max_files:
            raise InvalidRequestError(
                f"That folder has more than {self._max_files} text files. Choose a narrower one.",
                path=str(root),
            )
        return root

    def _walk(self, root: Path) -> Iterator[Path]:
        if root.is_file():
            if root.suffix.lower() in TEXT_SUFFIXES:
                yield root
            return
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root).parts
            if any(part.startswith(".") or part in SKIP_DIRS for part in relative):
                continue
            if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            if path.is_symlink() and root not in path.resolve().parents:
                continue  # never leave the folder the user chose
            yield path

    def _ingest_root(self, root: Path) -> IngestReport:
        report = IngestReport()
        seen: set[str] = set()
        for path in self._walk(root):
            seen.add(str(path))
            self._ingest_file(root, path, report)
        for row in self._db.query(
            "SELECT id, path FROM document WHERE root = ? AND removed_at IS NULL", (str(root),)
        ):
            if str(row["path"]) not in seen:  # deleted from disk: deleted from the index
                with self._db.transaction() as tx:
                    self._drop_chunks(tx, str(row["id"]))
                    tx.execute("DELETE FROM document WHERE id = ?", (row["id"],))
                report.removed += 1
        return report

    def _ingest_file(self, root: Path, path: Path, report: IngestReport) -> None:
        existing = self._db.query_one(
            "SELECT id, content_hash, removed_at FROM document WHERE path = ?", (str(path),)
        )
        if existing is not None and existing["removed_at"] is not None:
            return  # the user excluded it; adding the folder again lifts that
        size = path.stat().st_size
        if size > self._max_bytes:
            report.skipped.append({"path": str(path), "reason": "larger than the size limit"})
            return
        if SECRET_NAMES.search(path.name):
            report.skipped.append({"path": str(path), "reason": "looks like a secrets file"})
            return
        raw = path.read_bytes()
        if b"\x00" in raw[:4096]:
            report.skipped.append({"path": str(path), "reason": "not text"})
            return
        text = raw.decode("utf-8", errors="replace")
        if SECRET_CONTENT.search(text):
            report.skipped.append({"path": str(path), "reason": "contains what looks like a key"})
            return
        digest = hashlib.sha256(raw).hexdigest()
        if existing is not None and existing["content_hash"] == digest:
            report.unchanged += 1
            return

        if path.suffix.lower() in {".html", ".htm"}:
            text = _html_text(text)
        chunks = chunk(text, title=path.stem)
        now = self._now()
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()
        with self._db.transaction() as tx:
            if existing is None:
                document_id = new_id("doc")
                tx.execute(
                    "INSERT INTO document (id, root, path, title, content_hash, mime, bytes, "
                    "modified_at, ingested_at, chunk_count) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        document_id,
                        str(root),
                        str(path),
                        path.stem,
                        digest,
                        _mime(path),
                        size,
                        modified,
                        now,
                        len(chunks),
                    ),
                )
                report.added += 1
            else:
                document_id = str(existing["id"])
                self._drop_chunks(tx, document_id)
                tx.execute(
                    "UPDATE document SET content_hash = ?, bytes = ?, modified_at = ?, "
                    "ingested_at = ?, chunk_count = ? WHERE id = ?",
                    (digest, size, modified, now, len(chunks), document_id),
                )
                report.updated += 1
            for seq, (heading, body) in enumerate(chunks):
                tx.execute(
                    "INSERT INTO document_chunk (id, document_id, seq, text, heading_path, "
                    "token_count, created_at) VALUES (?,?,?,?,?,?,?)",
                    (new_id("chk"), document_id, seq, body, heading, len(body) // 4, now),
                )

    @staticmethod
    def _drop_chunks(tx: Any, document_id: str) -> None:
        tx.execute(
            "DELETE FROM document_embedding WHERE chunk_id IN "
            "(SELECT id FROM document_chunk WHERE document_id = ?)",
            (document_id,),
        )
        tx.execute("DELETE FROM document_chunk WHERE document_id = ?", (document_id,))

    def _now(self) -> str:
        return self._clock.now().isoformat(timespec="milliseconds")


def chunk(text: str, *, title: str) -> list[tuple[str, str]]:
    """Split by headings, then into windows that overlap (docs/13 §4).

    Each passage keeps its heading path ("Tides > Neap tides"), which is what lets HEDWIG say
    where something came from and what lets a passage make sense on its own.
    """
    sections: list[tuple[list[str], list[str]]] = [([title], [])]
    trail: list[tuple[int, str]] = []
    for line in text.splitlines():
        found = _HEADING.match(line)
        if found:
            level = len(found.group(1))
            trail = [(lvl, name) for lvl, name in trail if lvl < level] + [(level, found.group(2))]
            sections.append(([title, *(name for _, name in trail)], []))
        else:
            sections[-1][1].append(line)

    passages: list[tuple[str, str]] = []
    for path, lines in sections:
        body = "\n".join(lines).strip()
        if not body:
            continue
        heading = " > ".join(path)
        start = 0
        while start < len(body):
            end = min(len(body), start + CHUNK_CHARS)
            if end < len(body):  # end on a paragraph or sentence where one is near
                cut = max(body.rfind("\n\n", start, end), body.rfind(". ", start, end))
                if cut > start + CHUNK_CHARS // 2:
                    end = cut + 1
            passages.append((heading, body[start:end].strip()))
            if end >= len(body):
                break
            start = max(end - OVERLAP_CHARS, start + 1)
    return passages


class _TextOnly(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in {"script", "style"}:
            self._skip += 1
        if tag in {"h1", "h2", "h3"} and not self._skip:
            self.parts.append("\n" + "#" * int(tag[1]) + " ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip:
            self._skip -= 1
        if tag in {"p", "div", "li", "h1", "h2", "h3", "br"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def _html_text(html: str) -> str:
    parser = _TextOnly()
    parser.feed(html)
    return re.sub(r"\n{3,}", "\n\n", "".join(parser.parts))


def _mime(path: Path) -> str:
    return {".html": "text/html", ".htm": "text/html"}.get(path.suffix.lower(), "text/plain")
