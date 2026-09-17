"""Correlation context.

Every unit of work carries a `correlation_id` from its entry point: `turn_*` for turns,
`job_*` for jobs, `req_*` for bare API requests. It reaches logs, events, and every
database record written along the way, so one query reconstructs the whole unit of work
(docs/19 §3).

Propagation uses `contextvars` rather than threading an argument through every signature:
it survives `await` boundaries and is inherited by spawned tasks, which is exactly the
behaviour needed for background work to trace back to the conversation that caused it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

from hedwig.core.ids import new_id

_correlation_id: ContextVar[str | None] = ContextVar("hedwig_correlation_id", default=None)
_causation_id: ContextVar[str | None] = ContextVar("hedwig_causation_id", default=None)


def current_correlation_id() -> str | None:
    return _correlation_id.get()


def current_causation_id() -> str | None:
    return _causation_id.get()


def new_correlation_id(prefix: str = "req") -> str:
    return new_id(prefix)


@contextmanager
def correlation_scope(
    correlation_id: str | None = None,
    *,
    prefix: str = "req",
    causation_id: str | None = None,
) -> Iterator[str]:
    """Bind a correlation id for the duration of the block.

    Nested scopes are supported; the previous value is restored on exit.
    """
    resolved = correlation_id or new_correlation_id(prefix)
    correlation_token: Token[str | None] = _correlation_id.set(resolved)
    causation_token: Token[str | None] = _causation_id.set(causation_id)
    try:
        yield resolved
    finally:
        _correlation_id.reset(correlation_token)
        _causation_id.reset(causation_token)
