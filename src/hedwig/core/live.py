"""Live turn output: a context-scoped sink (ADR-0023).

Whoever runs a turn and wants to watch it — the WebSocket — binds a sink around the call; code
inside the turn emits to it without knowing who, if anyone, is listening. Like correlation
(`core.context`), it follows `await` and spawned tasks.

Ephemeral by design: not the bus, not persisted, not replayed. A sink that fails is dropped for
the rest of its scope, so a closed socket never fails a turn.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from hedwig.core.logging import get_logger

logger = get_logger(__name__)

Sink = Callable[[str, Mapping[str, Any]], Awaitable[None]]


class _Binding:
    __slots__ = ("alive", "sink", "stopped")

    def __init__(self, sink: Sink) -> None:
        self.sink = sink
        self.alive = True
        self.stopped = False

    def stop(self) -> None:
        """The listener asked for the reply to end now (VOI-03). Producers check `live_stopped`."""
        self.stopped = True


LiveHandle = _Binding
"""What `live_scope` yields: the listener's hold on this turn — today, a way to stop it."""


_binding: ContextVar[_Binding | None] = ContextVar("hedwig_live_sink", default=None)


@contextmanager
def live_scope(sink: Sink) -> Iterator[LiveHandle]:
    """Bind `sink` for the duration of the block. Nested scopes restore the outer one."""
    binding = _Binding(sink)
    token = _binding.set(binding)
    try:
        yield binding
    finally:
        _binding.reset(token)


def live_stopped() -> bool:
    """Whether the listener asked this turn's reply to stop (VOI-03)."""
    binding = _binding.get()
    return binding is not None and binding.stopped


def is_live() -> bool:
    """Whether anyone is listening — lets a producer choose to stream at all."""
    binding = _binding.get()
    return binding is not None and binding.alive


async def emit_live(kind: str, payload: Mapping[str, Any]) -> None:
    binding = _binding.get()
    if binding is None or not binding.alive:
        return
    try:
        await binding.sink(kind, payload)
    except Exception:  # the listener went away; the turn carries on regardless
        binding.alive = False
        logger.debug("live sink dropped", extra={"fields": {"kind": kind}})
