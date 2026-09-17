"""The `EventBus` port.

These tests pin the *shape* of the contract, separately from any implementation, so that a
change to the port is a deliberate act rather than a side effect of editing the bus.
`InProcessBus` is verified against the behaviour in tests/unit/test_event_bus.py.
"""

from __future__ import annotations

import dataclasses
import inspect
from datetime import UTC, datetime

import pytest

from hedwig.core.ports import Delivery, Event, EventBus, Subscription


def test_event_is_immutable() -> None:
    """Value objects crossing a port are frozen (docs/03 §2 rule 4)."""
    event = Event(
        id="01JQ",
        type="conversation.message.received",
        occurred_at=datetime.now(UTC),
        source="api",
        correlation_id="turn_01JQ",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        event.type = "something.else.happened"  # type: ignore[misc]


def test_event_defaults_are_sane() -> None:
    event = Event(
        id="01JQ",
        type="memory.episode.stored",
        occurred_at=datetime.now(UTC),
        source="memory",
        correlation_id="job_01JQ",
    )
    assert event.payload == {}
    assert event.causation_id is None
    assert event.principal_id == "local"
    assert event.schema_version == 1


def test_delivery_declares_whether_loss_is_acceptable() -> None:
    """Every subscriber answers this at registration time, in code review (docs/04 §7)."""
    assert {member.value for member in Delivery} == {"at_least_once", "lossy"}


def test_bus_exposes_the_documented_operations() -> None:
    assert set(_public_methods(EventBus)) == {"publish", "emit", "subscribe", "replay", "drain"}
    assert set(_public_methods(Subscription)) == {"unsubscribe"}
    assert set(_public_properties(Subscription)) == {"name", "pattern"}


def test_publish_returns_nothing() -> None:
    """A publisher never learns whether anyone handled its event (docs/04 §2)."""
    signature = inspect.signature(EventBus.publish)
    assert signature.return_annotation in ("None", None)


def test_subscribe_requires_a_stable_name() -> None:
    """The name keys the delivery cursor and the idempotency record across restarts."""
    parameters = inspect.signature(EventBus.subscribe).parameters
    assert parameters["name"].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters["name"].default is inspect.Parameter.empty
    assert parameters["delivery"].default is Delivery.AT_LEAST_ONCE


def _public_methods(protocol: type) -> list[str]:
    return [
        name
        for name, member in vars(protocol).items()
        if not name.startswith("_") and inspect.isfunction(member)
    ]


def _public_properties(protocol: type) -> list[str]:
    return [
        name
        for name, member in vars(protocol).items()
        if not name.startswith("_") and isinstance(member, property)
    ]
