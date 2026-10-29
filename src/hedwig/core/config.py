"""Configuration.

One layered model, resolved once at startup, validated, and frozen thereafter
(docs/02 §7). Precedence, lowest to highest:

    defaults in code
      < config/hedwig.toml          (checked in, safe defaults)
        < config/hedwig.local.toml  (gitignored, machine-specific)
          < .env file
            < HEDWIG_* environment variables
              < CLI flags

Nested keys use a double underscore in the environment: `HEDWIG_API__PORT=9000`.

Two rules that are easy to break and expensive to fix:

* **Secrets never live in config files.** They come from the OS keychain through a
  `SecretStore` port. Nothing here is a secret, and nothing here ever will be.
* **Modules receive their own typed sub-config**, injected at construction. No module
  reaches into a global.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = REPO_ROOT / "config"

Environment = Literal["development", "production", "test"]

USE_LOCAL_SOURCES = True
"""Whether `config/hedwig.local.toml` and `.env` are read.

False in tests (set by `tests/conftest.py`): a developer's machine-local settings must not be
able to change test outcomes. Before this existed, creating a local file pointing at another
model made every fixture-built container in the suite load that model."""


class RuntimeConfig(BaseModel):
    """Process-level settings."""

    model_config = {"frozen": True, "extra": "forbid"}

    environment: Environment = "development"
    data_dir: Path = Path("~/.hedwig")
    timezone: str = ""
    """IANA name, e.g. "Europe/Lisbon". Empty means the system's local zone, which is the
    right default for an application that runs on the user's own machine. Read by the
    circadian energy baseline (docs/09 §5.3): a companion whose energy tracks UTC while the
    user is in Lisbon is worse than one with no circadian model at all."""

    @field_validator("data_dir")
    @classmethod
    def _expand(cls, value: Path) -> Path:
        return value.expanduser()

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def blobs_dir(self) -> Path:
        return self.data_dir / "blobs"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"


class ApiConfig(BaseModel):
    """The HTTP/WebSocket edge.

    Bound to loopback by default and there is no plan to change that default. Exposing
    HEDWIG on a network interface is a decision with a threat model attached (docs/21 §4).
    """

    model_config = {"frozen": True, "extra": "forbid"}

    host: str = "127.0.0.1"
    port: Annotated[int, Field(ge=1, le=65535)] = 8730
    cors_origins: tuple[str, ...] = ("http://localhost:5173", "http://127.0.0.1:5173")

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"


class LoggingConfig(BaseModel):
    """Structured logging (docs/19 §4)."""

    model_config = {"frozen": True, "extra": "forbid"}

    level: Literal["debug", "info", "warning", "error"] = "info"
    format: Literal["json", "console"] = "console"
    file_enabled: bool = True
    redact_content: bool = True
    """Message text, memory content and prompts stay out of logs unless explicitly
    enabled. A companion's logs would otherwise be the least protected copy of the most
    personal data on the machine (docs/19 §4)."""


class BusConfig(BaseModel):
    """The event bus (docs/04 §3.1)."""

    model_config = {"frozen": True, "extra": "forbid"}

    queue_size: Annotated[int, Field(ge=1)] = 512
    handler_timeout_seconds: Annotated[float, Field(gt=0)] = 30.0
    replay_window_hours: Annotated[int, Field(ge=0)] = 24
    strict_types: bool = True
    """Unknown event types are a hard error. Off only for experiments (docs/04 §4)."""


class StorageConfig(BaseModel):
    """Content-addressed blob storage (docs/05 §2)."""

    model_config = {"frozen": True, "extra": "forbid"}

    quota_bytes: int | None = None
    max_blob_bytes: Annotated[int, Field(gt=0)] = 256 * 1024 * 1024


class SchedulerConfig(BaseModel):
    """Background task scheduling (docs/17 §5-6)."""

    model_config = {"frozen": True, "extra": "forbid"}

    enabled: bool = True
    tick_seconds: Annotated[float, Field(gt=0)] = 5.0
    max_concurrent: Annotated[int, Field(ge=1)] = 1
    """One by default: background work must never contend with the conversation."""


class LlmConfig(BaseModel):
    """The language faculty (docs/14).

    Model names are configuration, not code: swapping a model must not require a change to
    anything but this section (docs/01 §5).
    """

    model_config = {"frozen": True, "extra": "forbid"}

    provider: Literal["ollama", "recorded"] = "ollama"
    endpoint: str = "http://127.0.0.1:11434"

    conversational: str = "llama3.1:8b"
    """User-facing prose. The only tier that pays for a large model."""
    utility: str = "qwen2.5:3b"
    """Planning, appraisal, extraction, summarisation — high volume, structured."""
    embedding: str = "nomic-embed-text"

    request_timeout_seconds: Annotated[float, Field(gt=0)] = 120.0
    connect_timeout_seconds: Annotated[float, Field(gt=0)] = 5.0
    stall_timeout_seconds: Annotated[float, Field(gt=0)] = 20.0
    """Per-chunk, while streaming. A model that goes quiet fails here rather than hanging
    until the total deadline (docs/14 §10)."""

    max_attempts: Annotated[int, Field(ge=1, le=10)] = 3
    retry_base_delay_seconds: Annotated[float, Field(ge=0)] = 0.5
    max_concurrent: Annotated[int, Field(ge=1)] = 1
    """Ollama serves one model at a time efficiently; queueing beats thrashing."""
    max_request_tokens: int = 0
    context_window: Annotated[int, Field(ge=1024)] = 8192
    """The conversational model's context, in tokens. The prompt budget is this minus the
    reply's `max_tokens` minus a safety margin (docs/14 §5.1). Set it to what the model
    actually supports: too high and prompts are truncated by the provider, silently."""
    """0 disables the per-request cap. Daily budgets belong to the resource governor."""


class MemoryConfig(BaseModel):
    """Memory (docs/06).

    The decay constants are the specification, not tuning knobs: changing one changes what
    HEDWIG forgets, so they live here where the change is visible.
    """

    model_config = {"frozen": True, "extra": "forbid"}

    recent_turn_window: Annotated[int, Field(ge=1, le=100)] = 8
    """Short-term memory: the last N messages, included verbatim and never charged to the
    retrieval budget (docs/06 §5.1)."""
    context_token_budget: Annotated[int, Field(ge=100)] = 3000
    decay_half_life_days: Annotated[float, Field(gt=0)] = 30.0
    forget_threshold: Annotated[float, Field(ge=0, le=1)] = 0.08
    max_captures_per_turn: Annotated[int, Field(ge=1, le=50)] = 5


class EmotionConfig(BaseModel):
    """The emotion engine (docs/09).

    Only the operational knobs are here. The mapping matrix, the half-lives, the caps and
    the floors are *not* configurable: changing what HEDWIG's mood responds to is an
    architectural change with an ADR, not a setting someone flips (docs/08 §8).
    """

    model_config = {"frozen": True, "extra": "forbid"}

    enabled: bool = True
    tick_seconds: Annotated[float, Field(gt=0, le=600)] = 30.0
    """The coalescing interval (docs/09 §5.2). Per-event updates would storm the guarded
    row and make the avatar twitch; 30 s of emotional latency is imperceptible."""
    min_publish_delta: Annotated[float, Field(ge=0, le=1)] = 0.02
    """L1 movement below which nothing is published, so idle hours stay quiet."""


class SessionsConfig(BaseModel):
    """Session lifecycle (docs/02 §5.1, docs/27 §5.1)."""

    model_config = {"frozen": True, "extra": "forbid"}

    idle_timeout_minutes: Annotated[float, Field(gt=0)] = 30.0
    """A session with no activity for this long ends. The relationship update runs then."""
    reap_interval_seconds: Annotated[float, Field(gt=0)] = 300.0


class PersonalityConfig(BaseModel):
    """The personality engine (docs/10).

    Unlike emotion's constants, the drift caps are configuration: docs/10 §8 says that if
    users find adaptation too slow, the fix is to relax them *in config, not in code*. The
    defaults are the documented values.
    """

    model_config = {"frozen": True, "extra": "forbid"}

    enabled: bool = True
    anchors: dict[str, float] = Field(default_factory=dict)
    """Override bootstrap anchors (docs/10 §6), e.g. `{"formality": 0.6}`. Only affects a
    HEDWIG that has never run; a drifted trait is never reset by a config change."""
    per_proposal_cap: Annotated[float, Field(gt=0, le=0.25)] = 0.03
    weekly_budget: Annotated[float, Field(gt=0, le=0.25)] = 0.02
    lifetime_cap: Annotated[float, Field(gt=0, le=1)] = 0.25
    instruction_step: Annotated[float, Field(gt=0, le=0.25)] = 0.06


class PluginsConfig(BaseModel):
    """The plugin allowlist (ADR-0016).

    An explicit list of importable module paths. Nothing is discovered, downloaded or
    installed at runtime; a plugin is first-party code in this repository that happens to be
    switchable. Empty by default.
    """

    model_config = {"frozen": True, "extra": "forbid"}

    enabled: tuple[str, ...] = ()
    settings: dict[str, dict[str, Any]] = Field(default_factory=dict)


class Config(BaseSettings):
    """The whole of HEDWIG's configuration."""

    model_config = SettingsConfigDict(
        env_prefix="HEDWIG_",
        env_nested_delimiter="__",
        env_file=(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="forbid",
        frozen=True,
    )

    runtime: RuntimeConfig = RuntimeConfig()
    api: ApiConfig = ApiConfig()
    logging: LoggingConfig = LoggingConfig()
    bus: BusConfig = BusConfig()
    storage: StorageConfig = StorageConfig()
    scheduler: SchedulerConfig = SchedulerConfig()
    llm: LlmConfig = LlmConfig()
    memory: MemoryConfig = MemoryConfig()
    emotion: EmotionConfig = EmotionConfig()
    personality: PersonalityConfig = PersonalityConfig()
    sessions: SessionsConfig = SessionsConfig()
    plugins: PluginsConfig = PluginsConfig()

    @property
    def database_path(self) -> Path:
        return self.runtime.data_dir / "hedwig.db"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # First source wins. The machine-local sources are skipped when `USE_LOCAL_SOURCES`
        # is off, which is how the test suite stays independent of the laptop it runs on.
        local: tuple[PydanticBaseSettingsSource, ...] = (
            (
                dotenv_settings,
                TomlConfigSettingsSource(settings_cls, CONFIG_DIR / "hedwig.local.toml"),
            )
            if USE_LOCAL_SOURCES
            else ()
        )
        return (
            init_settings,
            env_settings,
            *local,
            TomlConfigSettingsSource(settings_cls, CONFIG_DIR / "hedwig.toml"),
            file_secret_settings,
        )

    @property
    def is_development(self) -> bool:
        return self.runtime.environment == "development"

    def ensure_directories(self) -> None:
        """Create the data directories. Called at startup, never during validation."""
        for directory in (
            self.runtime.data_dir,
            self.runtime.logs_dir,
            self.runtime.blobs_dir,
            self.runtime.backups_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    def redacted_dump(self) -> dict[str, Any]:
        """A JSON-safe view for `hedwig config` and future `/v1/config`.

        Nothing is redacted today because nothing here is sensitive. The hook exists so
        that the first secret-shaped value added cannot leak through this path by default.
        """
        return self.model_dump(mode="json")


def load_config(**overrides: Any) -> Config:
    """Resolve configuration from all layers.

    `overrides` are the highest-precedence layer, used for CLI flags and tests.
    """
    return Config(**overrides)
