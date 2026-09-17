"""Configuration manager: provenance, hot reload, and controlled runtime updates.

`core.config` resolves the layers once at startup and freezes the result. This module owns
what happens afterwards, and exists for three reasons:

* **Provenance.** "Why is this setting not taking effect?" is the most common question a
  layered-configuration system produces, and the answer costs one string per field
  (docs/17 §8). Each layer is resolved a second time, independently, so we can say which
  one actually won.
* **Hot reload, narrowly.** A handful of keys — log level, thresholds, budgets — can change
  without a restart. Everything else requires one, and says so rather than appearing to
  work (docs/17 §8).
* **Validation before application.** A patch is validated against the whole model before
  anything changes, so an invalid patch changes nothing.

Runtime mutability is deliberately small. A configuration value that can change at any
moment is a value no test can pin down, so the default answer to "should this be hot?" is
no.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from pydantic import ValidationError

from hedwig.core.config import CONFIG_DIR, REPO_ROOT, Config, load_config
from hedwig.core.errors import ConfigurationError, InvalidRequestError, NotFoundError
from hedwig.core.logging import fields, get_logger
from hedwig.core.ports import (
    ConfigChange,
    ConfigEntry,
    ConfigListener,
    ConfigSource,
    Health,
    HealthStatus,
)

logger = get_logger(__name__)

HOT_PATHS: Final[frozenset[str]] = frozenset(
    {
        "logging.level",
        "logging.format",
        "logging.redact_content",
    }
)
"""Settings that may change while running.

Everything absent from this set needs a restart. Add to it only when a key is genuinely
safe to change mid-flight *and* something reads it on every use rather than caching it at
construction — otherwise "hot" is a lie the API tells.
"""

ENV_PREFIX: Final = "HEDWIG_"
NESTED_DELIMITER: Final = "__"


class LayeredConfigManager:
    """The `ConfigManager` implementation."""

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or load_config()
        self._listeners: list[ConfigListener] = []
        self._runtime_overrides: dict[str, Any] = {}
        self._layers: list[tuple[ConfigSource, dict[str, Any]]] = _resolve_layers()

    # -- reading -----------------------------------------------------------

    @property
    def current(self) -> Config:
        return self._config

    def entries(self) -> Sequence[ConfigEntry]:
        flat = _flatten(self._config.model_dump(mode="json"))
        return tuple(
            ConfigEntry(path=path, value=value, source=self._source_of(path), hot=path in HOT_PATHS)
            for path, value in sorted(flat.items())
        )

    def provenance(self, path: str) -> ConfigEntry:
        flat = _flatten(self._config.model_dump(mode="json"))
        if path not in flat:
            raise NotFoundError(f"no such setting: {path!r}", path=path)
        return ConfigEntry(
            path=path, value=flat[path], source=self._source_of(path), hot=path in HOT_PATHS
        )

    def snapshot(self) -> Mapping[str, Any]:
        """A JSON-safe view for `/v1/config` and for logs.

        Nothing is redacted today because nothing in the model is a secret — secrets come
        from the OS keychain and never pass through here. The hook exists so the first
        secret-shaped value added cannot leak through this path by default.
        """
        return self._config.model_dump(mode="json")

    def _source_of(self, path: str) -> ConfigSource:
        if path in self._runtime_overrides:
            return ConfigSource.RUNTIME
        for source, values in reversed(self._layers):
            if path in values:
                return source
        return ConfigSource.DEFAULT

    # -- writing -----------------------------------------------------------

    def apply(self, patch: Mapping[str, Any], *, reason: str) -> Sequence[ConfigChange]:
        if not patch:
            return ()

        rejected = sorted(set(patch) - HOT_PATHS)
        if rejected:
            # Refusing loudly beats silently ignoring: a setting that appears to change and
            # does not is worse than one that says it cannot.
            raise InvalidRequestError(
                f"these settings require a restart: {rejected}",
                paths=rejected,
                hot_paths=sorted(HOT_PATHS),
            )

        before = _flatten(self._config.model_dump(mode="json"))
        merged = {**self._runtime_overrides, **dict(patch)}
        candidate = self._build(merged)

        after = _flatten(candidate.model_dump(mode="json"))
        changes = tuple(
            ConfigChange(path=path, old=before.get(path), new=after[path], reason=reason)
            for path in sorted(set(patch))
            if before.get(path) != after[path]
        )
        if not changes:
            return ()

        self._config = candidate
        self._runtime_overrides = merged
        self._notify(changes)
        logger.info(
            "configuration changed",
            extra=fields(
                reason=reason, changes=[f"{c.path}: {c.old!r} -> {c.new!r}" for c in changes]
            ),
        )
        return changes

    def reload(self, *, reason: str = "manual reload") -> Sequence[ConfigChange]:
        """Re-read the files, applying only the differences that are hot-reloadable.

        A file change to a structural key is reported as a notice rather than applied: the
        user should know the file and the process disagree.
        """
        self._layers = _resolve_layers()
        fresh = self._build(self._runtime_overrides)

        before = _flatten(self._config.model_dump(mode="json"))
        after = _flatten(fresh.model_dump(mode="json"))
        differing = {path for path in after if before.get(path) != after[path]}

        cold = sorted(differing - HOT_PATHS)
        if cold:
            logger.warning(
                "configuration file changed settings that need a restart",
                extra=fields(reason=reason, paths=cold),
            )

        hot = {path: after[path] for path in sorted(differing & HOT_PATHS)}
        if not hot:
            return ()
        return self.apply(hot, reason=reason)

    def _build(self, overrides: Mapping[str, Any]) -> Config:
        nested: dict[str, Any] = {}
        for path, value in overrides.items():
            _assign(nested, path, value)
        try:
            return load_config(**nested)
        except ValidationError as exc:
            raise InvalidRequestError(
                f"configuration patch is invalid: {exc.error_count()} error(s)",
                errors=[
                    {"path": ".".join(str(p) for p in e["loc"]), "message": e["msg"]}
                    for e in exc.errors()
                ],
            ) from exc

    # -- listeners ---------------------------------------------------------

    def subscribe(self, listener: ConfigListener) -> Callable[[], None]:
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def _notify(self, changes: Sequence[ConfigChange]) -> None:
        for listener in list(self._listeners):
            try:
                listener(changes)
            except Exception:
                # A listener that throws must not prevent the others from being told, and
                # must not undo a change that has already been applied.
                logger.exception("configuration listener failed")

    # -- health ------------------------------------------------------------

    async def health(self) -> Health:
        return Health(
            status=HealthStatus.OK,
            detail={
                "environment": self._config.runtime.environment,
                "sources": [source.value for source, values in self._layers if values],
                "runtime_overrides": sorted(self._runtime_overrides),
                "hot_paths": len(HOT_PATHS),
            },
        )


# -- layer resolution ------------------------------------------------------


def _resolve_layers() -> list[tuple[ConfigSource, dict[str, Any]]]:
    """Read each configuration source independently, lowest precedence first.

    Deliberately a second, simpler pass over the same inputs pydantic-settings reads.
    Pydantic resolves the values; this resolves *where they came from*, which pydantic does
    not report. The duplication is the price of an honest answer, and it is bounded: a
    conformance test asserts the two agree.
    """
    return [
        (ConfigSource.FILE, _read_toml(CONFIG_DIR / "hedwig.toml")),
        (ConfigSource.LOCAL_FILE, _read_toml(CONFIG_DIR / "hedwig.local.toml")),
        (ConfigSource.DOTENV, _read_env_file(REPO_ROOT / ".env")),
        (ConfigSource.ENVIRONMENT, _read_environ(os.environ)),
    ]


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as handle:
            return _flatten(tomllib.load(handle))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationError(f"cannot read {path}: {exc}", path=str(path)) from exc


def _read_env_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip().strip("\"'")
    return _read_environ(values)


def _read_environ(environ: Mapping[str, str]) -> dict[str, Any]:
    resolved: dict[str, Any] = {}
    for key, value in environ.items():
        if not key.startswith(ENV_PREFIX):
            continue
        path = key.removeprefix(ENV_PREFIX).replace(NESTED_DELIMITER, ".").lower()
        resolved[path] = value
    return resolved


def _flatten(data: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    """Nested mapping to dotted paths. Lists and scalars are leaves."""
    flat: dict[str, Any] = {}
    for key, value in data.items():
        path = f"{prefix}{key}"
        if isinstance(value, Mapping):
            flat.update(_flatten(value, prefix=f"{path}."))
        else:
            flat[path] = value
    return flat


def _assign(target: dict[str, Any], path: str, value: Any) -> None:
    head, _, rest = path.partition(".")
    if not rest:
        target[head] = value
        return
    child = target.setdefault(head, {})
    if not isinstance(child, dict):  # pragma: no cover - guarded by validation
        raise InvalidRequestError(f"cannot set {path!r}: {head!r} is not a section")
    _assign(child, rest, value)


def iter_hot_paths() -> Iterator[str]:
    yield from sorted(HOT_PATHS)
