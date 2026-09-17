from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from hedwig.core.config import Config, load_config


def test_defaults_come_from_the_checked_in_toml() -> None:
    config = load_config()
    assert config.api.host == "127.0.0.1"
    assert config.api.port == 8730
    assert config.logging.redact_content is True


def test_environment_variables_outrank_toml(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HEDWIG_API__PORT", "9999")
    monkeypatch.setenv("HEDWIG_LOGGING__LEVEL", "debug")
    config = load_config()
    assert config.api.port == 9999
    assert config.logging.level == "debug"


def test_explicit_overrides_outrank_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HEDWIG_API__PORT", "9999")
    config = load_config(api={"port": 7000})
    assert config.api.port == 7000


def test_data_dir_is_expanded() -> None:
    config = load_config(runtime={"data_dir": "~/somewhere"})
    assert not str(config.runtime.data_dir).startswith("~")
    assert config.runtime.data_dir.is_absolute()


def test_derived_directories_hang_off_data_dir(tmp_path: Path) -> None:
    config = load_config(runtime={"data_dir": str(tmp_path)})
    assert config.runtime.logs_dir == tmp_path / "logs"
    assert config.runtime.blobs_dir == tmp_path / "blobs"
    assert config.runtime.backups_dir == tmp_path / "backups"


def test_config_is_frozen() -> None:
    config = load_config()
    with pytest.raises(ValidationError):
        config.api = config.api


def test_unknown_keys_are_rejected() -> None:
    """A typo in config must fail loudly at startup, not be silently ignored."""
    with pytest.raises(ValidationError):
        Config(api={"prot": 8730})  # type: ignore[arg-type]


def test_invalid_port_is_rejected() -> None:
    with pytest.raises(ValidationError):
        load_config(api={"port": 99999})


def test_ensure_directories_creates_the_tree(tmp_path: Path) -> None:
    config = load_config(runtime={"data_dir": str(tmp_path / "fresh")})
    config.ensure_directories()
    assert config.runtime.logs_dir.is_dir()
    assert config.runtime.blobs_dir.is_dir()
    assert config.runtime.backups_dir.is_dir()


def test_redacted_dump_is_json_safe() -> None:
    import json

    json.dumps(load_config().redacted_dump())
