"""Personality persistence (docs/05 §5.4).

Ordinary SQL. The decisions live in `drift.py`; this module records them, and records them
completely — every proposal, every rejection with its rule, every change and every undoing of
one. Nothing is deleted: "you've gotten weird, go back" must leave a trail of having happened.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime

from hedwig.core.ids import new_id
from hedwig.core.ports.personality import (
    Evidence,
    IdentityCore,
    PersonalityProfile,
    Trait,
    TraitState,
)
from hedwig.core.store import Database
from hedwig.personality.drift import RecentChange
from hedwig.personality.traits import TraitSpec


class PersonalityStore:
    """Reads and writes the four personality tables."""

    def __init__(self, database: Database) -> None:
        self._db = database

    # -- bootstrap -----------------------------------------------------------

    def bootstrap(
        self, specs: Mapping[Trait, TraitSpec], identity: IdentityCore, *, now: datetime
    ) -> bool:
        """Write anchors and the identity core if they are not there. Returns whether it did.

        Idempotent and non-destructive: a restart never resets a drifted trait to its anchor,
        and a changed anchor in config only affects a HEDWIG that has never run.
        """
        at = _iso(now)
        created = False
        with self._db.transaction() as connection:
            for trait, spec in specs.items():
                cursor = connection.execute(
                    "INSERT INTO personality_trait (name, value, anchor, floor, ceiling, "
                    "updated_at) VALUES (?,?,?,?,?,?) ON CONFLICT (name) DO NOTHING",
                    (trait.value, spec.anchor, spec.anchor, spec.floor, spec.ceiling, at),
                )
                created = created or cursor.rowcount > 0
            for key, value in _identity_rows(identity).items():
                connection.execute(
                    "INSERT INTO identity_core (key, value, changed_by, updated_at) "
                    "VALUES (?,?,'bootstrap',?) ON CONFLICT (key) DO NOTHING",
                    (key, value, at),
                )
        return created

    # -- reading -------------------------------------------------------------

    def profile(self) -> PersonalityProfile:
        rows = self._db.query("SELECT * FROM personality_trait")
        traits = {
            Trait(str(row["name"])): TraitState(
                trait=Trait(str(row["name"])),
                value=float(row["value"]),
                anchor=float(row["anchor"]),
                floor=float(row["floor"]),
                ceiling=float(row["ceiling"]),
                lifetime_drift=float(row["lifetime_drift"]),
            )
            for row in rows
        }
        version = sum(int(row["version"]) for row in rows)
        return PersonalityProfile(traits=traits, identity=self.identity(), version=version)

    def identity(self) -> IdentityCore:
        rows = {
            str(r["key"]): str(r["value"]) for r in self._db.query("SELECT * FROM identity_core")
        }
        default = IdentityCore()
        return IdentityCore(
            name=rows.get("name", default.name),
            pronouns=rows.get("pronouns", default.pronouns),
            self_description=rows.get("self_description", default.self_description),
            values=rows.get("values", default.values),
            prohibitions=rows.get("prohibitions", default.prohibitions),
        )

    def recent_changes(self, trait: Trait, *, since: datetime) -> list[RecentChange]:
        """Applied, unreverted drift — what the budget and oscillation rules count.

        Reverts and resets are excluded: undoing a change is not a change in a direction, and
        counting it would let a user's "go back" block the very correction they asked for.
        """
        rows = self._db.query(
            "SELECT applied_at, delta FROM personality_history WHERE trait = ? "
            "AND source IN ('inferred','instruction') AND reverted_at IS NULL "
            "AND applied_at >= ? ORDER BY applied_at",
            (trait.value, _iso(since)),
        )
        return [
            RecentChange(
                applied_at=datetime.fromisoformat(str(row["applied_at"])),
                delta=float(row["delta"]),
            )
            for row in rows
        ]

    def history(self, *, trait: Trait | None = None, limit: int = 100) -> list[dict[str, object]]:
        if trait is None:
            rows = self._db.query(
                "SELECT * FROM personality_history ORDER BY applied_at DESC LIMIT ?", (limit,)
            )
        else:
            rows = self._db.query(
                "SELECT * FROM personality_history WHERE trait = ? "
                "ORDER BY applied_at DESC LIMIT ?",
                (trait.value, limit),
            )
        return [dict(row) for row in rows]

    def proposals(self, *, limit: int = 100) -> list[dict[str, object]]:
        rows = self._db.query(
            "SELECT * FROM drift_proposal ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        return [dict(row) for row in rows]

    # -- writing -------------------------------------------------------------

    def record_proposal(
        self,
        *,
        trait: str,
        delta: float,
        source: str,
        status: str,
        rule: str | None,
        rationale: str,
        evidence: Sequence[Evidence],
        now: datetime,
    ) -> str:
        proposal_id = new_id("drift")
        self._db.execute(
            "INSERT INTO drift_proposal (id, trait, proposed_delta, rationale, evidence, "
            "evidence_count, source, status, rejection_rule, created_at, decided_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                proposal_id,
                trait,
                delta,
                rationale,
                json.dumps(
                    [
                        {
                            "memory_id": item.memory_id,
                            "weight": item.weight,
                            "observed_at": _iso(item.observed_at),
                            "kind": item.kind,
                        }
                        for item in evidence
                    ]
                ),
                len(evidence),
                source,
                status,
                rule,
                _iso(now),
                _iso(now),
            ),
        )
        return proposal_id

    def apply(
        self,
        trait: Trait,
        delta: float,
        *,
        source: str,
        proposal_id: str | None,
        now: datetime,
        affects_lifetime: bool = True,
    ) -> str:
        """Move a trait and write the history row, in one transaction."""
        history_id = new_id("ph")
        at = _iso(now)
        with self._db.transaction() as connection:
            row = connection.execute(
                "SELECT value FROM personality_trait WHERE name = ?", (trait.value,)
            ).fetchone()
            old = float(row["value"])
            new = round(old + delta, 9)
            connection.execute(
                "UPDATE personality_trait SET value = ?, lifetime_drift = MAX(0, "
                "lifetime_drift + ?), updated_at = ?, version = version + 1 WHERE name = ?",
                (new, abs(delta) if affects_lifetime else 0.0, at, trait.value),
            )
            connection.execute(
                "INSERT INTO personality_history (id, trait, old_value, new_value, delta, "
                "source, proposal_id, applied_at) VALUES (?,?,?,?,?,?,?,?)",
                (history_id, trait.value, old, new, new - old, source, proposal_id, at),
            )
        return history_id

    def revert(self, history_id: str, *, now: datetime) -> str | None:
        """Undo one change. Returns the new history row, or `None` if there was nothing to undo.

        Gives back exactly the lifetime budget the change consumed, which is the accounting
        docs/10 §10 calls "an easy place for a subtle bug".
        """
        at = _iso(now)
        with self._db.transaction() as connection:
            row = connection.execute(
                "SELECT h.*, t.value AS current, t.floor, t.ceiling FROM personality_history h "
                "JOIN personality_trait t ON t.name = h.trait WHERE h.id = ?",
                (history_id,),
            ).fetchone()
            if row is None or row["reverted_at"] is not None:
                return None
            if str(row["source"]) not in {"inferred", "instruction"}:
                return None  # undoing an undo is a new change, not a revert

            current = float(row["current"])
            target = max(
                float(row["floor"]), min(float(row["ceiling"]), current - float(row["delta"]))
            )
            reversal_id = new_id("ph")
            connection.execute(
                "UPDATE personality_trait SET value = ?, lifetime_drift = MAX(0, "
                "lifetime_drift - ?), updated_at = ?, version = version + 1 WHERE name = ?",
                (target, abs(float(row["delta"])), at, row["trait"]),
            )
            connection.execute(
                "UPDATE personality_history SET reverted_at = ? WHERE id = ?", (at, history_id)
            )
            connection.execute(
                "INSERT INTO personality_history (id, trait, old_value, new_value, delta, "
                "source, applied_at) VALUES (?,?,?,?,?,'revert',?)",
                (reversal_id, row["trait"], current, target, target - current, at),
            )
        return reversal_id

    def reset(self, trait: Trait, *, now: datetime) -> str:
        """Return a trait to its anchor and clear its lifetime drift."""
        at = _iso(now)
        reset_id = new_id("ph")
        with self._db.transaction() as connection:
            row = connection.execute(
                "SELECT value, anchor FROM personality_trait WHERE name = ?", (trait.value,)
            ).fetchone()
            old, anchor = float(row["value"]), float(row["anchor"])
            connection.execute(
                "UPDATE personality_trait SET value = anchor, lifetime_drift = 0, "
                "updated_at = ?, version = version + 1 WHERE name = ?",
                (at, trait.value),
            )
            connection.execute(
                "INSERT INTO personality_history (id, trait, old_value, new_value, delta, "
                "source, applied_at) VALUES (?,?,?,?,?,'reset',?)",
                (reset_id, trait.value, old, anchor, anchor - old, at),
            )
        return reset_id

    def amend_identity(self, key: str, value: str, *, now: datetime) -> int:
        """Write one identity key with user consent. Returns the new revision."""
        with self._db.transaction() as connection:
            connection.execute(
                "UPDATE identity_core SET value = ?, revision = revision + 1, "
                "changed_by = 'user_consent', updated_at = ? WHERE key = ?",
                (value, _iso(now), key),
            )
            row = connection.execute(
                "SELECT revision FROM identity_core WHERE key = ?", (key,)
            ).fetchone()
        return int(row["revision"])

    def counts(self) -> dict[str, int]:
        def one(sql: str) -> int:
            row = self._db.query_one(sql)
            return int(row["n"]) if row else 0

        return {
            "changes": one(
                "SELECT COUNT(*) AS n FROM personality_history WHERE source IN "
                "('inferred','instruction') AND reverted_at IS NULL"
            ),
            "reverted": one(
                "SELECT COUNT(*) AS n FROM personality_history WHERE reverted_at IS NOT NULL"
            ),
            "rejected_proposals": one(
                "SELECT COUNT(*) AS n FROM drift_proposal WHERE status = 'rejected'"
            ),
        }


def _identity_rows(identity: IdentityCore) -> dict[str, str]:
    return {
        "name": identity.name,
        "pronouns": identity.pronouns,
        "self_description": identity.self_description,
        "values": identity.values,
        "prohibitions": identity.prohibitions,
    }


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds")
