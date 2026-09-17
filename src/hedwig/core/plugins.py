"""Plugin loader: optional first-party modules, composed by configuration (ADR-0016).

**Read the scope before the code.** docs/01 §4 lists "no plugin marketplace, no arbitrary
third-party extension surface" as an explicit non-goal, and this module does not change
that. It is an *in-tree* loader:

* Plugins are named explicitly in configuration. Nothing is discovered by scanning a
  directory, downloaded, or installed at runtime.
* A plugin is an ordinary Python module in this repository, reviewed like any other code.
* It runs with full process privileges. **There is no sandbox and no security boundary**,
  and pretending otherwise would be worse than the honest absence of one — a boundary that
  does not hold is a boundary people rely on.

What it buys is real but modest: an optional subsystem can be switched off by configuration
instead of by editing the composition root, its failure is isolated from the rest of
startup, and its lifecycle is managed rather than improvised.

Load order is a topological sort over declared dependencies, so a plugin that provides a
service another needs is set up first.
"""

from __future__ import annotations

import importlib
import time
from collections.abc import Mapping, Sequence
from typing import Any

from hedwig.core.errors import ConflictError, HedwigError, NotFoundError
from hedwig.core.logging import fields, get_logger
from hedwig.core.ports import (
    Health,
    HealthStatus,
    Plugin,
    PluginManifest,
    PluginRecord,
    PluginStatus,
    ServiceRegistry,
)

logger = get_logger(__name__)

PLUGIN_ATTRIBUTE = "PLUGIN"
"""The module-level object a plugin module must export."""


class PluginError(HedwigError):
    """A plugin could not be imported, or refused to set up."""

    code = "plugin_failed"


class SimplePluginContext:
    """What a plugin is handed at setup time.

    Deliberately narrow (`PluginContext` in ports/plugins.py): configuration, the registry,
    its own settings block, and a logger factory. A plugin that needs anything else asks the
    registry for it by name, which makes the dependency visible in code review instead of
    reached for in an import.
    """

    __slots__ = ("_config", "_name", "_registry", "_settings")

    def __init__(
        self,
        *,
        name: str,
        config: Any,
        registry: ServiceRegistry,
        settings: Mapping[str, Any],
    ) -> None:
        self._name = name
        self._config = config
        self._registry = registry
        self._settings = dict(settings)

    @property
    def config(self) -> Any:
        return self._config

    @property
    def registry(self) -> ServiceRegistry:
        return self._registry

    @property
    def settings(self) -> Mapping[str, Any]:
        return self._settings

    def logger(self, name: str) -> Any:
        return get_logger(f"hedwig.plugins.{self._name}.{name}")


class AllowlistPluginLoader:
    """The `PluginLoader` implementation.

    `modules` is the allowlist: a sequence of importable module paths, in any order. Order
    on disk is not order of setup — dependencies decide that.
    """

    def __init__(
        self,
        modules: Sequence[str],
        *,
        config: Any,
        registry: ServiceRegistry,
        settings: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        self._modules = tuple(modules)
        self._config = config
        self._registry = registry
        self._settings = dict(settings or {})
        self._records: list[PluginRecord] = []
        self._loaded: list[tuple[str, Plugin]] = []

    # -- loading -----------------------------------------------------------

    async def load_all(self) -> Sequence[PluginRecord]:
        if self._records:
            raise ConflictError("plugins are already loaded")

        discovered = self._import_all()
        ordered = _resolve_order(discovered)

        for module_path, plugin in ordered:
            manifest = plugin.manifest
            started = time.monotonic()
            try:
                await plugin.setup(
                    SimplePluginContext(
                        name=manifest.name,
                        config=self._config,
                        registry=self._registry,
                        settings=self._settings.get(manifest.name, {}),
                    )
                )
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                self._records.append(
                    PluginRecord(
                        manifest=manifest,
                        module=module_path,
                        status=PluginStatus.FAILED,
                        error=error,
                        load_ms=_elapsed_ms(started),
                    )
                )
                logger.error(
                    "plugin failed to set up",
                    extra=fields(plugin=manifest.name, module=module_path, error=error),
                )
                if manifest.required:
                    # A required plugin that cannot start is a broken installation, not a
                    # degraded one. Everything already set up is torn down first.
                    await self.unload_all()
                    raise PluginError(
                        f"required plugin {manifest.name!r} failed: {error}",
                        plugin=manifest.name,
                    ) from exc
                continue

            self._loaded.append((module_path, plugin))
            self._records.append(
                PluginRecord(
                    manifest=manifest,
                    module=module_path,
                    status=PluginStatus.LOADED,
                    load_ms=_elapsed_ms(started),
                )
            )
            logger.info(
                "plugin loaded",
                extra=fields(
                    plugin=manifest.name,
                    version=manifest.version,
                    provides=list(manifest.provides),
                ),
            )

        return tuple(self._records)

    def _import_all(self) -> list[tuple[str, Plugin]]:
        imported: list[tuple[str, Plugin]] = []

        for module_path in self._modules:
            try:
                module = importlib.import_module(module_path)
            except Exception as exc:
                self._record_import_failure(module_path, f"{type(exc).__name__}: {exc}")
                continue

            plugin = getattr(module, PLUGIN_ATTRIBUTE, None)
            if plugin is None:
                self._record_import_failure(
                    module_path, f"module exports no {PLUGIN_ATTRIBUTE!r} object"
                )
                continue
            if not isinstance(plugin, Plugin):
                self._record_import_failure(
                    module_path,
                    f"{PLUGIN_ATTRIBUTE} does not satisfy the Plugin protocol "
                    "(needs manifest, setup, teardown)",
                )
                continue

            imported.append((module_path, plugin))

        return imported

    def _record_import_failure(self, module_path: str, error: str) -> None:
        self._records.append(
            PluginRecord(
                manifest=PluginManifest(name=module_path, version="unknown"),
                module=module_path,
                status=PluginStatus.FAILED,
                error=error,
            )
        )
        logger.error("plugin could not be imported", extra=fields(module=module_path, error=error))

    # -- unloading ---------------------------------------------------------

    async def unload_all(self) -> None:
        """Tear down in reverse order. Never raises: shutdown has to finish."""
        for module_path, plugin in reversed(self._loaded):
            try:
                await plugin.teardown()
            except Exception as exc:
                logger.error(
                    "plugin teardown failed",
                    extra=fields(module=module_path, error=f"{type(exc).__name__}: {exc}"),
                )
        self._loaded.clear()

    # -- introspection -----------------------------------------------------

    def records(self) -> Sequence[PluginRecord]:
        return tuple(self._records)

    async def health(self) -> Health:
        failed = [r for r in self._records if r.status is PluginStatus.FAILED]
        required_failed = [r for r in failed if r.manifest.required]

        status = HealthStatus.OK
        if required_failed:
            status = HealthStatus.DOWN
        elif failed:
            status = HealthStatus.DEGRADED

        return Health(
            status=status,
            message=f"{len(failed)} plugin(s) failed" if failed else "",
            detail={
                "configured": len(self._modules),
                "loaded": len(self._loaded),
                "failed": [{"plugin": r.manifest.name, "error": r.error} for r in failed],
            },
        )


# -- ordering --------------------------------------------------------------


def _resolve_order(plugins: Sequence[tuple[str, Plugin]]) -> list[tuple[str, Plugin]]:
    """Topological sort by declared dependency, allowlist order as the tiebreak.

    A stable tiebreak matters: a load order that varies between runs turns an ordering bug
    into an intermittent one.
    """
    by_name = {plugin.manifest.name: (module, plugin) for module, plugin in plugins}
    position = {plugin.manifest.name: index for index, (_, plugin) in enumerate(plugins)}
    unresolved = {plugin.manifest.name: set(plugin.manifest.depends_on) for _, plugin in plugins}

    for name, dependencies in unresolved.items():
        missing = dependencies - by_name.keys()
        if missing:
            raise NotFoundError(
                f"plugin {name!r} depends on plugin(s) that are not enabled: {sorted(missing)}",
                plugin=name,
                missing=sorted(missing),
            )

    ordered: list[tuple[str, Plugin]] = []
    while unresolved:
        ready = sorted(
            (name for name, deps in unresolved.items() if not deps), key=lambda n: position[n]
        )
        if not ready:
            raise ConflictError(
                f"circular plugin dependency among {sorted(unresolved)}",
                plugins=sorted(unresolved),
            )
        for name in ready:
            ordered.append(by_name[name])
            del unresolved[name]
        for dependencies in unresolved.values():
            dependencies.difference_update(ready)
    return ordered


def _elapsed_ms(started: float) -> float:
    return round((time.monotonic() - started) * 1000, 2)
