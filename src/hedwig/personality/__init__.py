"""Layer 3 — Cognition: the personality engine (docs/10, docs/27).

Who HEDWIG is: ten traits that shape how it speaks, an identity core that shapes what it is,
and the brakes that let the first evolve without ever touching the second.

No model is involved anywhere in this module. Rendering is a deterministic band lookup,
instruction detection is a set of strict patterns, and drift is decided by seven guardrails
written as arithmetic.
"""

from __future__ import annotations

from hedwig.personality.drift import IDENTITY_KEYS, RecentChange, Verdict, evaluate
from hedwig.personality.engine import (
    SUBSCRIPTION,
    IdentityChangeRefusedError,
    PersonalityEngine,
)
from hedwig.personality.instructions import Instruction, acknowledgement, detect_instructions
from hedwig.personality.mind import PersonaMindReader
from hedwig.personality.rendering import (
    BANDS,
    MAX_DIRECTIVES,
    generation_policy,
    merge,
    relationship_directives,
    trait_directives,
)
from hedwig.personality.store import PersonalityStore
from hedwig.personality.traits import SPECS, DriftLimits, TraitSpec

__all__ = [
    "BANDS",
    "IDENTITY_KEYS",
    "MAX_DIRECTIVES",
    "SPECS",
    "SUBSCRIPTION",
    "DriftLimits",
    "IdentityChangeRefusedError",
    "Instruction",
    "PersonaMindReader",
    "PersonalityEngine",
    "PersonalityStore",
    "RecentChange",
    "TraitSpec",
    "Verdict",
    "acknowledgement",
    "detect_instructions",
    "evaluate",
    "generation_policy",
    "merge",
    "relationship_directives",
    "trait_directives",
]
