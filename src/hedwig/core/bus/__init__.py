"""Event bus: the durable in-process publish/subscribe implementation.

The rule that governs its use is in docs/04 §2 — commands are direct calls, events are
published facts. Nothing here makes that rule true; it is a review discipline. What this
package provides is a bus good enough that following the rule is never the slow path.
"""

from __future__ import annotations

from hedwig.core.bus.catalogue import (
    EventCatalogue,
    EventTypeSpec,
    UnknownEventTypeError,
    platform_catalogue,
)
from hedwig.core.bus.in_process import EventStormError, InProcessBus

__all__ = [
    "EventCatalogue",
    "EventStormError",
    "EventTypeSpec",
    "InProcessBus",
    "UnknownEventTypeError",
    "platform_catalogue",
]
