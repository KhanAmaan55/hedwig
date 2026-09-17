"""Layer 4 — Knowledge: memory (docs/06).

Four durable stores and two derived views. Milestone 5 implements episodic and semantic
memory, entities, lineage, importance scoring, retrieval, decay and forgetting; Milestone 6
connects the write path to a finished turn (docs/26). Social and procedural memory arrive
with the milestones that give them meaning (docs/06 §2).

The recent-turn window is not here: it is a query over a table `sessions` owns, so it lives
with that table (docs/26 §4). docs/06 §2's claim is unchanged — short-term memory is a
query, not a store.
"""

from __future__ import annotations

from hedwig.memory.capture import (
    BeliefCandidate,
    CaptureReport,
    CaptureService,
    EpisodeCandidate,
    Verdict,
    has_substance,
    similarity,
)
from hedwig.memory.maintenance import MaintenanceReport, MemoryMaintenance
from hedwig.memory.recaller import MemoryRecaller
from hedwig.memory.retrieval import HybridRetrieval
from hedwig.memory.salience import (
    DEFAULT_HALF_LIFE_DAYS,
    FORGET_THRESHOLD,
    base_importance,
    decayed_salience,
    forget_budget,
    half_life_days,
    is_forgettable,
    reinforcement,
    salience_floor,
)
from hedwig.memory.store import SqliteMemoryStore
from hedwig.memory.turn_capture import (
    REMEMBER_MARKERS,
    SUBSCRIPTION,
    TurnCaptureSubscriber,
    candidates_from_turn,
)

__all__ = [
    "DEFAULT_HALF_LIFE_DAYS",
    "FORGET_THRESHOLD",
    "REMEMBER_MARKERS",
    "SUBSCRIPTION",
    "BeliefCandidate",
    "CaptureReport",
    "CaptureService",
    "EpisodeCandidate",
    "HybridRetrieval",
    "MaintenanceReport",
    "MemoryMaintenance",
    "MemoryRecaller",
    "SqliteMemoryStore",
    "TurnCaptureSubscriber",
    "Verdict",
    "base_importance",
    "candidates_from_turn",
    "decayed_salience",
    "forget_budget",
    "half_life_days",
    "has_substance",
    "is_forgettable",
    "reinforcement",
    "salience_floor",
    "similarity",
]
