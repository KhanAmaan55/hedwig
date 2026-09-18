"""The event catalogue.

docs/04 §4: an unknown event type is a hard error, not a warning. A typo'd event name that
silently goes nowhere is among the nastiest bugs an event system can have — the publisher
succeeds, no handler runs, and nothing anywhere says so.

So every type is registered before it can be published, with the payload keys it carries.
Registration lives next to the module that publishes the event; the platform's own types
are below.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final

from hedwig.core.errors import InvalidRequestError

# domain.noun.verb_past — three segments, past tense, enforced.
TYPE_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")


class UnknownEventTypeError(InvalidRequestError):
    """An event type that nobody registered."""

    code = "unknown_event_type"


@dataclass(frozen=True, slots=True)
class EventTypeSpec:
    type: str
    description: str
    payload_keys: frozenset[str] = field(default_factory=frozenset)
    """Required payload keys. Kept deliberately light — a full JSON Schema per type is the
    Milestone-3 upgrade (docs/04 §11); requiring the keys catches the common mistake now."""
    schema_version: int = 1


class EventCatalogue:
    """The set of publishable event types."""

    def __init__(self) -> None:
        self._specs: dict[str, EventTypeSpec] = {}

    def register(
        self,
        type_: str,
        description: str,
        *,
        payload_keys: frozenset[str] | set[str] | tuple[str, ...] = (),
        schema_version: int = 1,
    ) -> EventTypeSpec:
        if not TYPE_PATTERN.match(type_):
            raise InvalidRequestError(
                f"event type must be domain.noun.verb_past, got {type_!r}", type=type_
            )
        existing = self._specs.get(type_)
        spec = EventTypeSpec(
            type=type_,
            description=description,
            payload_keys=frozenset(payload_keys),
            schema_version=schema_version,
        )
        if existing is not None and existing != spec:
            raise InvalidRequestError(
                f"event type {type_!r} is already registered with a different definition"
            )
        self._specs[type_] = spec
        return spec

    def get(self, type_: str) -> EventTypeSpec:
        try:
            return self._specs[type_]
        except KeyError:
            raise UnknownEventTypeError(
                f"event type {type_!r} is not registered. Register it in the module that "
                "publishes it, and add a row to docs/04 §5.",
                type=type_,
            ) from None

    def validate(self, type_: str, payload: object) -> EventTypeSpec:
        spec = self.get(type_)
        if spec.payload_keys:
            keys = set(payload.keys()) if isinstance(payload, dict) else set()
            missing = spec.payload_keys - keys
            if missing:
                raise InvalidRequestError(
                    f"event {type_!r} is missing payload keys: {sorted(missing)}",
                    type=type_,
                    missing=sorted(missing),
                )
        return spec

    def __contains__(self, type_: str) -> bool:
        return type_ in self._specs

    def __len__(self) -> int:
        return len(self._specs)

    def types(self) -> tuple[str, ...]:
        return tuple(sorted(self._specs))


def platform_catalogue() -> EventCatalogue:
    """The event types the platform layer itself publishes.

    Higher layers add theirs at wiring time. Keeping this a function rather than a module
    global means tests get a clean catalogue instead of inheriting one.
    """
    catalogue = EventCatalogue()

    catalogue.register(
        "system.startup.completed", "The process finished starting.", payload_keys={"version"}
    )
    catalogue.register("system.shutdown.started", "The process began shutting down.")
    catalogue.register(
        "system.config.changed",
        "One or more hot-reloadable settings changed.",
        payload_keys={"changes", "reason"},
    )
    catalogue.register(
        "system.service.started", "A registered service started.", payload_keys={"service"}
    )
    catalogue.register(
        "system.service.failed",
        "A registered service failed to start or stop.",
        payload_keys={"service", "error"},
    )
    catalogue.register(
        "system.plugin.loaded", "A plugin was set up.", payload_keys={"plugin", "version"}
    )
    catalogue.register(
        "system.plugin.failed", "A plugin failed to load.", payload_keys={"plugin", "error"}
    )
    # Conversation (docs/04 §5.1, docs/26 §7.1)
    catalogue.register(
        "conversation.session.started",
        "A session was opened.",
        payload_keys={"session_id", "channel"},
    )
    catalogue.register(
        "conversation.message.received",
        "A message arrived from the user.",
        payload_keys={"session_id", "message_id", "text", "trust"},
    )
    catalogue.register(
        "conversation.reply.produced",
        "HEDWIG produced a reply.",
        payload_keys={"session_id", "message_id", "text"},
    )
    catalogue.register(
        "conversation.turn.completed",
        "A turn finished. Carries the turn's substance, not only its identifiers, so "
        "capture never reads back a row that finalize has not written yet (ADR-0018).",
        payload_keys={"turn_id", "session_id", "status"},
    )
    catalogue.register(
        "perception.input.appraised",
        "The guard classified an input. Carries the verdict, never the content, so no "
        "lexical judgement of a person reaches emotion through a side door (docs/07 §14.3).",
        payload_keys={"allowed", "trust", "injection_score"},
    )
    # Memory (docs/04 §5.2)
    catalogue.register(
        "memory.episode.stored",
        "An episodic memory was written.",
        payload_keys={"memory_id", "salience"},
    )
    catalogue.register(
        "memory.belief.formed",
        "A semantic memory was written.",
        payload_keys={"memory_id", "statement", "confidence"},
    )
    catalogue.register(
        "memory.belief.superseded",
        "A belief was replaced by a newer one.",
        payload_keys={"old_id", "new_id", "reason"},
    )
    catalogue.register(
        "memory.item.reinforced",
        "One or more memories gained salience.",
        payload_keys={"memory_ids", "amount", "cause"},
    )
    catalogue.register(
        "memory.item.forgotten",
        "A memory was tombstoned.",
        payload_keys={"memory_id", "reason"},
    )
    catalogue.register(
        "memory.entity.discovered",
        "A new entity was seen for the first time.",
        payload_keys={"entity_id", "kind", "name"},
    )
    # Emotion (docs/04 §5.3, docs/09 §5.2)
    catalogue.register(
        "emotion.state.changed",
        "The emotional state moved enough to be worth saying so. Carries a full snapshot, "
        "never a delta, so a subscriber that misses one is not left with half a state.",
        payload_keys={"state", "cause"},
    )
    catalogue.register(
        "emotion.threshold.crossed",
        "A dimension entered or left a band. Hysteresis stops it flapping on the boundary.",
        payload_keys={"dimension", "direction", "value"},
    )
    catalogue.register(
        "state.document.changed",
        "A state document was written.",
        payload_keys={"namespace", "key", "version", "reason"},
    )
    catalogue.register(
        "state.snapshot.restored",
        "A state snapshot was restored.",
        payload_keys={"snapshot_id", "documents"},
    )
    catalogue.register(
        "scheduler.task.started", "A scheduled task began.", payload_keys={"task", "run_id"}
    )
    catalogue.register(
        "scheduler.task.completed",
        "A scheduled task finished.",
        payload_keys={"task", "run_id", "status"},
    )
    catalogue.register(
        "llm.model.switched",
        "A tier was pointed at a different model.",
        payload_keys={"tier", "previous", "current", "reason"},
    )
    catalogue.register(
        "storage.blob.stored", "A blob was written.", payload_keys={"digest", "size_bytes"}
    )
    catalogue.register("storage.blob.deleted", "A blob was removed.", payload_keys={"digest"})

    return catalogue
