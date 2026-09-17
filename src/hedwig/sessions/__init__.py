"""The conversation log (docs/05 §5.1, docs/26 §4).

Sessions, messages, turns and the working-set log — the durable trace a turn leaves behind,
and the recent-turn window that short-term memory is a query over (docs/06 §2).
"""

from __future__ import annotations

from hedwig.sessions.store import DEFAULT_WINDOW, SqliteSessionStore

__all__ = ["DEFAULT_WINDOW", "SqliteSessionStore"]
