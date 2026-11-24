"""Measure recall on the fixed corpus with a live embedding model (MEM-05/MEM-04, docs/06 §17).

    uv run python scripts/eval_recall.py                 # lexical vs lexical + semantic
    uv run python scripts/eval_recall.py --record        # also save the vectors for the test suite
    uv run python scripts/eval_recall.py --model embeddinggemma

Runs the real store and retrieval in a throwaway data directory; nothing touches your HEDWIG
data. `--record` embeds every corpus text and query with the model and writes them, as float16,
to `tests/evals/recall_vectors.json`, which `test_recall.py` replays so the semantic floors are
real numbers that run without a model.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import struct
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("HEDWIG_LOGGING__LEVEL", "error")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests" / "evals"))

import recall  # noqa: E402

from hedwig.core.config import load_config  # noqa: E402
from hedwig.memory.retrieval import episode_text  # noqa: E402
from hedwig.wiring import build_container  # noqa: E402

VECTORS = ROOT / "tests" / "evals" / "recall_vectors.json"


HYDE_INSTRUCTIONS = (
    "Write one short sentence that could be a note about the user answering their question, "
    'in the third person ("The user ..."). Guess freely; it is only used to search.'
)


def hyde(container: object, base: recall.Expand, generator_model: str) -> recall.Expand:
    """An experiment (docs/06 §20): also search a model-written guess at the answer."""
    from hedwig.core.ports.llm import GenerationRequest, Message, Role

    async def expand(text: str) -> list[str]:
        queries = list(await base(text))
        result = await container.llm.generate(  # type: ignore[attr-defined]
            GenerationRequest(
                messages=(
                    Message(role=Role.SYSTEM, content=HYDE_INSTRUCTIONS),
                    Message(role=Role.USER, content=text),
                ),
                purpose="plan.hyde",
                max_tokens=40,
                temperature=0.0,
                model=generator_model,
            )
        )
        return [*queries, result.text.strip()]

    return expand


async def measure(
    model: str, *, semantic: bool, corpus_name: str = "tuning", hyde_model: str | None = None
) -> recall.Report:
    with tempfile.TemporaryDirectory() as data:
        config = load_config(
            runtime={"data_dir": data, "environment": "development"},
            llm={"provider": "ollama", "embedding": model},
            memory={"semantic_recall": semantic},
            scheduler={"enabled": False},
            logging={"level": "error", "file_enabled": False},
        )
        container = build_container(config)
        await container.start()
        try:
            corpus = recall.load(corpus_name)
            names = await recall.seed(container.memory, container.clock, corpus)
            await container.bus.drain()
            registry_index = container.registry.get("embeddings")
            await registry_index.backfill()  # type: ignore[attr-defined]
            expand = recall.as_a_turn(corpus)
            if hyde_model:
                expand = hyde(container, expand, hyde_model)
            return await recall.evaluate(container.retrieval, corpus, names, expand=expand)
        finally:
            await container.stop()


async def record(model: str, corpus_name: str = "tuning") -> int:
    config = load_config()
    corpus = recall.load(corpus_name)
    documents = [
        episode_text(m["title"], m["text"]) if m["kind"] == "episode" else m["text"]
        for m in corpus["memories"]
    ]
    texts = [config.llm.embedding_document_prefix + d for d in documents]
    expand = recall.as_a_turn(corpus)  # the queries a turn would search, goal ones included
    queries = {q for query in corpus["queries"] for q in await expand(query["text"])}
    texts += [config.llm.embedding_query_prefix + q for q in sorted(queries)]
    with tempfile.TemporaryDirectory() as data:
        container = build_container(
            load_config(
                runtime={"data_dir": data},
                llm={"provider": "ollama", "embedding": model},
                scheduler={"enabled": False},
                logging={"level": "error", "file_enabled": False},
            )
        )
        vectors = await container.llm.embed(texts, model=model)
        container.database.close()
    packed = {
        text: base64.b64encode(struct.pack(f"<{len(v)}e", *v)).decode()
        for text, v in zip(texts, vectors, strict=True)
    }
    recall.CORPORA[corpus_name][1].write_text(
        json.dumps({"model": model, "format": "float16-le-base64", "vectors": packed}, indent=0)
        + "\n"
    )
    return len(packed)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default=load_config().llm.embedding)
    parser.add_argument("--record", action="store_true")
    parser.add_argument(
        "--corpus",
        choices=sorted(recall.CORPORA),
        default="tuning",
        help="`heldout` was never used to tune retrieval (docs/06 §20).",
    )
    parser.add_argument("--hyde", metavar="MODEL", help="Experiment: also search a guessed answer.")
    args = parser.parse_args()

    for semantic in (False, True):
        report = await measure(
            args.model, semantic=semantic, corpus_name=args.corpus, hyde_model=args.hyde
        )
        print(f"\n{'lexical + semantic (' + args.model + ')' if semantic else 'lexical only'}")
        print(recall.table(report.summary()))
        for miss in report.misses():
            print(f"  miss [{miss.kind}] {miss.text!r}: top five {miss.ranked[:5]}")
    if args.record:
        count = await record(args.model, args.corpus)
        print(f"\nrecorded {count} vectors to {recall.CORPORA[args.corpus][1].relative_to(ROOT)}")


if __name__ == "__main__":
    asyncio.run(main())
