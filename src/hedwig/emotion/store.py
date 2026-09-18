"""Emotion persistence (docs/05 §5.4).

One guarded row for the current state, a downsamplable timeline, and the appraisal log that
answers "why". Ordinary SQL — the interesting code in this module is next door in
`dynamics.py`.

The one rule worth stating: **nothing here stores user text.** `appraisal.rationale` is the
name of the rule that fired. The inspector reads this table and an export ships it, and a
conversation must not leak into it sideways.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from hedwig.core.ids import new_id
from hedwig.core.ports import Clock
from hedwig.core.ports.emotion import DIMENSIONS, Appraisal, Dimension, EmotionState
from hedwig.core.store import Database


class EmotionStore:
    """Reads and writes the three emotion tables."""

    def __init__(self, database: Database, *, clock: Clock) -> None:
        self._db = database
        self._clock = clock

    # -- current state -----------------------------------------------------

    def load(self) -> EmotionState | None:
        row = self._db.query_one("SELECT * FROM emotion_state WHERE id = 1")
        if row is None:
            return None
        return EmotionState(
            happiness=float(row["happiness"]),
            trust=float(row["trust"]),
            curiosity=float(row["curiosity"]),
            confidence=float(row["confidence"]),
            energy=float(row["energy"]),
            stress=float(row["stress"]),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
            version=int(row["version"]),
        )

    def ticked_at(self) -> datetime | None:
        """When the integrator last ran.

        Decay is measured from this, not from the last *change*: an hour in which nothing
        happened is still an hour of decay, and reading `updated_at` instead would make
        idle time free.
        """
        row = self._db.query_one("SELECT ticked_at FROM emotion_state WHERE id = 1")
        return datetime.fromisoformat(str(row["ticked_at"])) if row else None

    def save(self, state: EmotionState, *, ticked_at: datetime) -> None:
        self._db.execute(
            """
            INSERT INTO emotion_state (
                id, happiness, trust, curiosity, confidence, energy, stress,
                ticked_at, updated_at, version
            ) VALUES (1,?,?,?,?,?,?,?,?,?)
            ON CONFLICT (id) DO UPDATE SET
                happiness = excluded.happiness, trust      = excluded.trust,
                curiosity = excluded.curiosity, confidence = excluded.confidence,
                energy    = excluded.energy,    stress     = excluded.stress,
                ticked_at = excluded.ticked_at, updated_at = excluded.updated_at,
                version   = excluded.version
            """,
            (
                state.happiness,
                state.trust,
                state.curiosity,
                state.confidence,
                state.energy,
                state.stress,
                _iso(ticked_at),
                _iso(state.updated_at or ticked_at),
                state.version,
            ),
        )

    # -- history and lineage -----------------------------------------------

    def record_history(
        self,
        state: EmotionState,
        *,
        cause: str,
        appraisal_id: str | None = None,
        correlation_id: str | None = None,
    ) -> str:
        entry_id = new_id("emo")
        self._db.execute(
            "INSERT INTO emotion_history (id, recorded_at, dims, cause, appraisal_id, "
            "correlation_id) VALUES (?,?,?,?,?,?)",
            (
                entry_id,
                _iso(state.updated_at or self._clock.now()),
                json.dumps(state.as_dict()),
                cause,
                appraisal_id,
                correlation_id,
            ),
        )
        return entry_id

    def record_appraisal(
        self,
        appraisal: Appraisal,
        *,
        event_type: str,
        event_id: str | None,
        deltas: Mapping[Dimension, float],
        correlation_id: str | None = None,
    ) -> str:
        appraisal_id = new_id("apr")
        self._db.execute(
            "INSERT INTO appraisal (id, target_event_id, event_type, created_at, dims, "
            "deltas, rationale, correlation_id) VALUES (?,?,?,?,?,?,?,?)",
            (
                appraisal_id,
                event_id,
                event_type,
                _iso(self._clock.now()),
                json.dumps(
                    {
                        dimension.value: round(value, 6)
                        for dimension, value in appraisal.values().items()
                    }
                    | {"significance": appraisal.significance}
                ),
                json.dumps({d.value: round(deltas.get(d, 0.0), 6) for d in DIMENSIONS}),
                appraisal.rationale,
                correlation_id,
            ),
        )
        return appraisal_id

    def history(self, *, since: datetime | None = None, limit: int = 200) -> list[dict[str, Any]]:
        if since is not None:
            rows = self._db.query(
                "SELECT recorded_at, dims, cause FROM emotion_history WHERE recorded_at >= ? "
                "ORDER BY recorded_at DESC LIMIT ?",
                (_iso(since), limit),
            )
        else:
            rows = self._db.query(
                "SELECT recorded_at, dims, cause FROM emotion_history "
                "ORDER BY recorded_at DESC LIMIT ?",
                (limit,),
            )
        return [
            {
                "recorded_at": str(row["recorded_at"]),
                "cause": str(row["cause"]),
                **json.loads(str(row["dims"])),
            }
            for row in reversed(rows)
        ]

    def counts(self) -> dict[str, int]:
        def one(sql: str) -> int:
            row = self._db.query_one(sql)
            return int(row["n"]) if row else 0

        return {
            "history_entries": one("SELECT COUNT(*) AS n FROM emotion_history"),
            "appraisals": one("SELECT COUNT(*) AS n FROM appraisal"),
        }


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds")
