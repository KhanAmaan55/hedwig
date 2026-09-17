"""The configuration manager.

Two things are worth testing here: that provenance tells the truth, and that the hot/cold
distinction is enforced rather than advertised. A setting that appears to change and does
not is worse than one that refuses.
"""

from __future__ import annotations

import pytest

from hedwig.core.config import load_config
from hedwig.core.config_manager import HOT_PATHS, LayeredConfigManager
from hedwig.core.errors import InvalidRequestError, NotFoundError
from hedwig.core.ports import ConfigChange, ConfigSource


@pytest.fixture
def manager() -> LayeredConfigManager:
    return LayeredConfigManager(load_config(logging={"file_enabled": False}))


# -- reading ---------------------------------------------------------------


def test_entries_cover_every_setting(manager: LayeredConfigManager) -> None:
    paths = {entry.path for entry in manager.entries()}

    assert "api.port" in paths
    assert "logging.level" in paths
    assert "runtime.data_dir" in paths


def test_hot_settings_are_marked(manager: LayeredConfigManager) -> None:
    by_path = {entry.path: entry for entry in manager.entries()}

    assert by_path["logging.level"].hot is True
    assert by_path["api.port"].hot is False


def test_provenance_reports_the_file_layer(manager: LayeredConfigManager) -> None:
    """`config/hedwig.toml` sets api.port, so that is where the value comes from."""
    entry = manager.provenance("api.port")

    assert entry.value == 8730
    assert entry.source is ConfigSource.FILE


def test_provenance_reports_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HEDWIG_API__PORT", "9999")
    manager = LayeredConfigManager(load_config(logging={"file_enabled": False}))

    entry = manager.provenance("api.port")

    assert entry.value == 9999
    assert entry.source is ConfigSource.ENVIRONMENT


def test_provenance_of_an_unknown_path_raises(manager: LayeredConfigManager) -> None:
    with pytest.raises(NotFoundError):
        manager.provenance("api.nonsense")


def test_provenance_agrees_with_the_resolved_value(manager: LayeredConfigManager) -> None:
    """The layer walk is a second pass over the same inputs; it must not disagree."""
    resolved = manager.current.model_dump(mode="json")

    assert manager.provenance("api.host").value == resolved["api"]["host"]
    assert manager.provenance("logging.level").value == resolved["logging"]["level"]


def test_snapshot_is_json_safe(manager: LayeredConfigManager) -> None:
    import json

    json.dumps(manager.snapshot())


# -- applying --------------------------------------------------------------


def test_a_hot_setting_can_be_changed(manager: LayeredConfigManager) -> None:
    changes = manager.apply({"logging.level": "debug"}, reason="test")

    assert [change.path for change in changes] == ["logging.level"]
    assert manager.current.logging.level == "debug"
    assert manager.provenance("logging.level").source is ConfigSource.RUNTIME


def test_a_cold_setting_is_refused_by_name(manager: LayeredConfigManager) -> None:
    """Refusing loudly beats silently ignoring."""
    with pytest.raises(InvalidRequestError, match=r"api\.port"):
        manager.apply({"api.port": 9999}, reason="test")

    assert manager.current.api.port == 8730


def test_an_invalid_value_changes_nothing(manager: LayeredConfigManager) -> None:
    before = manager.current.logging.level

    with pytest.raises(InvalidRequestError):
        manager.apply({"logging.level": "loud"}, reason="test")

    assert manager.current.logging.level == before


def test_applying_the_current_value_reports_no_change(
    manager: LayeredConfigManager,
) -> None:
    assert manager.apply({"logging.level": manager.current.logging.level}, reason="x") == ()


def test_an_empty_patch_is_a_no_op(manager: LayeredConfigManager) -> None:
    assert manager.apply({}, reason="x") == ()


def test_changes_report_the_old_and_new_values(manager: LayeredConfigManager) -> None:
    manager.apply({"logging.level": "debug"}, reason="first")
    changes = manager.apply({"logging.level": "error"}, reason="second")

    assert changes[0].old == "debug"
    assert changes[0].new == "error"
    assert changes[0].reason == "second"


def test_runtime_overrides_accumulate(manager: LayeredConfigManager) -> None:
    manager.apply({"logging.level": "debug"}, reason="one")
    manager.apply({"logging.format": "json"}, reason="two")

    assert manager.current.logging.level == "debug"
    assert manager.current.logging.format == "json"


# -- listeners -------------------------------------------------------------


def test_listeners_are_told_about_changes(manager: LayeredConfigManager) -> None:
    seen: list[ConfigChange] = []
    manager.subscribe(seen.extend)

    manager.apply({"logging.level": "debug"}, reason="test")

    assert [change.path for change in seen] == ["logging.level"]


def test_unsubscribing_stops_notifications(manager: LayeredConfigManager) -> None:
    seen: list[ConfigChange] = []
    unsubscribe = manager.subscribe(seen.extend)
    unsubscribe()

    manager.apply({"logging.level": "debug"}, reason="test")

    assert seen == []


def test_a_listener_that_throws_does_not_block_the_others(
    manager: LayeredConfigManager,
) -> None:
    """A listener must not be able to undo a change that has already been applied."""
    seen: list[ConfigChange] = []

    def explodes(_: object) -> None:
        raise RuntimeError("listener is broken")

    manager.subscribe(explodes)
    manager.subscribe(seen.extend)

    manager.apply({"logging.level": "debug"}, reason="test")

    assert len(seen) == 1
    assert manager.current.logging.level == "debug"


# -- reload ----------------------------------------------------------------


def test_reload_with_no_file_changes_reports_nothing(
    manager: LayeredConfigManager,
) -> None:
    assert manager.reload() == ()


def test_reload_applies_hot_changes_from_the_environment(
    manager: LayeredConfigManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HEDWIG_LOGGING__LEVEL", "warning")

    changes = manager.reload(reason="env changed")

    assert [change.path for change in changes] == ["logging.level"]
    assert manager.current.logging.level == "warning"


def test_reload_does_not_apply_cold_changes(
    manager: LayeredConfigManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HEDWIG_API__PORT", "9999")

    manager.reload(reason="env changed")

    assert manager.current.api.port == 8730  # still needs a restart


# -- health ----------------------------------------------------------------


async def test_health_reports_the_active_sources(manager: LayeredConfigManager) -> None:
    from hedwig.core.ports import HealthStatus

    health = await manager.health()

    assert health.status is HealthStatus.OK
    assert "file" in health.detail["sources"]
    assert health.detail["hot_paths"] == len(HOT_PATHS)
