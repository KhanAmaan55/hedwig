"""Content-addressed file storage (docs/05 §2).

Bytes on disk, metadata in SQLite. The identifier of a blob is the SHA-256 of its content,
which buys three things for free: deduplication, verifiable integrity, and the absence of
path traversal as a category of bug — a path is derived from a hash, never from input.

Writes are atomic: content goes to a temporary file, is fsynced, and is then renamed into
place. A crash mid-write leaves a temp file, never a half-written blob claiming to be
something it is not.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import AsyncIterator, Sequence
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

from hedwig.core.errors import HedwigError, NotFoundError
from hedwig.core.logging import fields, get_logger
from hedwig.core.ports import BlobRef, Clock, EventBus, Health, HealthStatus, StorageStats
from hedwig.core.store import Database

logger = get_logger(__name__)

CHUNK_SIZE = 1 << 20  # 1 MiB
DIGEST_PREFIX = "sha256:"


class QuotaExceededError(HedwigError):
    """Storing this blob would pass the configured limit."""

    code = "storage_quota_exceeded"
    http_status = 507


class CorruptBlobError(HedwigError):
    """Stored bytes do not match their digest."""

    code = "blob_corrupt"


class ContentAddressedStorage:
    """The `FileStorage` implementation."""

    def __init__(
        self,
        root: Path,
        database: Database,
        *,
        clock: Clock,
        bus: EventBus | None = None,
        quota_bytes: int | None = None,
        max_blob_bytes: int = 256 * 1024 * 1024,
    ) -> None:
        self._root = Path(root)
        self._database = database
        self._clock = clock
        self._bus = bus
        self._quota_bytes = quota_bytes
        self._max_blob_bytes = max_blob_bytes
        self._root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._tmp = self._root / ".tmp"
        self._tmp.mkdir(exist_ok=True, mode=0o700)

    # -- paths -------------------------------------------------------------

    def _path_for(self, digest: str) -> Path:
        hex_digest = digest.removeprefix(DIGEST_PREFIX)
        # Two levels of fan-out: a flat directory with 100k files is slow to list and
        # unpleasant on some filesystems.
        return self._root / hex_digest[:2] / hex_digest[2:4] / hex_digest

    # -- writes ------------------------------------------------------------

    async def put_bytes(
        self, data: bytes, *, media_type: str = "application/octet-stream"
    ) -> BlobRef:
        if len(data) > self._max_blob_bytes:
            raise QuotaExceededError(
                f"blob of {len(data)} bytes exceeds the {self._max_blob_bytes} byte limit",
                size=len(data),
            )
        digest = DIGEST_PREFIX + hashlib.sha256(data).hexdigest()

        existing = self._stat_row(digest)
        if existing is not None:
            self._touch(digest)
            return existing

        self._check_quota(len(data))
        temp = self._tmp / f"put-{digest.removeprefix(DIGEST_PREFIX)[:16]}"
        with temp.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        return self._commit(digest, len(data), media_type, temp)

    async def put_stream(
        self, stream: BinaryIO, *, media_type: str = "application/octet-stream"
    ) -> BlobRef:
        # Streamed content has an unknown digest until it is fully read, so it lands in a
        # temp file first and is renamed once the name is known.
        hasher = hashlib.sha256()
        size = 0
        temp = self._tmp / f"stream-{os.getpid()}-{id(stream):x}"

        try:
            with temp.open("wb") as handle:
                while chunk := stream.read(CHUNK_SIZE):
                    size += len(chunk)
                    if size > self._max_blob_bytes:
                        raise QuotaExceededError(
                            f"stream exceeds the {self._max_blob_bytes} byte limit", size=size
                        )
                    hasher.update(chunk)
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())

            digest = DIGEST_PREFIX + hasher.hexdigest()
            return self._commit(digest, size, media_type, temp)
        finally:
            temp.unlink(missing_ok=True)

    async def put_file(self, path: Path, *, media_type: str | None = None) -> BlobRef:
        resolved = Path(path).expanduser().resolve()
        if not resolved.is_file():
            raise NotFoundError(f"no such file: {path}", path=str(path))
        with resolved.open("rb") as handle:
            return await self.put_stream(
                handle, media_type=media_type or _guess_media_type(resolved)
            )

    def _commit(self, digest: str, size: int, media_type: str, temp: Path) -> BlobRef:
        existing = self._stat_row(digest)
        if existing is not None:
            temp.unlink(missing_ok=True)
            self._touch(digest)
            return existing

        self._check_quota(size)
        destination = self._path_for(digest)
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temp.replace(destination)
        destination.chmod(0o600)

        created_at = self._clock.now()
        self._database.execute(
            "INSERT INTO blob (digest, size_bytes, media_type, created_at, accessed_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT (digest) DO UPDATE SET "
            "ref_count = blob.ref_count + 1",
            (
                digest,
                size,
                media_type,
                created_at.isoformat(timespec="milliseconds"),
                created_at.isoformat(timespec="milliseconds"),
            ),
        )
        logger.debug("blob stored", extra=fields(digest=digest[:19], size_bytes=size))
        self._announce("storage.blob.stored", {"digest": digest, "size_bytes": size})
        return BlobRef(digest=digest, size_bytes=size, media_type=media_type, created_at=created_at)

    # -- reads -------------------------------------------------------------

    async def get_bytes(self, digest: str) -> bytes:
        path = self._require(digest)
        self._touch(digest)
        return path.read_bytes()

    async def open(self, digest: str) -> AsyncIterator[bytes]:
        path = self._require(digest)
        self._touch(digest)

        async def chunks() -> AsyncIterator[bytes]:
            with path.open("rb") as handle:
                while chunk := handle.read(CHUNK_SIZE):
                    yield chunk

        return chunks()

    async def stat(self, digest: str) -> BlobRef | None:
        return self._stat_row(digest)

    async def exists(self, digest: str) -> bool:
        return self._stat_row(digest) is not None and self._path_for(digest).is_file()

    async def verify(self, digest: str) -> bool:
        path = self._path_for(digest)
        if not path.is_file():
            return False
        hasher = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(CHUNK_SIZE):
                hasher.update(chunk)
        return DIGEST_PREFIX + hasher.hexdigest() == digest

    # -- deletion ----------------------------------------------------------

    async def delete(self, digest: str) -> bool:
        row = self._stat_row(digest)
        path = self._path_for(digest)
        if row is None and not path.exists():
            return False

        path.unlink(missing_ok=True)
        self._database.execute("DELETE FROM blob WHERE digest = ?", (digest,))
        self._prune_empty_dirs(path.parent)
        logger.info("blob deleted", extra=fields(digest=digest[:19]))
        self._announce("storage.blob.deleted", {"digest": digest})
        return True

    async def prune_orphans(self) -> Sequence[str]:
        """Reconcile the filesystem and the metadata table.

        Only a crash between the two writes can desynchronise them. That should be visible
        and repaired, not quietly tolerated.
        """
        removed: list[str] = []

        known = {str(row["digest"]) for row in self._database.query("SELECT digest FROM blob")}
        on_disk: set[str] = set()
        for path in self._root.rglob("*"):
            if not path.is_file() or self._tmp in path.parents:
                continue
            on_disk.add(DIGEST_PREFIX + path.name)

        for digest in on_disk - known:
            self._path_for(digest).unlink(missing_ok=True)
            removed.append(f"file:{digest}")
        for digest in known - on_disk:
            self._database.execute("DELETE FROM blob WHERE digest = ?", (digest,))
            removed.append(f"row:{digest}")

        for temp in self._tmp.iterdir():
            temp.unlink(missing_ok=True)
            removed.append(f"tmp:{temp.name}")

        if removed:
            logger.warning("pruned orphaned blobs", extra=fields(count=len(removed)))
        return tuple(removed)

    # -- stats and health --------------------------------------------------

    async def stats(self) -> StorageStats:
        row = self._database.query_one(
            "SELECT COUNT(*) AS n, COALESCE(SUM(size_bytes), 0) AS total FROM blob"
        )
        return StorageStats(
            blob_count=int(row["n"]) if row else 0,
            total_bytes=int(row["total"]) if row else 0,
            quota_bytes=self._quota_bytes,
        )

    async def health(self) -> Health:
        stats = await self.stats()
        status = HealthStatus.OK
        message = ""
        if stats.quota_bytes:
            used = stats.total_bytes / stats.quota_bytes
            if used > 0.9:
                status, message = HealthStatus.DEGRADED, f"{used:.0%} of quota used"
        if not self._root.is_dir():
            status, message = HealthStatus.DOWN, f"blob root {self._root} is missing"

        return Health(
            status=status,
            message=message,
            detail={
                "root": str(self._root),
                "blobs": stats.blob_count,
                "bytes": stats.total_bytes,
                "quota_bytes": stats.quota_bytes,
            },
        )

    # -- helpers -----------------------------------------------------------

    def _require(self, digest: str) -> Path:
        path = self._path_for(digest)
        if not path.is_file():
            raise NotFoundError(f"blob {digest} is not stored", digest=digest)
        return path

    def _stat_row(self, digest: str) -> BlobRef | None:
        row = self._database.query_one("SELECT * FROM blob WHERE digest = ?", (digest,))
        if row is None:
            return None
        return BlobRef(
            digest=str(row["digest"]),
            size_bytes=int(row["size_bytes"]),
            media_type=str(row["media_type"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def _touch(self, digest: str) -> None:
        self._database.execute(
            "UPDATE blob SET accessed_at = ? WHERE digest = ?",
            (self._clock.now().isoformat(timespec="milliseconds"), digest),
        )

    def _check_quota(self, incoming: int) -> None:
        if self._quota_bytes is None:
            return
        row = self._database.query_one("SELECT COALESCE(SUM(size_bytes), 0) AS total FROM blob")
        total = int(row["total"]) if row else 0
        if total + incoming > self._quota_bytes:
            raise QuotaExceededError(
                f"storing {incoming} bytes would exceed the {self._quota_bytes} byte quota",
                used=total,
                quota=self._quota_bytes,
            )

    def _prune_empty_dirs(self, directory: Path) -> None:
        for candidate in (directory, directory.parent):
            if candidate == self._root or not candidate.is_dir():
                return
            if not any(candidate.iterdir()):
                candidate.rmdir()

    def _announce(self, type_: str, payload: dict[str, object]) -> None:
        if self._bus is None:
            return
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # pragma: no cover - synchronous context
            return
        loop.create_task(self._bus.emit(type_, payload, source="core.storage"))  # noqa: RUF006


def _guess_media_type(path: Path) -> str:
    import mimetypes

    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"
