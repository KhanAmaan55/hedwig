"""Reflection: HEDWIG reviewing its own experience (docs/12).

T1 (session, docs/12 §14) and T2 (nightly, §15).
"""

from hedwig.reflection.nightly import NightlyReflection
from hedwig.reflection.periodic import PeriodicReflection
from hedwig.reflection.session import Reflection, SessionReflector

__all__ = ["NightlyReflection", "PeriodicReflection", "Reflection", "SessionReflector"]
