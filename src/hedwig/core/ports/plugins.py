"""The `Plugin` and `PluginLoader` ports (ADR-0016).

**Scope, stated first because it is the whole design.** This is an *in-tree, first-party*
module loader. It composes optional parts of HEDWIG that ship in this repository; it is not
an extension point for third-party code.

docs/01 §4 lists "no plugin marketplace, no arbitrary third-party extension surface" as an
explicit non-goal, and docs/13 §8 says any real extension model needs its own threat model.
Nothing here changes that:

* Plugins are loaded only from an explicit allowlist in configuration — never discovered by
  scanning a directory, never downloaded, never installed at runtime.
* A plugin is an ordinary Python module in this repository, reviewed like any other code.
  It runs with full process privileges, and this loader makes **no sandboxing claim**.
* The loader adds ordering, isolation of failures, and a lifecycle. It adds no security
  boundary, because a boundary that does not hold is worse than an absent one.

What it buys: optional subsystems (a speech adapter, a document watcher) can be switched off
by configuration rather than by editing the composition root, and a failure in one leaves
the rest of the system running.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class PluginStatus(StrEnum):
    DISCOVERED = "discovered"
    LOADED = "loaded"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class PluginManifest:
    """What a plugin declares about itself."""

    name: str
    version: str
    description: str = ""
    provides: tuple[str, ...] = field(default_factory=tuple)
    """Service names this plugin registers. Used for ordering and for diagnostics."""
    depends_on: tuple[str, ...] = field(default_factory=tuple)
    """Names of other plugins that must be set up first."""
    required: bool = False
    """A required plugin that fails aborts startup; an optional one degrades it."""


@dataclass(frozen=True, slots=True)
class PluginRecord:
    """The outcome of attempting to load one plugin."""

    manifest: PluginManifest
    module: str
    status: PluginStatus
    error: str | None = None
    load_ms: float = 0.0


@runtime_checkable
class PluginContext(Protocol):
    """What a plugin is handed at setup time.

    Deliberately narrow: configuration, the service registry, the event bus, and a logger.
    A plugin that needs something else asks for it through the registry, which means the
    dependency is visible rather than reached for.
    """

    @property
    def config(self) -> Any:
        """The resolved `Config`. Typed loosely here to keep ports free of pydantic."""
        ...

    @property
    def registry(self) -> Any:
        """The `ServiceRegistry`, for registering what this plugin provides."""
        ...

    @property
    def settings(self) -> Mapping[str, Any]:
        """This plugin's own configuration block, if any."""
        ...

    def logger(self, name: str) -> Any: ...


@runtime_checkable
class Plugin(Protocol):
    """A loadable unit.

    Implemented as a module-level object (or class instance) exported as `PLUGIN`.
    """

    @property
    def manifest(self) -> PluginManifest: ...

    async def setup(self, context: PluginContext) -> None:
        """Register services and prepare resources. Must be idempotent."""
        ...

    async def teardown(self) -> None:
        """Release anything `setup` acquired. Must not raise."""
        ...


@runtime_checkable
class PluginLoader(Protocol):
    """Loads the allowlisted plugins, in dependency order."""

    async def load_all(self) -> Sequence[PluginRecord]:
        """Import, order and set up every allowlisted plugin.

        Never raises for an optional plugin: the failure is recorded in its `PluginRecord`
        and surfaced as a notice. A failing *required* plugin raises.
        """
        ...

    async def unload_all(self) -> None:
        """Tear down in reverse order. Never raises."""
        ...

    def records(self) -> Sequence[PluginRecord]: ...
