"""Layer 2 — Orchestration: the turn graph (docs/07).

Sequences a turn: perceive, recall, deliberate, act, compose, learn. Owns the *ordering*
and nothing else — no cognitive rules, no memory logic, no prompt content.
"""

from __future__ import annotations

from hedwig.brain.announcer import BusAnnouncer
from hedwig.brain.context import assemble_context
from hedwig.brain.graph import Brain, build_turn_graph, default_brain
from hedwig.brain.nodes import Collaborators, make_nodes
from hedwig.brain.planner import RulePlanner
from hedwig.brain.routing import (
    DEFAULT_CAPS,
    TurnCaps,
    after_act,
    after_approve,
    after_deliberate,
    after_guard,
    truncation_reason,
)
from hedwig.brain.state import TurnState, new_turn, summarise
from hedwig.brain.stubs import (
    STUB_MARKER,
    DefaultMindReader,
    InMemoryConversation,
    PermissiveGuard,
    RecordingAnnouncer,
    StubRecaller,
    StubResponder,
    StubToolRunner,
)

__all__ = [
    "DEFAULT_CAPS",
    "STUB_MARKER",
    "Brain",
    "BusAnnouncer",
    "Collaborators",
    "DefaultMindReader",
    "InMemoryConversation",
    "PermissiveGuard",
    "RecordingAnnouncer",
    "RulePlanner",
    "StubRecaller",
    "StubResponder",
    "StubToolRunner",
    "TurnCaps",
    "TurnState",
    "after_act",
    "after_approve",
    "after_deliberate",
    "after_guard",
    "assemble_context",
    "build_turn_graph",
    "default_brain",
    "make_nodes",
    "new_turn",
    "summarise",
    "truncation_reason",
]
