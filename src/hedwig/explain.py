"""Why HEDWIG said that (docs/16 §6, §14, SEC-01).

A read model: it reads what a turn already recorded, across the modules that own those records,
under docs/08 §3.1's rule — reads go to the tables, writes go through the owner. It writes
nothing and owns nothing.

Every section is always present; empty or `None` means "nothing happened", never "not looked".
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from hedwig.core.errors import NotFoundError
from hedwig.core.ports.memory import Belief, Episode
from hedwig.core.store import Database
from hedwig.core.types import MemoryId
from hedwig.memory.store import SqliteMemoryStore

EXCERPT = 240


class Explainer:
    def __init__(self, database: Database, memory: SqliteMemoryStore) -> None:
        self._db = database
        self._memory = memory

    async def explain(self, message_id: str) -> dict[str, Any]:
        reply = self._db.query_one(
            "SELECT id, session_id, role, text, meta, emotion_ref, created_at FROM message "
            "WHERE id = ?",
            (message_id,),
        )
        if reply is None or reply["role"] != "hedwig":
            raise NotFoundError("No reply from HEDWIG with that id.", message_id=message_id)

        turn = self._db.query_one("SELECT * FROM turn WHERE reply_message_id = ?", (message_id,))
        meta = _json(reply["meta"])
        question = None
        if turn is not None and turn["user_message_id"]:
            asked = self._db.query_one(
                "SELECT text FROM message WHERE id = ?", (turn["user_message_id"],)
            )
            question = str(asked["text"]) if asked else None
        correlation = str(turn["correlation_id"]) if turn is not None else None

        return {
            "message_id": message_id,
            "session_id": str(reply["session_id"]),
            "question": question,
            "reply": str(reply["text"]),
            "at": str(reply["created_at"]),
            "turn": None
            if turn is None
            else {
                "id": str(turn["id"]),
                "status": str(turn["status"]),
                "latency_ms": turn["latency_ms"],
            },
            "intent": meta.get("intent"),
            "rationale": meta.get("rationale"),
            # None, not [], for replies from before directives were recorded (docs/16 §14).
            "directives": meta.get("directives"),
            "goals_in_view": meta.get("goals"),
            "finding_offered": meta.get("finding_offered"),
            "mood": self._mood(reply["emotion_ref"]),
            "retrieval": await self._retrieval(turn),
            "tools": self._tools(correlation),
            "model": self._model(correlation),
            "trace_id": correlation,
        }

    def _mood(self, emotion_ref: Any) -> dict[str, float] | None:
        if not emotion_ref:
            return None
        row = self._db.query_one("SELECT dims FROM emotion_history WHERE id = ?", (emotion_ref,))
        if row is None:
            return None
        return {k: round(float(v), 3) for k, v in _json(row["dims"]).items()}

    async def _retrieval(self, turn: Any) -> dict[str, Any] | None:
        if turn is None or not turn["working_set_id"]:
            return None
        log = self._db.query_one(
            "SELECT * FROM working_set_log WHERE id = ?", (turn["working_set_id"],)
        )
        if log is None:
            return None
        items = []
        for item in json.loads(log["items"] or "[]"):
            memory = await self._memory.inspect(MemoryId(str(item.get("memory_id", ""))))
            passage = None if memory else self._passage(str(item.get("memory_id", "")))
            items.append(
                {
                    "memory_id": item.get("memory_id"),
                    "score": item.get("score"),
                    "trust": item.get("trust"),
                    "source": item.get("source"),
                    "kind": _kind(memory) or ("document" if passage else None),
                    "text": (_text(memory) if memory else (passage or ""))[:EXCERPT] or None,
                    "forgotten_since": bool(memory and memory.tombstoned_at),
                    "cited_in_reply": True,
                }
            )
        return {
            "queries": json.loads(log["query"] or "[]"),
            "items": items,
            "token_count": log["token_count"],
            "dropped_count": log["dropped_count"],
        }

    def _passage(self, chunk_id: str) -> str | None:
        """A passage of the user's documents (docs/13 §14), with the file it came from."""
        row = self._db.query_one(
            "SELECT c.heading_path, c.text, d.path FROM document_chunk c "
            "JOIN document d ON d.id = c.document_id WHERE c.id = ?",
            (chunk_id,),
        )
        return f"{row['path']} — {row['heading_path']}: {row['text']}" if row else None

    def _tools(self, correlation: str | None) -> list[dict[str, Any]]:
        if not correlation:
            return []
        rows = self._db.query(
            "SELECT tool_name, status, error, duration_ms FROM tool_call "
            "WHERE correlation_id = ? ORDER BY created_at",
            (correlation,),
        )
        return [
            {
                "name": row["tool_name"],
                "status": row["status"],
                "error": row["error"],
                "duration_ms": row["duration_ms"],
            }
            for row in rows
        ]

    def _model(self, correlation: str | None) -> list[dict[str, Any]]:
        if not correlation:
            return []
        rows = self._db.query(
            "SELECT purpose, tier, model, status, tokens_in, tokens_out, duration_ms, "
            "first_token_ms FROM llm_call WHERE correlation_id = ? ORDER BY created_at",
            (correlation,),
        )
        return [dict(row) for row in rows]


def _json(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    parsed = json.loads(value)
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _kind(memory: Episode | Belief | None) -> str | None:
    if memory is None:
        return None
    return "episode" if isinstance(memory, Episode) else "belief"


def _text(memory: Episode | Belief) -> str:
    return memory.content if isinstance(memory, Episode) else memory.statement
