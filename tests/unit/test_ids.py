from __future__ import annotations

import pytest

from hedwig.core.ids import ULID_LENGTH, UlidFactory, is_ulid, new_id, new_ulid, timestamp_ms


def test_ulid_has_the_documented_shape() -> None:
    value = new_ulid()
    assert len(value) == ULID_LENGTH
    assert is_ulid(value)
    assert value.upper() == value


def test_ulids_sort_by_creation_time() -> None:
    values = [new_ulid() for _ in range(500)]
    assert values == sorted(values)
    assert len(set(values)) == len(values)


def test_ulids_are_monotonic_within_one_millisecond() -> None:
    """The property the ULID spec leaves optional and docs/05 §3.1 depends on."""
    factory = UlidFactory(now_ms=lambda: 1_767_000_000_000)  # frozen clock
    values = [factory.new() for _ in range(1000)]
    assert values == sorted(values)
    assert len(set(values)) == len(values)


def test_timestamp_round_trips() -> None:
    factory = UlidFactory(now_ms=lambda: 1_767_225_600_000)
    assert timestamp_ms(factory.new()) == 1_767_225_600_000


def test_prefixed_ids_keep_a_valid_ulid() -> None:
    value = new_id("turn")
    prefix, _, ulid = value.partition("_")
    assert prefix == "turn"
    assert is_ulid(ulid)


@pytest.mark.parametrize(
    "value",
    ["", "not-a-ulid", "01JQ8Z", "01JQ8Z" + "I" * 20, "Z" * 26],
)
def test_invalid_values_are_rejected(value: str) -> None:
    assert not is_ulid(value)
    with pytest.raises(ValueError):
        timestamp_ms(value)
