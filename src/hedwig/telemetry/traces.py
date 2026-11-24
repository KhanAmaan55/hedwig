"""One unit of work, reassembled by its correlation id (docs/19 §3, §5, §13).

A turn (`turn_…`) or a scheduled run: its root row, then one timeline of its stages, the bus
handlers it set off, the model and tool calls they made and the events they emitted — each
with its offset from the start. Ids, names, timings and counts; no content.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from hedwig.core.errors import NotFoundError
from hedwig.core.store import Database

FAILED = frozenset({"failed", "error", "timeout", "invalid"})
"""Statuses that ended badly. A tool call that was `denied` did what the user asked."""


class Traces:
    def __init__(self, database: Database) -> None:
        self._db = database

    def recent(self, *, limit: int = 20, kind: str | None = None) -> list[dict[str, Any]]:
        """Recent turns and scheduled runs, newest first; `kind` keeps only turns or only jobs."""
        rows = self._db.query(
            "SELECT * FROM ("
            "  SELECT correlation_id, 'turn' AS kind, id AS name, status, started_at, latency_ms "
            "  FROM turn "
            "  UNION ALL "
            "  SELECT correlation_id, 'job' AS kind, task_name AS name, status, started_at, "
            "  (julianday(finished_at) - julianday(started_at)) * 86400000.0 AS latency_ms "
            "  FROM task_run WHERE started_at IS NOT NULL"
            ") WHERE ? IS NULL OR kind = ? ORDER BY started_at DESC LIMIT ?",
            (kind, kind, limit),
        )
        return [
            {**dict(r), "latency_ms": round(r["latency_ms"], 1) if r["latency_ms"] else None}
            for r in rows
        ]

    def trace(self, correlation_id: str) -> dict[str, Any]:
        root = self._root(correlation_id)
        entries = [
            *self._spans(correlation_id),
            *self._model_calls(correlation_id),
            *self._tool_calls(correlation_id),
            *self._events(correlation_id),
        ]
        if root is None and not entries:
            raise NotFoundError(
                "Nothing was recorded under that id.", correlation_id=correlation_id
            )
        # The earliest thing recorded: a model call's start is derived from its end and its
        # duration, and may precede the turn row's own timestamp by a clock tick.
        starts = [_at(e["at"]) for e in entries] + ([_at(root["started_at"])] if root else [])
        start = min(starts)
        entries.sort(key=lambda e: (_at(e["at"]), e["type"] != "stage"))
        for entry in entries:
            entry["offset_ms"] = round((_at(entry["at"]) - start).total_seconds() * 1000, 1)
        stages = [e for e in entries if e["type"] == "stage"]
        models = [e for e in entries if e["type"] == "model"]
        return {
            "correlation_id": correlation_id,
            "root": root,
            "summary": {
                "stages_ms": round(sum(e["duration_ms"] for e in stages), 1),
                "model_calls": len(models),
                "model_ms": round(sum(e["duration_ms"] or 0 for e in models), 1),
                "handlers": sum(e["type"] == "handler" for e in entries),
                "events": sum(e["type"] == "event" for e in entries),
                "failures": sum(e.get("status") in FAILED for e in entries),
            },
            "timeline": entries,
        }

    # -- the pieces --------------------------------------------------------------------------

    def _root(self, correlation_id: str) -> dict[str, Any] | None:
        turn = self._db.query_one(
            "SELECT id, session_id, status, started_at, completed_at, latency_ms, tokens_in, "
            "tokens_out FROM turn WHERE correlation_id = ? ORDER BY started_at LIMIT 1",
            (correlation_id,),
        )
        if turn is not None:
            return {"kind": "turn", **dict(turn)}
        run = self._db.query_one(
            # Not `error`: a task's exception text can quote what it was working on.
            "SELECT id, task_name, trigger, status, started_at, finished_at FROM task_run "
            "WHERE correlation_id = ? AND started_at IS NOT NULL LIMIT 1",
            (correlation_id,),
        )
        return {"kind": "job", **dict(run)} if run is not None else None

    def _spans(self, correlation_id: str) -> list[dict[str, Any]]:
        return [
            {
                "type": str(r["kind"]),
                "name": str(r["name"]),
                "at": str(r["started_at"]),
                "duration_ms": float(r["duration_ms"]),
                "status": str(r["status"]),
                **(json.loads(r["detail"]) if r["detail"] else {}),
            }
            for r in self._db.query(
                "SELECT * FROM span WHERE correlation_id = ? ORDER BY started_at",
                (correlation_id,),
            )
        ]

    def _model_calls(self, correlation_id: str) -> list[dict[str, Any]]:
        return [
            {
                "type": "model",
                "name": str(r["purpose"]),
                "at": _began(r["created_at"], r["duration_ms"]),
                "duration_ms": r["duration_ms"],
                "status": str(r["status"]),
                "tier": str(r["tier"]),
                "model": str(r["model"]),
                "first_token_ms": r["first_token_ms"],
                "tokens_in": r["tokens_in"],
                "tokens_out": r["tokens_out"],
            }
            for r in self._db.query(
                "SELECT * FROM llm_call WHERE correlation_id = ? ORDER BY created_at",
                (correlation_id,),
            )
        ]

    def _tool_calls(self, correlation_id: str) -> list[dict[str, Any]]:
        return [
            {
                "type": "tool",
                "name": str(r["tool_name"]),
                "at": _began(r["created_at"], r["duration_ms"]),
                "duration_ms": r["duration_ms"],
                "status": str(r["status"]),
            }
            for r in self._db.query(
                "SELECT tool_name, created_at, duration_ms, status FROM tool_call "
                "WHERE correlation_id = ? ORDER BY created_at",
                (correlation_id,),
            )
        ]

    def _events(self, correlation_id: str) -> list[dict[str, Any]]:
        return [
            {
                "type": "event",
                "name": str(r["type"]),
                "at": str(r["occurred_at"]),
                "duration_ms": None,
                "source": str(r["source"]),
                "id": str(r["id"]),
                "caused_by": r["causation_id"],
            }
            for r in self._db.query(
                "SELECT id, type, occurred_at, source, causation_id FROM event "
                "WHERE correlation_id = ? ORDER BY occurred_at",
                (correlation_id,),
            )
        ]


def _at(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _began(recorded: str, duration_ms: float | None) -> str:
    """Model and tool rows are written when the call ends; the timeline wants its start."""
    start = _at(recorded) - timedelta(milliseconds=duration_ms or 0)
    return start.isoformat(timespec="milliseconds")
