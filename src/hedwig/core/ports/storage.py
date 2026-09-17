"""The `FileStorage` port (docs/05 §2).

Blobs live on the filesystem, not in SQLite: large values bloat the page cache and make
backups slow. What lives in the database is metadata, so a blob and its row are written in
one transaction and can never disagree about existence.

Storage is **content-addressed** — the identifier of a blob is the SHA-256 of its bytes.
Three consequences, all of them load-bearing:

* Identical content is stored once, which matters when the same document is ingested twice.
* Integrity is verifiable at any time: re-hash and compare.
* Paths are derived from the hash, never from user input, so path traversal is not a class
  of bug that exists here.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class BlobRef:
    """A handle to stored bytes.

    `digest` is `sha256:<hex>`. It is the whole identity: two blobs with the same digest are
    the same blob.
    """

    digest: str
    size_bytes: int
    media_type: str
    created_at: datetime

    @property
    def short(self) -> str:
        """First 12 hex characters — for logs and UI, never for lookup."""
        return self.digest.removeprefix("sha256:")[:12]


@dataclass(frozen=True, slots=True)
class StorageStats:
    blob_count: int
    total_bytes: int
    quota_bytes: int | None


@runtime_checkable
class FileStorage(Protocol):
    """Content-addressed blob storage."""

    async def put_bytes(
        self, data: bytes, *, media_type: str = "application/octet-stream"
    ) -> BlobRef:
        """Store bytes and return a handle.

        Idempotent: storing identical content twice yields the same `BlobRef` and stores
        one copy. Raises `QuotaExceededError` if the configured limit would be passed.
        """
        ...

    async def put_stream(
        self, stream: BinaryIO, *, media_type: str = "application/octet-stream"
    ) -> BlobRef:
        """Store from a stream without holding the whole payload in memory."""
        ...

    async def put_file(self, path: Path, *, media_type: str | None = None) -> BlobRef:
        """Copy a file into storage. The original is left untouched."""
        ...

    async def get_bytes(self, digest: str) -> bytes:
        """Read a blob whole. Raises `NotFoundError` if it is absent."""
        ...

    async def open(self, digest: str) -> AsyncIterator[bytes]:
        """Stream a blob in chunks."""
        ...

    async def stat(self, digest: str) -> BlobRef | None: ...

    async def exists(self, digest: str) -> bool: ...

    async def delete(self, digest: str) -> bool:
        """Remove a blob. Returns `False` if it was not there.

        Hard deletion, deliberately: this is the mechanism the privacy purge needs
        (docs/21 §7). Reversible forgetting lives in the memory layer, above this one.
        """
        ...

    async def verify(self, digest: str) -> bool:
        """Re-hash the stored bytes and compare. `False` means corruption."""
        ...

    async def stats(self) -> StorageStats: ...

    async def prune_orphans(self) -> Sequence[str]:
        """Remove files with no metadata row and rows with no file.

        Returns what was removed. A crash between the two writes is the only way this can
        happen, and it should be visible rather than quietly tolerated.
        """
        ...
