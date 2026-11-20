"""The guard (docs/07 §3.1 node `guard`, docs/13 §12).

The user's own messages are always allowed and always `USER` trust: ADR-0011 makes the user the
one source of instruction, and a companion that refuses its user on a heuristic is broken, not
safe. What the guard adds is observation — an injection score and flags on the verdict, which
emotion appraises and the record keeps. It refuses nothing today, and says so.
"""

from __future__ import annotations

from dataclasses import dataclass

from hedwig.core.ports.brain import SafetyVerdict, TrustTier
from hedwig.knowledge.injection import scan


@dataclass(frozen=True, slots=True)
class RuleGuard:
    async def inspect(self, text: str) -> SafetyVerdict:
        result = scan(text)
        return SafetyVerdict(
            allowed=True,
            trust=TrustTier.USER,
            flags=result.flags,
            injection_score=result.score,
            reason="the user's own message" if not result.flags else "flagged, still the user's",
        )
