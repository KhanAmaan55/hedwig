from __future__ import annotations

import json
import logging

from hedwig.core.context import correlation_scope
from hedwig.core.logging import ConsoleFormatter, JsonFormatter, fields


def _record(**extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="hedwig.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="working set assembled",
        args=None,
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_formatter_emits_the_documented_schema() -> None:
    payload = json.loads(JsonFormatter().format(_record(**fields(packed=8, dropped=55))))

    assert payload["level"] == "info"
    assert payload["logger"] == "hedwig.test"
    assert payload["msg"] == "working set assembled"
    assert payload["fields"] == {"packed": 8, "dropped": 55}
    assert payload["ts"].endswith("+00:00")


def test_json_formatter_includes_correlation_and_duration() -> None:
    payload = json.loads(
        JsonFormatter().format(_record(correlation_id="turn_01JQ", duration_ms=78.2))
    )
    assert payload["correlation_id"] == "turn_01JQ"
    assert payload["duration_ms"] == 78.2


def test_extra_passed_without_fields_is_still_captured() -> None:
    """A caller who forgets `fields()` should not silently lose their data."""
    payload = json.loads(JsonFormatter().format(_record(candidates=63)))
    assert payload["fields"]["candidates"] == 63


def test_console_formatter_is_plain_text_without_a_tty() -> None:
    line = ConsoleFormatter(colour=False).format(_record(**fields(packed=8)))
    assert "\033[" not in line
    assert "working set assembled" in line
    assert "packed=8" in line


def test_correlation_scope_sets_and_restores() -> None:
    from hedwig.core.context import current_correlation_id

    assert current_correlation_id() is None
    with correlation_scope(prefix="turn") as outer:
        assert current_correlation_id() == outer
        assert outer.startswith("turn_")
        with correlation_scope("req_explicit") as inner:
            assert current_correlation_id() == inner == "req_explicit"
        assert current_correlation_id() == outer
    assert current_correlation_id() is None
