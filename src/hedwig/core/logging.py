"""Structured logging.

One schema for every log line (docs/19 §4):

    {"ts", "level", "logger", "msg", "correlation_id", "span", "duration_ms", "fields"}

Data goes in `fields`, never interpolated into `msg`, so logs stay queryable with `jq`.
Two renderers: JSON for files and production, a readable console format for development.

Built on the standard library rather than a logging framework because uvicorn, FastAPI and
every dependency already log through `logging`, and unifying them is the main job here.

Usage:

    log = get_logger(__name__)
    log.info("working set assembled", extra=fields(candidates=63, packed=8))
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hedwig.core.config import LoggingConfig, RuntimeConfig
from hedwig.core.context import current_correlation_id

_RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "message",
    "asctime",
    "taskName",
    # uvicorn attaches a pre-coloured copy of its own message; ours is already formatted.
    "color_message",
}

_LEVEL_COLOURS = {
    "DEBUG": "\033[38;5;244m",
    "INFO": "\033[38;5;39m",
    "WARNING": "\033[38;5;214m",
    "ERROR": "\033[38;5;203m",
    "CRITICAL": "\033[48;5;203;38;5;255m",
}
_DIM = "\033[38;5;244m"
_RESET = "\033[0m"


def fields(**values: Any) -> dict[str, Any]:
    """Wrap structured data for the `extra=` argument."""
    return {"fields": values}


def _record_fields(record: logging.LogRecord) -> dict[str, Any]:
    explicit = getattr(record, "fields", None)
    collected: dict[str, Any] = dict(explicit) if isinstance(explicit, dict) else {}
    # Anything passed via extra= without using fields() is still captured, not dropped.
    for key, value in record.__dict__.items():
        if key not in _RESERVED and key not in {"fields", "correlation_id", "span"}:
            collected.setdefault(key, value)
    return collected


class JsonFormatter(logging.Formatter):
    """One JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        correlation_id = getattr(record, "correlation_id", None)
        if correlation_id:
            payload["correlation_id"] = correlation_id
        for optional in ("span", "duration_ms"):
            value = getattr(record, optional, None)
            if value is not None:
                payload[optional] = value
        collected = _record_fields(record)
        if collected:
            payload["fields"] = collected
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, separators=(",", ":"))


class ConsoleFormatter(logging.Formatter):
    """Human-readable, aligned, coloured when the stream is a TTY."""

    def __init__(self, *, colour: bool) -> None:
        super().__init__()
        self._colour = colour

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, UTC).strftime("%H:%M:%S.%f")[:-3]
        level = record.levelname.ljust(5)
        logger_name = record.name.removeprefix("hedwig.")

        collected = _record_fields(record)
        correlation_id = getattr(record, "correlation_id", None)
        if correlation_id:
            collected["corr"] = str(correlation_id).split("_")[-1][-6:]
        duration = getattr(record, "duration_ms", None)
        if duration is not None:
            collected["ms"] = duration

        suffix = " ".join(f"{key}={value}" for key, value in collected.items())

        if self._colour:
            colour = _LEVEL_COLOURS.get(record.levelname, "")
            head = f"{_DIM}{timestamp}{_RESET} {colour}{level}{_RESET} {_DIM}{logger_name}{_RESET}"
            tail = f" {_DIM}{suffix}{_RESET}" if suffix else ""
        else:
            head = f"{timestamp} {level} {logger_name}"
            tail = f"  {suffix}" if suffix else ""

        line = f"{head}  {record.getMessage()}{tail}"
        if record.exc_info:
            line = f"{line}\n{self.formatException(record.exc_info)}"
        return line


class CorrelationFilter(logging.Filter):
    """Attach the ambient correlation id to every record, including third-party ones."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "correlation_id"):
            record.correlation_id = current_correlation_id()
        return True


def setup_logging(config: LoggingConfig, runtime: RuntimeConfig) -> None:
    """Configure the root logger and take over uvicorn's.

    Idempotent: calling it twice replaces handlers rather than duplicating output, which
    matters under `--reload`.
    """
    level = getattr(logging, config.level.upper())

    stream = sys.stderr
    if config.format == "json":
        console_formatter: logging.Formatter = JsonFormatter()
    else:
        console_formatter = ConsoleFormatter(colour=stream.isatty())

    console = logging.StreamHandler(stream)
    console.setFormatter(console_formatter)
    console.addFilter(CorrelationFilter())

    handlers: list[logging.Handler] = [console]

    if config.file_enabled:
        log_path = _prepare_log_file(runtime.logs_dir)
        if log_path is not None:
            file_handler = logging.handlers.RotatingFileHandler(
                log_path, maxBytes=50 * 1024 * 1024, backupCount=5, encoding="utf-8"
            )
            file_handler.setFormatter(JsonFormatter())  # files are always JSON
            file_handler.addFilter(CorrelationFilter())
            handlers.append(file_handler)

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
        existing.close()
    for handler in handlers:
        root.addHandler(handler)
    root.setLevel(level)

    # uvicorn installs its own handlers; route it through ours instead.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    # Access logs are duplicated by our own request middleware.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def _prepare_log_file(logs_dir: Path) -> Path | None:
    try:
        logs_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError:
        # A missing or unwritable data directory must not prevent HEDWIG from starting;
        # console logging still works and the failure is visible there.
        return None
    return logs_dir / "hedwig.log"


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
