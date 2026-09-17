"""The `ConfigManager` port (docs/02 §7, docs/17 §8).

Configuration is resolved once at startup and frozen. This port covers what happens after
that: reading the effective values, knowing *where each one came from*, and changing the
few that are safe to change while running.

The provenance part is not a nicety. "Why is this setting not taking effect?" is one of the
most common and most annoying questions in any layered-configuration system, and answering
it costs one string per field (docs/17 §8).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class ConfigSource(StrEnum):
    """Where a value came from, lowest precedence first."""

    DEFAULT = "default"
    FILE = "file"
    LOCAL_FILE = "local_file"
    DOTENV = "dotenv"
    ENVIRONMENT = "environment"
    OVERRIDE = "override"
    RUNTIME = "runtime"


@dataclass(frozen=True, slots=True)
class ConfigEntry:
    """One resolved setting."""

    path: str
    """Dotted path, e.g. `api.port`."""
    value: Any
    source: ConfigSource
    hot: bool
    """Whether this key can change without a restart."""


@dataclass(frozen=True, slots=True)
class ConfigChange:
    path: str
    old: Any
    new: Any
    reason: str


ConfigListener = Callable[[Sequence[ConfigChange]], None]


@runtime_checkable
class ConfigManager(Protocol):
    """Access to the resolved configuration, and controlled runtime updates."""

    @property
    def current(self) -> Any:
        """The frozen `Config`. Typed loosely so this port stays free of pydantic."""
        ...

    def entries(self) -> Sequence[ConfigEntry]:
        """Every setting with its value, source and hot-reloadability."""
        ...

    def provenance(self, path: str) -> ConfigEntry:
        """Where one setting came from. Raises `NotFoundError` for an unknown path."""
        ...

    def snapshot(self) -> Mapping[str, Any]:
        """A JSON-safe view, suitable for `/v1/config` and for logs."""
        ...

    def apply(self, patch: Mapping[str, Any], *, reason: str) -> Sequence[ConfigChange]:
        """Change hot-reloadable settings at runtime.

        The patch is validated against the full config model before anything is applied, so
        an invalid patch changes nothing. Attempting to change a key that is not
        hot-reloadable raises `InvalidRequestError` naming the key — silently ignoring it
        would be worse than refusing.
        """
        ...

    def reload(self, *, reason: str = "manual reload") -> Sequence[ConfigChange]:
        """Re-read the configuration files, applying only hot-reloadable differences."""
        ...

    def subscribe(self, listener: ConfigListener) -> Callable[[], None]:
        """Be told when settings change. Returns an unsubscribe callable."""
        ...
