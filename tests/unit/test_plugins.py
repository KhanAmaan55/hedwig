"""The plugin loader.

Scope first (ADR-0016): this is an in-tree loader for first-party optional modules, not an
extension surface. The tests that matter are about *ordering* and *failure isolation*,
because those are the only two things the loader actually promises.

There is no sandboxing test because there is no sandbox — see the module docstring for why
claiming one would be worse than the honest absence of one.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from hedwig.core.errors import ConflictError, NotFoundError
from hedwig.core.plugins import AllowlistPluginLoader, PluginError
from hedwig.core.ports import PluginContext, PluginManifest, PluginStatus
from hedwig.core.registry import InMemoryServiceRegistry


class FakePlugin:
    """A plugin that records what happened to it."""

    def __init__(
        self,
        name: str,
        log: list[str],
        *,
        depends_on: tuple[str, ...] = (),
        required: bool = False,
        fail: bool = False,
    ) -> None:
        self.manifest = PluginManifest(
            name=name,
            version="1.0.0",
            provides=(f"{name}-service",),
            depends_on=depends_on,
            required=required,
        )
        self._log = log
        self._fail = fail
        self.context: PluginContext | None = None

    async def setup(self, context: PluginContext) -> None:
        if self._fail:
            raise RuntimeError(f"{self.manifest.name} refuses to set up")
        self.context = context
        self._log.append(f"setup:{self.manifest.name}")
        context.registry.register(f"{self.manifest.name}-service", object())

    async def teardown(self) -> None:
        self._log.append(f"teardown:{self.manifest.name}")


@pytest.fixture
def modules(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Install throwaway modules into `sys.modules` so imports resolve without files."""
    created: list[str] = []

    def install(path: str, plugin: object | None, *, attribute: str = "PLUGIN") -> None:
        module = types.ModuleType(path)
        if plugin is not None:
            setattr(module, attribute, plugin)
        monkeypatch.setitem(sys.modules, path, module)
        created.append(path)

    return install


def _loader(
    paths: list[str], registry: InMemoryServiceRegistry, **kwargs: Any
) -> AllowlistPluginLoader:
    return AllowlistPluginLoader(paths, config=object(), registry=registry, **kwargs)


# -- loading ---------------------------------------------------------------


async def test_an_allowlisted_plugin_is_set_up(
    modules: Any, registry: InMemoryServiceRegistry
) -> None:
    log: list[str] = []
    modules("fake.alpha", FakePlugin("alpha", log))

    records = await _loader(["fake.alpha"], registry).load_all()

    assert log == ["setup:alpha"]
    assert records[0].status is PluginStatus.LOADED
    assert records[0].manifest.name == "alpha"
    assert "alpha-service" in registry.names()


async def test_nothing_is_loaded_that_is_not_on_the_allowlist(
    modules: Any, registry: InMemoryServiceRegistry
) -> None:
    """Nothing is discovered by scanning: the allowlist is the whole input."""
    log: list[str] = []
    modules("fake.alpha", FakePlugin("alpha", log))
    modules("fake.beta", FakePlugin("beta", log))

    await _loader(["fake.alpha"], registry).load_all()

    assert log == ["setup:alpha"]


async def test_plugins_receive_only_their_own_settings(
    modules: Any, registry: InMemoryServiceRegistry
) -> None:
    plugin = FakePlugin("alpha", [])
    modules("fake.alpha", plugin)

    await _loader(
        ["fake.alpha"],
        registry,
        settings={"alpha": {"voice": "piper"}, "beta": {"secret": "not yours"}},
    ).load_all()

    assert plugin.context is not None
    assert plugin.context.settings == {"voice": "piper"}


async def test_loading_twice_is_refused(modules: Any, registry: InMemoryServiceRegistry) -> None:
    modules("fake.alpha", FakePlugin("alpha", []))
    loader = _loader(["fake.alpha"], registry)
    await loader.load_all()

    with pytest.raises(ConflictError):
        await loader.load_all()


# -- ordering --------------------------------------------------------------


async def test_dependencies_are_set_up_first(
    modules: Any, registry: InMemoryServiceRegistry
) -> None:
    log: list[str] = []
    modules("fake.consumer", FakePlugin("consumer", log, depends_on=("provider",)))
    modules("fake.provider", FakePlugin("provider", log))

    # Allowlist order is deliberately the wrong order; dependencies decide.
    await _loader(["fake.consumer", "fake.provider"], registry).load_all()

    assert log == ["setup:provider", "setup:consumer"]


async def test_allowlist_order_is_the_tiebreak(
    modules: Any, registry: InMemoryServiceRegistry
) -> None:
    log: list[str] = []
    for name in ("gamma", "alpha", "beta"):
        modules(f"fake.{name}", FakePlugin(name, log))

    await _loader(["fake.gamma", "fake.alpha", "fake.beta"], registry).load_all()

    assert log == ["setup:gamma", "setup:alpha", "setup:beta"]


async def test_a_missing_dependency_is_named(
    modules: Any, registry: InMemoryServiceRegistry
) -> None:
    modules("fake.consumer", FakePlugin("consumer", [], depends_on=("absent",)))

    with pytest.raises(NotFoundError, match="absent"):
        await _loader(["fake.consumer"], registry).load_all()


async def test_circular_dependencies_are_detected(
    modules: Any, registry: InMemoryServiceRegistry
) -> None:
    modules("fake.a", FakePlugin("a", [], depends_on=("b",)))
    modules("fake.b", FakePlugin("b", [], depends_on=("a",)))

    with pytest.raises(ConflictError, match="circular"):
        await _loader(["fake.a", "fake.b"], registry).load_all()


# -- failure isolation -----------------------------------------------------


async def test_an_optional_plugin_that_fails_leaves_the_rest_running(
    modules: Any, registry: InMemoryServiceRegistry
) -> None:
    log: list[str] = []
    modules("fake.broken", FakePlugin("broken", log, fail=True))
    modules("fake.healthy", FakePlugin("healthy", log))

    records = await _loader(["fake.broken", "fake.healthy"], registry).load_all()

    assert log == ["setup:healthy"]
    by_name = {record.manifest.name: record for record in records}
    assert by_name["broken"].status is PluginStatus.FAILED
    assert "refuses to set up" in (by_name["broken"].error or "")
    assert by_name["healthy"].status is PluginStatus.LOADED


async def test_a_required_plugin_that_fails_aborts_startup(
    modules: Any, registry: InMemoryServiceRegistry
) -> None:
    log: list[str] = []
    modules("fake.essential", FakePlugin("essential", log, required=True, fail=True))

    with pytest.raises(PluginError, match="essential"):
        await _loader(["fake.essential"], registry).load_all()


async def test_a_required_failure_tears_down_what_already_loaded(
    modules: Any, registry: InMemoryServiceRegistry
) -> None:
    log: list[str] = []
    modules("fake.first", FakePlugin("first", log))
    modules("fake.essential", FakePlugin("essential", log, required=True, fail=True))

    with pytest.raises(PluginError):
        await _loader(["fake.first", "fake.essential"], registry).load_all()

    assert log == ["setup:first", "teardown:first"]


async def test_an_unimportable_module_is_recorded_not_raised(
    registry: InMemoryServiceRegistry,
) -> None:
    records = await _loader(["hedwig.does.not.exist"], registry).load_all()

    assert records[0].status is PluginStatus.FAILED
    assert "ModuleNotFoundError" in (records[0].error or "")


async def test_a_module_without_a_plugin_object_is_recorded(
    modules: Any, registry: InMemoryServiceRegistry
) -> None:
    modules("fake.empty", None)

    records = await _loader(["fake.empty"], registry).load_all()

    assert records[0].status is PluginStatus.FAILED
    assert "PLUGIN" in (records[0].error or "")


async def test_an_object_that_is_not_a_plugin_is_rejected(
    modules: Any, registry: InMemoryServiceRegistry
) -> None:
    modules("fake.wrong", object())

    records = await _loader(["fake.wrong"], registry).load_all()

    assert records[0].status is PluginStatus.FAILED
    assert "Plugin protocol" in (records[0].error or "")


# -- teardown --------------------------------------------------------------


async def test_teardown_runs_in_reverse_order(
    modules: Any, registry: InMemoryServiceRegistry
) -> None:
    log: list[str] = []
    modules("fake.provider", FakePlugin("provider", log))
    modules("fake.consumer", FakePlugin("consumer", log, depends_on=("provider",)))

    loader = _loader(["fake.provider", "fake.consumer"], registry)
    await loader.load_all()
    log.clear()
    await loader.unload_all()

    assert log == ["teardown:consumer", "teardown:provider"]


async def test_teardown_never_raises(modules: Any, registry: InMemoryServiceRegistry) -> None:
    class Stubborn(FakePlugin):
        async def teardown(self) -> None:
            raise RuntimeError("will not go quietly")

    modules("fake.stubborn", Stubborn("stubborn", []))
    modules("fake.polite", FakePlugin("polite", []))

    loader = _loader(["fake.stubborn", "fake.polite"], registry)
    await loader.load_all()
    await loader.unload_all()  # must not raise


# -- health ----------------------------------------------------------------


async def test_health_reflects_failures(modules: Any, registry: InMemoryServiceRegistry) -> None:
    from hedwig.core.ports import HealthStatus

    modules("fake.broken", FakePlugin("broken", [], fail=True))
    loader = _loader(["fake.broken"], registry)
    await loader.load_all()

    health = await loader.health()
    assert health.status is HealthStatus.DEGRADED
    assert health.detail["loaded"] == 0


async def test_health_is_ok_with_no_plugins(registry: InMemoryServiceRegistry) -> None:
    from hedwig.core.ports import HealthStatus

    loader = _loader([], registry)
    await loader.load_all()

    health = await loader.health()
    assert health.status is HealthStatus.OK
