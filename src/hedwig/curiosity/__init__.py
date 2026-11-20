"""Curiosity: what HEDWIG knows it does not know, and finding out (docs/11)."""

from hedwig.curiosity.detect import GapDetector
from hedwig.curiosity.explore import Exploration, Explorer
from hedwig.curiosity.mind import FindingsMindReader
from hedwig.curiosity.store import CuriosityStore, Finding, Gap

__all__ = [
    "CuriosityStore",
    "Exploration",
    "Explorer",
    "Finding",
    "FindingsMindReader",
    "Gap",
    "GapDetector",
]
