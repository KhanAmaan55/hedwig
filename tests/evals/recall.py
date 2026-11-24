"""The recall eval (MEM-05, docs/06 §15): does retrieval find what a question is about?

A fixed corpus (`recall_corpus.json`) is loaded into a real memory store, each query is run
through the real `HybridRetrieval` exactly as a turn runs it (the user's words as the query, the
default policy), and the ranking is scored:

* **recall@5** — the share of a query's relevant memories in the top five;
* **hit@5** — whether at least one is;
* **MRR** — 1 / rank of the first relevant memory (0 if none in the top ten).

Ranks come from the score, not from the working set's order, which is chronological on
purpose (docs/06 §5.5). Used by `test_recall.py` (a regression floor) and
`scripts/eval_recall.py` (the live numbers, with a real embedding model).
"""

from __future__ import annotations

import base64
import json
import struct
from collections import defaultdict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from hedwig.core.ids import new_id
from hedwig.core.ports import Clock
from hedwig.core.ports.memory import Belief, BeliefStatus, Episode, RetrievalEngine
from hedwig.core.types import MemoryId

CORPUS = Path(__file__).with_name("recall_corpus.json")
VECTORS = Path(__file__).with_name("recall_vectors.json")
HELDOUT = Path(__file__).with_name("recall_heldout.json")
"""Written after retrieval was tuned on CORPUS, and never used to tune it (docs/06 §20)."""
HELDOUT_VECTORS = Path(__file__).with_name("recall_heldout_vectors.json")
CORPORA = {"tuning": (CORPUS, VECTORS), "heldout": (HELDOUT, HELDOUT_VECTORS)}
K = 5
DEPTH = 10


def load(name: str = "tuning") -> dict[str, Any]:
    return dict(json.loads(CORPORA[name][0].read_text(encoding="utf-8")))


def recorded_vectors(name: str = "tuning") -> dict[str, Sequence[float]]:
    """The real model's vectors for every corpus text and query, by exact input text."""
    data = json.loads(CORPORA[name][1].read_text(encoding="utf-8"))
    vectors: dict[str, Sequence[float]] = {}
    for text, packed in data["vectors"].items():
        raw = base64.b64decode(packed)
        vectors[text] = list(struct.unpack(f"<{len(raw) // 2}e", raw))
    return vectors


async def seed(store: Any, clock: Clock, corpus: dict[str, Any]) -> dict[MemoryId, str]:
    """Write the corpus into `store`; returns real id → corpus id."""
    names: dict[MemoryId, str] = {}
    now = clock.now()
    for memory in corpus["memories"]:
        when = now - timedelta(days=memory["days_ago"], hours=1)
        if memory["kind"] == "belief":
            memory_id = MemoryId(new_id("bel"))
            await store.add_belief(
                Belief(
                    id=memory_id,
                    statement=memory["text"],
                    valid_from=when,
                    status=BeliefStatus.ACTIVE,
                    confidence=0.8,
                )
            )
        else:
            memory_id = MemoryId(new_id("ep"))
            await store.add_episode(
                Episode(
                    id=memory_id, occurred_at=when, title=memory["title"], content=memory["text"]
                )
            )
        names[memory_id] = memory["id"]
    return names


@dataclass(frozen=True)
class Result:
    kind: str
    text: str
    relevant: tuple[str, ...]
    ranked: tuple[str, ...]

    @property
    def recall(self) -> float:
        return len(set(self.ranked[:K]) & set(self.relevant)) / len(self.relevant)

    @property
    def hit(self) -> float:
        return 1.0 if set(self.ranked[:K]) & set(self.relevant) else 0.0

    @property
    def reciprocal_rank(self) -> float:
        for rank, name in enumerate(self.ranked[:DEPTH], start=1):
            if name in self.relevant:
                return 1.0 / rank
        return 0.0


@dataclass(frozen=True)
class Report:
    results: tuple[Result, ...]

    def summary(self) -> dict[str, dict[str, float]]:
        groups: dict[str, list[Result]] = defaultdict(list)
        for result in self.results:
            groups[result.kind].append(result)
            groups["all"].append(result)
        return {
            kind: {
                "recall@5": round(sum(r.recall for r in rs) / len(rs), 3),
                "hit@5": round(sum(r.hit for r in rs) / len(rs), 3),
                "mrr": round(sum(r.reciprocal_rank for r in rs) / len(rs), 3),
                "n": len(rs),
            }
            for kind, rs in groups.items()
        }

    def misses(self) -> list[Result]:
        return [r for r in self.results if r.hit == 0.0]


Expand = Callable[[str], Awaitable[Sequence[str]]]


async def _literal(text: str) -> Sequence[str]:
    return [text]


def as_a_turn(corpus: dict[str, Any]) -> Expand:
    """Query planning as a turn does it, with the corpus's active goals in the snapshot."""
    from hedwig.brain.planner import RulePlanner
    from hedwig.core.ports.brain import TurnPolicy

    planner, goals = RulePlanner(), tuple(corpus.get("goals", ()))

    async def expand(text: str) -> Sequence[str]:
        return await planner.plan_queries(text, policy=TurnPolicy(), goals=goals)

    return expand


async def evaluate(
    retrieval: RetrievalEngine,
    corpus: dict[str, Any],
    names: dict[MemoryId, str],
    *,
    expand: Expand = _literal,
) -> Report:
    """Each query as a turn would search it: `expand` is the turn's query planning."""
    results = []
    for query in corpus["queries"]:
        working = await retrieval.search(list(await expand(query["text"])))
        ranked = sorted(working.items, key=lambda item: item.score, reverse=True)
        results.append(
            Result(
                kind=query["kind"],
                text=query["text"],
                relevant=tuple(query["relevant"]),
                ranked=tuple(names.get(item.memory_id, "?") for item in ranked),
            )
        )
    return Report(tuple(results))


def table(summary: dict[str, dict[str, float]]) -> str:
    lines = [f"{'kind':<11} {'n':>3} {'recall@5':>9} {'hit@5':>7} {'MRR':>6}"]
    for kind in ("lexical", "paraphrase", "question", "goal", "temporal", "all"):
        if kind in summary:
            s = summary[kind]
            lines.append(
                f"{kind:<11} {int(s['n']):>3} {s['recall@5']:>9.3f} {s['hit@5']:>7.3f} {s['mrr']:>6.3f}"
            )
    return "\n".join(lines)
