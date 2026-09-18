"""The composition root.

What is worth asserting about `build_container` is structural: it types its fields against
ports, it builds without doing anything, and the lifecycle it hands to the registry starts
and stops in the right order.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from hedwig.core.clock import FakeClock, SystemClock
from hedwig.core.config import Config, load_config
from hedwig.core.ports import (
    Clock,
    ConfigManager,
    EventBus,
    FileStorage,
    PluginLoader,
    ServiceRegistry,
    StateManager,
    TaskScheduler,
)
from hedwig.core.store import Database
from hedwig.wiring import Container, build_container

PORT_TYPED_FIELDS = {
    "clock": Clock,
    "registry": ServiceRegistry,
    "config_manager": ConfigManager,
    "bus": EventBus,
    "state": StateManager,
    "storage": FileStorage,
    "scheduler": TaskScheduler,
    "plugins": PluginLoader,
}


def test_container_is_frozen(container: Container) -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        container.version = "9.9.9"  # type: ignore[misc]


def test_every_service_is_typed_against_a_port() -> None:
    """A concrete type here would mean something cannot be replaced (docs/03 §6)."""
    annotations = {field.name: field.type for field in dataclasses.fields(Container)}

    for name, port in PORT_TYPED_FIELDS.items():
        declared = annotations[name]
        assert declared in (port, port.__name__), f"{name} is not typed against its port"


def test_the_wired_services_satisfy_their_ports(container: Container) -> None:
    for name, port in PORT_TYPED_FIELDS.items():
        assert isinstance(getattr(container, name), port), f"{name} does not satisfy {port}"


def test_container_defaults_to_real_implementations(tmp_path: Path) -> None:
    config = load_config(
        runtime={"environment": "test", "data_dir": str(tmp_path)},
        logging={"file_enabled": False},
    )
    container = build_container(config, database=Database(":memory:"), migrate=True)

    assert isinstance(container.clock, SystemClock)
    assert isinstance(container.config, Config)
    container.database.close()


def test_construction_does_no_work(container: Container) -> None:
    """Building the graph must not start anything: that is `start`'s job."""
    assert container.uptime_seconds() == 0
    assert list(container.registry.names()) == [
        "config",
        "bus",
        "state",
        "storage",
        "scheduler",
        "llm",
        "sessions",
        "memory",
        "maintenance",
        "emotion",
        "brain",
    ]
    assert container.plugins.records() == ()


def test_services_declare_their_dependencies(container: Container) -> None:
    depends = {info.name: info.depends_on for info in container.registry.info()}

    assert depends["bus"] == ("config",)
    assert depends["state"] == ("bus",)
    assert depends["storage"] == ("bus",)


def test_the_scheduler_can_be_switched_off(tmp_path: Path, clock: FakeClock) -> None:
    config = load_config(
        runtime={"environment": "test", "data_dir": str(tmp_path)},
        logging={"file_enabled": False},
        scheduler={"enabled": False},
    )
    container = build_container(config, clock=clock, database=Database(":memory:"))

    assert "scheduler" not in container.registry.names()
    container.database.close()


def test_uptime_is_measured_monotonically(container: Container, clock: FakeClock) -> None:
    assert container.uptime_seconds() == 0
    clock.advance(minutes=90)
    assert container.uptime_seconds() == 5400


async def test_start_then_stop_is_clean(running_container: Container) -> None:
    statuses = {info.name: info.status.value for info in running_container.registry.info()}
    assert set(statuses.values()) == {"running"}
