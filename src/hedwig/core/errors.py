"""Typed errors.

Ports never leak provider exceptions (`httpx.HTTPError`, `sqlite3.OperationalError`);
they raise one of these instead. See docs/03 §2 rule 5.

`code` is the stable, machine-readable identifier that reaches API clients as
`error.code` (docs/16 §4.6). Adding a subclass means adding a code.
"""

from __future__ import annotations

from typing import Any


class HedwigError(Exception):
    """Base class for every error HEDWIG raises deliberately."""

    code: str = "internal"
    http_status: int = 500
    retryable: bool = False

    def __init__(self, message: str, **detail: Any) -> None:
        super().__init__(message)
        self.message = message
        self.detail: dict[str, Any] = detail


class ConfigurationError(HedwigError):
    """Configuration is missing, malformed, or internally inconsistent."""

    code = "configuration_invalid"
    http_status = 500


class InvalidRequestError(HedwigError):
    """The caller asked for something malformed."""

    code = "invalid_request"
    http_status = 400


class NotFoundError(HedwigError):
    """The requested resource does not exist."""

    code = "not_found"
    http_status = 404


class ConflictError(HedwigError):
    """The request conflicts with current state (version mismatch, duplicate)."""

    code = "conflict"
    http_status = 409


class CapabilityUnavailableError(HedwigError):
    """A required capability (model, index, network) is not available right now."""

    code = "capability_unavailable"
    http_status = 503
    retryable = True


class DegradedError(HedwigError):
    """The operation succeeded partially, or was refused because a subsystem is degraded."""

    code = "degraded"
    http_status = 503
    retryable = True
