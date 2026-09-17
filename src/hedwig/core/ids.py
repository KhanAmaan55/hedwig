"""ULID generation.

Every identifier in HEDWIG is a ULID stored as text: sortable by creation time, globally
unique without coordination, and readable in a log five years from now (docs/05 §3.1).

Implemented here rather than pulled from a package because identifiers are core
infrastructure, the algorithm is forty lines, and we want a guarantee the specification
leaves optional: **monotonicity within a millisecond**. Two ULIDs created in the same
millisecond still sort in creation order, so `ORDER BY id` is always a valid proxy for
`ORDER BY created_at`.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from typing import Final

# Crockford base32: no I, L, O or U, so identifiers cannot be misread aloud or by eye.
_ALPHABET: Final = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_DECODE: Final = {char: index for index, char in enumerate(_ALPHABET)}

_TIME_LEN: Final = 10
_RANDOM_LEN: Final = 16
ULID_LENGTH: Final = _TIME_LEN + _RANDOM_LEN

_MAX_RANDOM: Final = (1 << 80) - 1
_MAX_TIMESTAMP: Final = (1 << 48) - 1


def _encode(value: int, length: int) -> str:
    chars = [""] * length
    for index in range(length - 1, -1, -1):
        chars[index] = _ALPHABET[value & 0x1F]
        value >>= 5
    return "".join(chars)


class UlidFactory:
    """Thread-safe monotonic ULID factory.

    Takes the clock as a callable returning epoch milliseconds rather than importing
    `Clock`, so `core.ids` stays free of every other module — including its siblings.
    """

    def __init__(self, now_ms: Callable[[], int] | None = None) -> None:
        self._now_ms: Callable[[], int] = now_ms or (lambda: int(time.time() * 1000))
        self._lock = threading.Lock()
        self._last_ms = -1
        self._last_random = 0

    def new(self) -> str:
        timestamp = self._now_ms()
        if not 0 <= timestamp <= _MAX_TIMESTAMP:
            raise ValueError(f"timestamp out of ULID range: {timestamp}")

        with self._lock:
            if timestamp == self._last_ms:
                # Same millisecond: increment instead of re-randomising, so order holds.
                if self._last_random >= _MAX_RANDOM:
                    raise RuntimeError("ULID randomness exhausted within one millisecond")
                self._last_random += 1
            else:
                self._last_ms = timestamp
                self._last_random = int.from_bytes(os.urandom(10), "big")
            randomness = self._last_random

        return _encode(timestamp, _TIME_LEN) + _encode(randomness, _RANDOM_LEN)


_default_factory = UlidFactory()


def new_ulid() -> str:
    """Return a new monotonic ULID using the wall clock."""
    return _default_factory.new()


def new_id(prefix: str) -> str:
    """Return a prefixed identifier, e.g. `turn_01JQ8Z...`.

    Prefixes exist for humans reading logs; they are not parsed for meaning anywhere.
    """
    return f"{prefix}_{new_ulid()}"


def is_ulid(value: str) -> bool:
    """True if `value` is a syntactically valid ULID."""
    return (
        len(value) == ULID_LENGTH
        and all(char in _DECODE for char in value)
        and _DECODE[value[0]] < 8  # first character caps the 48-bit timestamp
    )


def timestamp_ms(value: str) -> int:
    """Extract the embedded epoch-millisecond timestamp from a ULID."""
    if not is_ulid(value):
        raise ValueError(f"not a valid ULID: {value!r}")
    result = 0
    for char in value[:_TIME_LEN]:
        result = (result << 5) | _DECODE[char]
    return result
