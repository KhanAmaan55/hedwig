"""Expression: emotional state to semantic avatar intent (docs/15, docs/28).

Owns no state and no tables. A pure resolver that every renderer — VRM today, 2D and headless
later — consumes through the same intent (ADR-0021).
"""

from __future__ import annotations

from hedwig.expression.resolver import normalise, resolve

__all__ = ["normalise", "resolve"]
