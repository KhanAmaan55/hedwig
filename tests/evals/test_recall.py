"""Recall must not get worse (MEM-05, MEM-04, docs/06 §17).

The floors are measured numbers, not targets: raise them when retrieval improves, and never
lower one without saying why in docs/06 §17. `-s` prints the table and every miss.

The semantic floors use the embedding model's real vectors, recorded once by
`scripts/eval_recall.py --record` and replayed here, so they run without a model.
"""

from __future__ import annotations

from typing import Any

import pytest
import recall

from hedwig.core.clock import FakeClock
from hedwig.core.types import MemoryId
from hedwig.llm.recorded import RecordedProvider, ScriptedResponse
from hedwig.memory import HybridRetrieval
from hedwig.wiring import Container

LEXICAL_FLOORS = {
    "lexical": (1.0, 0.9),
    "paraphrase": (0.16, 0.05),
    "question": (0.37, 0.41),
    "all": (0.51, 0.46),
}
SEMANTIC_FLOORS = {
    "lexical": (1.0, 0.95),
    "paraphrase": (0.95, 0.5),
    "question": (0.9, 0.6),
    "all": (0.95, 0.7),
}


@pytest.fixture
def llm_provider() -> RecordedProvider:
    """The real embedding model's recorded vectors; generation is never called here."""
    return RecordedProvider(
        default=ScriptedResponse(text="unused"), vectors=recall.recorded_vectors()
    )


def _check(report: recall.Report, floors: dict[str, tuple[float, float]]) -> None:
    summary = report.summary()
    print("\n" + recall.table(summary))
    for miss in report.misses():
        print(f"  miss [{miss.kind}] {miss.text!r}: top five {miss.ranked[:5]}")
    for kind, (floor_recall, floor_mrr) in floors.items():
        assert summary[kind]["recall@5"] >= floor_recall, (kind, summary[kind])
        assert summary[kind]["mrr"] >= floor_mrr, (kind, summary[kind])


async def _seeded(
    container: Container, clock: FakeClock
) -> tuple[dict[str, Any], dict[MemoryId, str]]:
    corpus = recall.load()
    names = await recall.seed(container.memory, clock, corpus)
    await container.bus.drain()  # the embedding subscriber runs on the bus
    return corpus, names


async def test_recall_by_words(running_container: Container, clock: FakeClock) -> None:
    corpus, names = await _seeded(running_container, clock)
    lexical_only = HybridRetrieval(running_container.database, clock=clock)
    _check(await recall.evaluate(lexical_only, corpus, names), LEXICAL_FLOORS)


async def test_recall_by_words_and_meaning(running_container: Container, clock: FakeClock) -> None:
    corpus, names = await _seeded(running_container, clock)
    _check(await recall.evaluate(running_container.retrieval, corpus, names), SEMANTIC_FLOORS)
