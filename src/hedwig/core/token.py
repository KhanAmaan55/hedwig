"""The API bearer token, as a file (ADR-0022, docs/21 §4).

Platform rather than API code because three different processes use it: the backend checks
it, the CLI presents it, and the Electron main process hands it to the renderer. None of
them should have to import FastAPI to find out what the token is.

Three properties, each preventing a specific failure:

* **Created with `0600` atomically.** The file is opened with `O_CREAT | O_EXCL` and the
  mode set at creation, so there is no window in which another account could read a
  world-readable token. Rotation writes a sibling and `os.replace`s it for the same reason.
* **Re-read when it changes.** The cache is keyed on the file's identity (inode, size,
  modification time), so `hedwig token rotate` is honoured on the next request without a
  restart — and costs one `stat` per check otherwise.
* **Fails closed.** A missing or empty file matches nothing. A token check that falls back to
  "allow" when its file disappears is not a check.
"""

from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path

TOKEN_BYTES = 32
"""docs/21 §4: 32 bytes of CSPRNG. `token_urlsafe` encodes them as 43 URL-safe characters."""

FILE_MODE = 0o600


class TokenFile:
    """Reads, creates and rotates the token at one path."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._cache_key: tuple[int, int, int] | None = None
        self._cached: str | None = None

    @property
    def path(self) -> Path:
        return self._path

    def ensure(self) -> str:
        """Return the token, creating it on first run. Idempotent."""
        existing = self.read()
        if existing:
            return existing
        self._path.parent.mkdir(parents=True, exist_ok=True)
        token = secrets.token_urlsafe(TOKEN_BYTES)
        try:
            descriptor = os.open(self._path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, FILE_MODE)
        except FileExistsError:
            # Another process created it between our read and our open. Theirs wins.
            return self.read() or self.rotate()
        with os.fdopen(descriptor, "w", encoding="ascii") as handle:
            handle.write(token + "\n")
        return token

    def read(self) -> str | None:
        """The current token, or None when there is no usable file."""
        try:
            info = self._path.stat()
        except FileNotFoundError:
            self._cache_key, self._cached = None, None
            return None

        key = (info.st_ino, info.st_size, info.st_mtime_ns)
        if key != self._cache_key:
            text = self._path.read_text(encoding="ascii").strip()
            self._cache_key, self._cached = key, text or None
        return self._cached

    def rotate(self) -> str:
        """Replace the token. Every client holding the old one is refused from now on."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        token = secrets.token_urlsafe(TOKEN_BYTES)
        staging = self._path.with_name(f".{self._path.name}.{secrets.token_hex(4)}")
        descriptor = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL, FILE_MODE)
        try:
            with os.fdopen(descriptor, "w", encoding="ascii") as handle:
                handle.write(token + "\n")
            staging.replace(self._path)
        finally:
            staging.unlink(missing_ok=True)
        return token

    def matches(self, presented: str) -> bool:
        """Constant-time comparison against the current token. False if there is none."""
        current = self.read()
        if not current or not presented:
            return False
        return secrets.compare_digest(current.encode(), presented.encode())

    def is_private(self) -> bool:
        """Whether only the owning account can read the file. For `hedwig doctor` and tests."""
        try:
            mode = stat.S_IMODE(self._path.stat().st_mode)
        except FileNotFoundError:
            return False
        return mode & 0o077 == 0
