"""The `StateManager` port (docs/08).

Durable, versioned, namespaced documents with one rule that shapes everything else:

    Exactly one module writes each piece of state. Everyone else reads.

That rule is what makes concurrent background work safe without distributed locking, and
what makes "who changed this?" always answerable (docs/08 §3). Here it is mechanical:
a namespace has a registered owner, and a write from anyone else is rejected.

Updates are expressed as **deltas applied to whatever is current**, never as
"set to this value" (docs/08 §4.1). That is what makes an optimistic-concurrency retry
produce the intuitively right answer instead of clobbering a concurrent change.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

StateValue = Mapping[str, Any]
"""Documents are JSON objects. Anything that needs a schema owns one at its own layer."""

Mutator = Callable[[StateValue], StateValue]
"""Read-modify-write function. Must be pure and idempotent: it may be retried."""


@dataclass(frozen=True, slots=True)
class StateDocument:
    """One versioned document."""

    namespace: str
    key: str
    value: StateValue
    version: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class StateRevision:
    """A historical version of a document, with the reason it changed."""

    namespace: str
    key: str
    version: int
    value: StateValue
    reason: str
    recorded_at: datetime


@dataclass(frozen=True, slots=True)
class StateSnapshot:
    """A point-in-time copy of one or more whole namespaces.

    Generalises the nightly identity snapshot of docs/08 §7.1. The point is selective
    rollback: restoring a month of drifted personality must not touch memory, so snapshots
    are per-namespace rather than whole-database.
    """

    id: str
    label: str
    namespaces: tuple[str, ...]
    taken_at: datetime
    document_count: int


@dataclass(frozen=True, slots=True)
class NamespaceInfo:
    owner: str
    description: str = ""
    keep_history: bool = True
    tags: tuple[str, ...] = field(default_factory=tuple)


@runtime_checkable
class StateManager(Protocol):
    """Durable cognitive and operational state."""

    def register_namespace(self, namespace: str, info: NamespaceInfo) -> None:
        """Claim write ownership of a namespace.

        Called once, at wiring time. A second claim by a different owner is an error:
        two writers for one piece of state is the bug this port exists to prevent.
        """
        ...

    async def get(self, namespace: str, key: str) -> StateDocument | None: ...

    async def get_value(self, namespace: str, key: str, default: StateValue) -> StateValue:
        """Read, falling back to `default` when the document does not exist yet."""
        ...

    async def list_keys(self, namespace: str) -> Sequence[str]: ...

    async def put(
        self,
        namespace: str,
        key: str,
        value: StateValue,
        *,
        owner: str,
        reason: str,
        expected_version: int | None = None,
    ) -> StateDocument:
        """Write a document.

        `expected_version` enables optimistic concurrency: pass the version you read, and
        the write fails rather than silently overwriting a concurrent change. Pass `None`
        only for a genuine blind write, and `0` to require that the document is new.

        Raises `ConflictError` on a version mismatch, `PermissionError` if `owner` does not
        own the namespace.
        """
        ...

    async def mutate(
        self,
        namespace: str,
        key: str,
        mutator: Mutator,
        *,
        owner: str,
        reason: str,
        default: StateValue | None = None,
        max_retries: int = 3,
    ) -> StateDocument:
        """Apply a delta, retrying on conflict.

        The safe way to write Tier-3 state, and the reason `Mutator` must be pure.
        """
        ...

    async def history(
        self, namespace: str, key: str, *, limit: int = 50
    ) -> Sequence[StateRevision]: ...

    async def snapshot(self, label: str, namespaces: Sequence[str]) -> StateSnapshot:
        """Copy whole namespaces so they can be restored later."""
        ...

    async def list_snapshots(self, *, limit: int = 50) -> Sequence[StateSnapshot]: ...

    async def restore(self, snapshot_id: str, *, owner: str) -> int:
        """Restore a snapshot, returning the number of documents written.

        Restoration is itself a versioned write, so the rollback appears in history rather
        than erasing it.
        """
        ...
