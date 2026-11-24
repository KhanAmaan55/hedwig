"""`/v1/metrics`: Prometheus text, computed at scrape (docs/19 §6, §13).

Counters and histograms over the records survive restarts because they *are* the records; the
gateway's counters and the bus's queues are since this process started, as Prometheus expects
of a process. Names follow docs/19 §6.2, so a dashboard needs no translation.

A `_total` counter only ever goes up, so it counts rows in a state they never leave (a finished
turn, a settled correction, a dismissed offer). What is waiting now is a gauge beside it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from hedwig.core.store import Database

TURN_BUCKETS: tuple[float, ...] = (0.5, 1.0, 2.0, 4.0, 6.0, 10.0, 20.0, 40.0, 80.0)
FIRST_TOKEN_BUCKETS: tuple[float, ...] = (0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)
DIMENSIONS = ("happiness", "trust", "curiosity", "confidence", "energy", "stress")


class _Text:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def metric(
        self,
        name: str,
        kind: str,
        help_text: str,
        samples: Iterable[tuple[Mapping[str, str], float]],
    ) -> None:
        self.lines += [f"# HELP {name} {help_text}", f"# TYPE {name} {kind}"]
        for labels, value in samples:
            self.lines.append(f"{name}{_labels(labels)} {_number(value)}")

    def histogram(
        self, name: str, help_text: str, values: Sequence[float], buckets: Sequence[float]
    ) -> None:
        self.lines += [f"# HELP {name} {help_text}", f"# TYPE {name} histogram"]
        for bound in buckets:
            count = sum(v <= bound for v in values)
            self.lines.append(f'{name}_bucket{{le="{_number(bound)}"}} {count}')
        self.lines.append(f'{name}_bucket{{le="+Inf"}} {len(values)}')
        self.lines.append(f"{name}_sum {_number(sum(values))}")
        self.lines.append(f"{name}_count {len(values)}")


def render_metrics(
    database: Database,
    *,
    now: datetime,
    uptime_seconds: float,
    gateway: str = "",
    bus: Mapping[str, Any] | None = None,
) -> str:
    """The whole exposition. `gateway` is the gateway's own Prometheus text, appended as is."""
    db = database
    out = _Text()
    out.metric("hedwig_uptime_seconds", "gauge", "Seconds since start.", [({}, uptime_seconds)])

    # -- turns ---------------------------------------------------------------------------------
    out.metric(
        "hedwig_turns_total",
        "counter",
        "Turns by how they ended.",
        _grouped(
            db,
            "SELECT status AS k, COUNT(*) AS n FROM turn WHERE status != 'running' GROUP BY status",
            "status",
        ),
    )
    latencies = [
        float(r["ms"]) / 1000
        for r in db.query("SELECT latency_ms AS ms FROM turn WHERE latency_ms IS NOT NULL")
    ]
    out.histogram(
        "hedwig_turn_duration_seconds", "Turn latency, start to reply.", latencies, TURN_BUCKETS
    )
    first = [
        float(r["ms"]) / 1000
        for r in db.query(
            "SELECT first_token_ms AS ms FROM llm_call WHERE first_token_ms IS NOT NULL "
            "AND tier = 'conversational'"
        )
    ]
    out.histogram(
        "hedwig_reply_first_token_seconds",
        "Time to a reply's first token, on record (the gateway's own histogram is since start).",
        first,
        FIRST_TOKEN_BUCKETS,
    )
    out.metric(
        "hedwig_llm_calls_recorded_total",
        "counter",
        "Model calls on record, by tier and outcome.",
        [
            ({"tier": str(r["tier"]), "status": str(r["status"])}, float(r["n"]))
            for r in db.query(
                "SELECT tier, status, COUNT(*) AS n FROM llm_call GROUP BY tier, status"
            )
        ],
    )

    # -- memory --------------------------------------------------------------------------------
    episodes = [
        ({"kind": "episode", "status": str(r["k"])}, float(r["n"]))
        for r in db.query(
            "SELECT CASE WHEN tombstoned_at IS NULL THEN 'live' ELSE 'forgotten' END AS k, "
            "COUNT(*) AS n FROM episode GROUP BY k"
        )
    ]
    beliefs = [
        ({"kind": "belief", "status": str(r["k"])}, float(r["n"]))
        for r in db.query("SELECT status AS k, COUNT(*) AS n FROM belief GROUP BY status")
    ]
    out.metric(
        "hedwig_memories_total", "gauge", "Memories by kind and status.", [*episodes, *beliefs]
    )
    out.metric(
        "hedwig_memories_forgotten_total",
        "counter",
        "Memories tombstoned, by reason.",
        _grouped(db, "SELECT reason AS k, COUNT(*) AS n FROM tombstone GROUP BY reason", "reason"),
    )
    live = db.query_one(
        "SELECT SUM(status = 'tentative') AS t, SUM(status IN ('tentative','active')) AS n "
        "FROM belief"
    )
    out.metric(
        "hedwig_beliefs_tentative_ratio",
        "gauge",
        "Share of live beliefs still unconfirmed: confabulation pressure.",
        [({}, _ratio(live["t"], live["n"]) if live else 0.0)],
    )
    week = (now - timedelta(days=7)).isoformat(timespec="milliseconds")
    packed = db.query_one(
        "SELECT SUM(dropped_count) AS dropped, SUM(json_array_length(items)) AS kept "
        "FROM working_set_log WHERE created_at >= ?",
        (week,),
    )
    dropped = float(packed["dropped"] or 0) if packed else 0.0
    kept = float(packed["kept"] or 0) if packed else 0.0
    out.metric(
        "hedwig_retrieval_dropped_ratio",
        "gauge",
        "Share of recall candidates dropped for space over the last 7 days.",
        [({}, _ratio(dropped, dropped + kept))],
    )

    # -- personality, emotion ------------------------------------------------------------------
    out.metric(
        "hedwig_personality_drift_applied_total",
        "counter",
        "Personality changes applied (zero for weeks is a bug report, not a calm month).",
        _grouped(
            db,
            "SELECT source AS k, COUNT(*) AS n FROM personality_history "
            "WHERE source IN ('inferred','instruction') GROUP BY source",
            "source",
        ),
    )
    out.metric(
        "hedwig_personality_drift_rejected_total",
        "counter",
        "Drift proposals a guardrail refused, by rule.",
        _grouped(
            db,
            "SELECT COALESCE(rejection_rule, 'unknown') AS k, COUNT(*) AS n FROM drift_proposal "
            "WHERE status = 'rejected' GROUP BY k",
            "rule",
        ),
    )
    mood = db.query_one(
        "SELECT happiness, trust, curiosity, confidence, energy, stress FROM emotion_state"
    )
    out.metric(
        "hedwig_emotion_dimension",
        "gauge",
        "The mood now, per dimension.",
        [({"dimension": key}, float(mood[key])) for key in DIMENSIONS] if mood else [],
    )

    # -- reflection ----------------------------------------------------------------------------
    out.metric(
        "hedwig_reflection_runs_total",
        "counter",
        "Reflection runs by tier and outcome.",
        [
            ({"tier": str(r["tier"]), "status": str(r["status"])}, float(r["n"]))
            for r in db.query(
                "SELECT tier, status, COUNT(*) AS n FROM reflection_run GROUP BY tier, status"
            )
        ],
    )
    out.metric(
        "hedwig_reflection_lag_hours",
        "gauge",
        "Hours since each tier last completed: missed runs show here.",
        [
            (
                {"tier": str(r["tier"])},
                (now - datetime.fromisoformat(r["last"])).total_seconds() / 3600,
            )
            for r in db.query(
                "SELECT tier, MAX(COALESCE(finished_at, started_at)) AS last FROM reflection_run "
                "WHERE status = 'completed' GROUP BY tier"
            )
        ],
    )

    # -- curiosity, initiative, corrections ----------------------------------------------------
    findings = {
        str(r["status"]): float(r["n"])
        for r in db.query("SELECT status, COUNT(*) AS n FROM finding GROUP BY status")
    }
    out.metric(
        "hedwig_curiosity_findings_total",
        "gauge",
        "Findings by status.",
        [({"status": k}, v) for k, v in sorted(findings.items())],
    )
    shown = sum(v for k, v in findings.items() if k not in ("new", "discarded", "quarantined"))
    kept = findings.get("confirmed", 0) + findings.get("promoted", 0)
    out.metric(
        "hedwig_curiosity_promotion_ratio",
        "gauge",
        "Findings the user kept, of those they saw.",
        [({}, _ratio(kept, shown))],
    )
    out.metric(
        "hedwig_injection_quarantined_total",
        "counter",
        "Fetched text set aside as a likely prompt injection.",
        [({}, findings.get("quarantined", 0))],
    )
    offers = {
        str(r["status"]): float(r["n"])
        for r in db.query("SELECT status, COUNT(*) AS n FROM initiative_offer GROUP BY status")
    }
    out.metric(
        "hedwig_curiosity_dismissal_ratio",
        "gauge",
        "Unprompted messages waved away (> 30 % is a design bug, docs/19 §6.2).",
        [({}, _ratio(offers.get("dismissed", 0), sum(offers.values())))],
    )
    out.metric(
        "hedwig_initiative_offers_total",
        "counter",
        "Unprompted messages, by kind.",
        _grouped(db, "SELECT kind AS k, COUNT(*) AS n FROM initiative_offer GROUP BY kind", "kind"),
    )
    out.metric(
        "hedwig_initiative_dismissed_total",
        "counter",
        "Unprompted messages the user waved away, by kind.",
        _grouped(
            db,
            "SELECT kind AS k, COUNT(*) AS n FROM initiative_offer WHERE status = 'dismissed' "
            "GROUP BY kind",
            "kind",
        ),
    )
    out.metric(
        "hedwig_corrections_total",
        "counter",
        "Corrections heard in conversation and settled, by what the user decided.",
        _grouped(
            db,
            "SELECT status AS k, COUNT(*) AS n FROM correction_proposal "
            "WHERE status != 'proposed' GROUP BY status",
            "status",
        ),
    )
    waiting = db.query_one(
        "SELECT COUNT(*) AS n FROM correction_proposal WHERE status = 'proposed'"
    )
    out.metric(
        "hedwig_corrections_pending",
        "gauge",
        "Corrections waiting for the user's yes or no.",
        [({}, float(waiting["n"]) if waiting else 0.0)],
    )

    # -- the bus (since start) and its dead letters (on record) --------------------------------
    dead = db.query_one("SELECT COUNT(*) AS n FROM event_dead_letter")
    out.metric(
        "hedwig_bus_dead_letters_total",
        "counter",
        "Events a handler gave up on.",
        [({}, float(dead["n"]) if dead else 0.0)],
    )
    subscriptions = (bus or {}).get("subscriptions", {})
    out.metric(
        "hedwig_bus_queue_depth",
        "gauge",
        "Events waiting, per subscription.",
        [({"subscription": name}, float(s["queued"])) for name, s in sorted(subscriptions.items())],
    )

    text = "\n".join(out.lines) + "\n"
    return text + (gateway if gateway.endswith("\n") or not gateway else gateway + "\n")


def _grouped(db: Database, sql: str, label: str) -> list[tuple[Mapping[str, str], float]]:
    return [({label: str(r["k"])}, float(r["n"])) for r in db.query(sql)]


def _ratio(part: Any, whole: Any) -> float:
    whole = float(whole or 0)
    return round(float(part or 0) / whole, 4) if whole else 0.0


def _labels(labels: Mapping[str, str]) -> str:
    if not labels:
        return ""
    inner = ",".join(f'{k}="{_escape(v)}"' for k, v in labels.items())
    return "{" + inner + "}"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.6g}"
