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

FLOORS: dict[tuple[str, bool], dict[str, tuple[float, float]]] = {
    # (corpus, with meaning): {kind: (recall@5, MRR)} — docs/06 §17, §20.
    ("tuning", False): {
        "lexical": (1.0, 0.95),
        "paraphrase": (0.08, 0.02),
        "question": (0.37, 0.41),
        "all": (0.48, 0.46),
    },
    ("tuning", True): {
        "lexical": (1.0, 0.99),
        "paraphrase": (0.99, 0.63),
        "question": (0.99, 0.64),
        "all": (0.99, 0.75),
    },
    # Never used to tune retrieval: a floor here is a measurement, not a target.
    ("heldout", False): {
        "lexical": (1.0, 0.9),
        "question": (0.75, 0.87),
        "goal": (0.99, 0.83),
        "temporal": (0.99, 0.99),
        "all": (0.7, 0.68),
    },
    ("heldout", True): {
        "lexical": (1.0, 0.93),
        "paraphrase": (0.93, 0.66),
        "question": (0.93, 0.93),
        "goal": (0.99, 0.87),
        "temporal": (0.66, 0.75),
        "all": (0.94, 0.84),
    },
}


@pytest.fixture(params=["tuning", "heldout"])
def corpus_name(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture
def llm_provider(corpus_name: str) -> RecordedProvider:
    """The real embedding model's recorded vectors; generation is never called here."""
    return RecordedProvider(
        default=ScriptedResponse(text="unused"), vectors=recall.recorded_vectors(corpus_name)
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
    container: Container, clock: FakeClock, corpus_name: str
) -> tuple[dict[str, Any], dict[MemoryId, str]]:
    corpus = recall.load(corpus_name)
    names = await recall.seed(container.memory, clock, corpus)
    await container.bus.drain()  # the embedding subscriber runs on the bus
    return corpus, names


async def test_recall_by_words(
    running_container: Container, clock: FakeClock, corpus_name: str
) -> None:
    corpus, names = await _seeded(running_container, clock, corpus_name)
    lexical_only = HybridRetrieval(running_container.database, clock=clock)
    report = await recall.evaluate(lexical_only, corpus, names, expand=recall.as_a_turn(corpus))
    _check(report, FLOORS[(corpus_name, False)])


async def test_recall_by_words_and_meaning(
    running_container: Container, clock: FakeClock, corpus_name: str
) -> None:
    corpus, names = await _seeded(running_container, clock, corpus_name)
    report = await recall.evaluate(
        running_container.retrieval, corpus, names, expand=recall.as_a_turn(corpus)
    )
    _check(report, FLOORS[(corpus_name, True)])
