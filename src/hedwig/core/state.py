"""State manager: durable, versioned, owned documents (docs/08).

Three mechanisms, each enforcing a rule the documentation states but that would otherwise
depend on everyone remembering it:

* **Namespace ownership** — one writer per piece of state (docs/08 §3). A write from a
  module that does not own the namespace is refused, not merely discouraged.
* **Optimistic concurrency with delta updates** — `mutate()` re-reads and re-applies on
  conflict, which is only correct because updates are deltas rather than absolute values
  (docs/08 §4.1).
* **History and snapshots** — every write is recorded with a reason, and namespaces can be
  snapshotted and restored independently, so rolling back drifted personality never touches
  memory (docs/08 §7.1).

What lives here is the mechanism. Which namespaces exist, and what their documents mean, is
decided by the layers that own them; this module never learns what an emotion is.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from hedwig.core.errors import ConflictError, HedwigError, NotFoundError
from hedwig.core.ids import new_id
from hedwig.core.logging import fields, get_logger
from hedwig.core.ports import (
    Clock,
    EventBus,
    Health,
    HealthStatus,
    Mutator,
    NamespaceInfo,
    StateDocument,
    StateRevision,
    StateSnapshot,
    StateValue,
)
from hedwig.core.store import Database

logger = get_logger(__name__)

HISTORY_REASON_MAX = 200


class OwnershipError(HedwigError):
    """A module tried to write state it does not own."""

    code = "state_not_owned"
    http_status = 403


class SqliteStateManager:
    """The `StateManager` implementation.

    Documents are JSON in SQLite: small, transactional alongside everything else, and
    inspectable with `sqlite3` in five years (docs/05 §3).
    """

    def __init__(
        self,
        database: Database,
        *,
        clock: Clock,
        bus: EventBus | None = None,
    ) -> None:
        self._database = database
        self._clock = clock
        self._bus = bus
        self._namespaces: dict[str, NamespaceInfo] = {}

    # -- namespaces --------------------------------------------------------

    def register_namespace(self, namespace: str, info: NamespaceInfo) -> None:
        existing = self._namespaces.get(namespace)
        if existing is not None and existing.owner != info.owner:
            raise ConflictError(
                f"namespace {namespace!r} is owned by {existing.owner!r}; "
                f"{info.owner!r} cannot claim it. One writer per piece of state (docs/08 §3).",
                namespace=namespace,
            )
        self._namespaces[namespace] = info
        logger.debug("namespace registered", extra=fields(namespace=namespace, owner=info.owner))

    def namespaces(self) -> Mapping[str, NamespaceInfo]:
        return dict(self._namespaces)

    def _authorise(self, namespace: str, owner: str) -> NamespaceInfo:
        info = self._namespaces.get(namespace)
        if info is None:
            raise NotFoundError(
                f"namespace {namespace!r} is not registered; claim it at wiring time",
                namespace=namespace,
            )
        if info.owner != owner:
            raise OwnershipError(
                f"{owner!r} may not write namespace {namespace!r}, owned by {info.owner!r}",
                namespace=namespace,
                owner=info.owner,
                attempted_by=owner,
            )
        return info

    # -- reads -------------------------------------------------------------

    async def get(self, namespace: str, key: str) -> StateDocument | None:
        row = self._database.query_one(
            "SELECT * FROM state_document WHERE namespace = ? AND key = ?", (namespace, key)
        )
        return _to_document(row) if row else None

    async def get_value(self, namespace: str, key: str, default: StateValue) -> StateValue:
        document = await self.get(namespace, key)
        return document.value if document else default

    async def list_keys(self, namespace: str) -> Sequence[str]:
        rows = self._database.query(
            "SELECT key FROM state_document WHERE namespace = ? ORDER BY key", (namespace,)
        )
        return tuple(str(row["key"]) for row in rows)

    async def history(
        self, namespace: str, key: str, *, limit: int = 50
    ) -> Sequence[StateRevision]:
        rows = self._database.query(
            """
            SELECT * FROM state_history
            WHERE namespace = ? AND key = ?
            ORDER BY version DESC LIMIT ?
            """,
            (namespace, key, limit),
        )
        return tuple(
            StateRevision(
                namespace=str(row["namespace"]),
                key=str(row["key"]),
                version=int(row["version"]),
                value=json.loads(row["value"]),
                reason=str(row["reason"]),
                recorded_at=datetime.fromisoformat(row["recorded_at"]),
            )
            for row in rows
        )

    # -- writes ------------------------------------------------------------

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
        info = self._authorise(namespace, owner)
        encoded = _encode(value)
        now = self._clock.now().isoformat(timespec="milliseconds")

        with self._database.transaction() as connection:
            row = connection.execute(
                "SELECT version, created_at FROM state_document WHERE namespace = ? AND key = ?",
                (namespace, key),
            ).fetchone()
            current_version = int(row["version"]) if row else 0
            created_at = str(row["created_at"]) if row else now

            if expected_version is not None and expected_version != current_version:
                raise ConflictError(
                    f"{namespace}/{key} is at version {current_version}, "
                    f"expected {expected_version}",
                    namespace=namespace,
                    key=key,
                    current_version=current_version,
                    expected_version=expected_version,
                )

            new_version = current_version + 1
            if row:
                connection.execute(
                    "UPDATE state_document SET value = ?, version = ?, updated_at = ? "
                    "WHERE namespace = ? AND key = ?",
                    (encoded, new_version, now, namespace, key),
                )
            else:
                connection.execute(
                    "INSERT INTO state_document (namespace, key, value, version, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (namespace, key, encoded, new_version, now, now),
                )

            if info.keep_history:
                connection.execute(
                    "INSERT INTO state_history (id, namespace, key, version, value, reason, "
                    "recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        new_id("rev"),
                        namespace,
                        key,
                        new_version,
                        encoded,
                        reason[:HISTORY_REASON_MAX],
                        now,
                    ),
                )

        document = StateDocument(
            namespace=namespace,
            key=key,
            value=dict(value),
            version=new_version,
            created_at=datetime.fromisoformat(created_at),
            updated_at=datetime.fromisoformat(now),
        )
        await self._announce(document, reason)
        return document

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
        self._authorise(namespace, owner)

        last_error: ConflictError | None = None
        for attempt in range(max_retries + 1):
            current = await self.get(namespace, key)
            if current is None and default is None:
                raise NotFoundError(
                    f"{namespace}/{key} does not exist and no default was given",
                    namespace=namespace,
                    key=key,
                )
            base = current.value if current else dict(default or {})
            version = current.version if current else 0

            try:
                return await self.put(
                    namespace,
                    key,
                    mutator(base),
                    owner=owner,
                    reason=reason,
                    expected_version=version,
                )
            except ConflictError as exc:
                # Someone wrote between our read and our write. Because the mutator is a
                # delta over whatever it is handed, re-reading and re-applying is correct.
                last_error = exc
                logger.debug(
                    "state write conflict, retrying",
                    extra=fields(namespace=namespace, key=key, attempt=attempt + 1),
                )

        raise ConflictError(
            f"{namespace}/{key} could not be updated after {max_retries} retries",
            namespace=namespace,
            key=key,
        ) from last_error

    async def _announce(self, document: StateDocument, reason: str) -> None:
        if self._bus is None:
            return
        await self._bus.emit(
            "state.document.changed",
            {
                "namespace": document.namespace,
                "key": document.key,
                "version": document.version,
                "reason": reason,
            },
            source="core.state",
        )

    # -- snapshots ---------------------------------------------------------

    async def snapshot(self, label: str, namespaces: Sequence[str]) -> StateSnapshot:
        wanted = tuple(namespaces)
        if not wanted:
            raise NotFoundError("a snapshot needs at least one namespace")

        placeholders = ",".join("?" * len(wanted))
        rows = self._database.query(
            f"SELECT namespace, key, value, version FROM state_document "
            f"WHERE namespace IN ({placeholders})",
            wanted,
        )
        payload = [
            {
                "namespace": row["namespace"],
                "key": row["key"],
                "value": json.loads(row["value"]),
                "version": row["version"],
            }
            for row in rows
        ]

        snapshot_id = new_id("snap")
        taken_at = self._clock.now()
        self._database.execute(
            "INSERT INTO state_snapshot (id, label, namespaces, payload, taken_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                snapshot_id,
                label,
                json.dumps(list(wanted)),
                json.dumps(payload, separators=(",", ":")),
                taken_at.isoformat(timespec="milliseconds"),
            ),
        )
        logger.info(
            "state snapshot taken",
            extra=fields(snapshot=snapshot_id, label=label, documents=len(payload)),
        )
        return StateSnapshot(
            id=snapshot_id,
            label=label,
            namespaces=wanted,
            taken_at=taken_at,
            document_count=len(payload),
        )

    async def list_snapshots(self, *, limit: int = 50) -> Sequence[StateSnapshot]:
        rows = self._database.query(
            "SELECT id, label, namespaces, taken_at, "
            "json_array_length(payload) AS documents "
            "FROM state_snapshot ORDER BY taken_at DESC LIMIT ?",
            (limit,),
        )
        return tuple(
            StateSnapshot(
                id=str(row["id"]),
                label=str(row["label"]),
                namespaces=tuple(json.loads(row["namespaces"])),
                taken_at=datetime.fromisoformat(row["taken_at"]),
                document_count=int(row["documents"]),
            )
            for row in rows
        )

    async def restore(self, snapshot_id: str, *, owner: str) -> int:
        row = self._database.query_one("SELECT * FROM state_snapshot WHERE id = ?", (snapshot_id,))
        if row is None:
            raise NotFoundError(f"snapshot {snapshot_id!r} not found", snapshot=snapshot_id)

        for namespace in json.loads(row["namespaces"]):
            self._authorise(namespace, owner)

        documents = json.loads(row["payload"])
        for document in documents:
            # A restore is an ordinary versioned write, so the rollback appears in history
            # rather than erasing it.
            await self.put(
                document["namespace"],
                document["key"],
                document["value"],
                owner=owner,
                reason=f"restored from snapshot {snapshot_id}",
            )

        if self._bus is not None:
            await self._bus.emit(
                "state.snapshot.restored",
                {"snapshot_id": snapshot_id, "documents": len(documents)},
                source="core.state",
            )
        logger.info(
            "state snapshot restored",
            extra=fields(snapshot=snapshot_id, documents=len(documents)),
        )
        return len(documents)

    # -- health ------------------------------------------------------------

    async def health(self) -> Health:
        row = self._database.query_one("SELECT COUNT(*) AS n FROM state_document")
        return Health(
            status=HealthStatus.OK,
            detail={
                "namespaces": len(self._namespaces),
                "documents": int(row["n"]) if row else 0,
            },
        )


def _encode(value: StateValue) -> str:
    try:
        return json.dumps(dict(value), separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ConflictError(f"state value is not JSON-serialisable: {exc}") from exc


def _to_document(row: Any) -> StateDocument:
    return StateDocument(
        namespace=str(row["namespace"]),
        key=str(row["key"]),
        value=json.loads(row["value"]),
        version=int(row["version"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )
