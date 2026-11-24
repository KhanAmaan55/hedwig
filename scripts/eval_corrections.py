"""Corrections in conversation, measured against a live model (docs/06 §19, MEM-07).

Each case: the beliefs HEDWIG was just using, what the user said, and which belief (if any)
it corrects with a word the correction must carry. Reports right proposals, wrong ones (the
worst kind: a proposal about the wrong thing, or one where there was none), and misses.

    uv run python scripts/eval_corrections.py --model gemma4:e2b
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile

from hedwig.core.config import load_config
from hedwig.core.ids import new_id
from hedwig.core.ports.memory import Belief, BeliefStatus
from hedwig.core.types import MemoryId
from hedwig.wiring import build_container

ANA = "The user's sister Ana lives in Porto."
VEG = "The user is vegetarian."
ACME = "The user works at Acme."
REX = "The user has a dog named Rex."
TRIP = "The user's trip to Porto is in June."
COFFEE = "The user drinks coffee every morning."

# (beliefs, what the user said, expected belief number or 0, a word the proposal must contain)
CASES: list[tuple[list[str], str, int, str]] = [
    ([ANA, VEG, ACME], "No, she lives in Lisbon.", 1, "lisbon"),
    ([ANA, VEG, ACME], "Actually, I'm vegan now, not vegetarian.", 2, "vegan"),
    ([ANA, VEG, ACME], "I no longer work at Acme, I joined Globex.", 3, "globex"),
    ([REX, TRIP], "Nope, the trip is in July.", 2, "july"),
    ([REX, TRIP], "Wrong, Rex is a cat, not a dog.", 1, "cat"),
    ([COFFEE], "I don't drink coffee anymore.", 1, "coffee"),
    ([ANA], "No, my sister is called Ines.", 1, "ines"),
    (["The user is learning Spanish."], "Not Spanish, Portuguese.", 1, "portuguese"),
    (["The user is 30 years old."], "That's not right, I'm 31.", 1, "31"),
    (["The user prefers short answers."], "Actually, I like detailed answers.", 1, "detailed"),
    ([ANA], "No, I meant the other book.", 0, ""),
    ([VEG], "Actually, let me rephrase the question.", 0, ""),
    ([ACME], "No, that's not what I asked.", 0, ""),
    ([TRIP], "That's wrong, the capital of Australia is Canberra.", 0, ""),
    ([REX], "Nope, I don't want a story right now.", 0, ""),
    ([COFFEE], "No, she lives in Lisbon.", 0, ""),
]

HELD_OUT: list[tuple[list[str], str, int, str]] = [
    # Written after the checks were tuned on CASES, and not used to tune them.
    (["The user's brother is called Tom.", ACME], "No, his name is Tim.", 1, "tim"),
    ([TRIP, REX], "Actually we're going to Faro, not Porto.", 1, "faro"),
    (["The user plays the guitar."], "I don't play anymore, I sold it.", 1, "guitar"),
    (["The user lives in London.", VEG], "Not London — Manchester.", 1, "manchester"),
    (["The user's favourite colour is blue."], "No, it's green.", 1, "green"),
    ([ANA, COFFEE], "No, the other way round, ask me later.", 0, ""),
    (["The user is learning Spanish."], "Actually, never mind, tell me a joke.", 0, ""),
    ([ACME, REX], "That's wrong, two plus two is four.", 0, ""),
]


async def run(model: str, cases: list[tuple[list[str], str, int, str]]) -> tuple[int, int, int]:
    right = wrong = missed = 0
    with tempfile.TemporaryDirectory() as data:
        config = load_config(
            runtime={"data_dir": data, "environment": "development"},
            llm={"provider": "ollama", "conversational": model, "utility": model},
            scheduler={"enabled": False},
            logging={"level": "error", "file_enabled": False},
        )
        container = build_container(config)
        await container.start()
        try:
            for number, (beliefs, said, expected, word) in enumerate(cases):
                ids = []
                for statement in beliefs:
                    belief_id = MemoryId(new_id("bel"))
                    await container.memory.add_belief(
                        Belief(
                            id=belief_id,
                            statement=statement,
                            valid_from=container.clock.now(),
                            status=BeliefStatus.ACTIVE,
                        )
                    )
                    ids.append(belief_id)
                proposal = await container.corrections.consider(
                    said, session_id=f"eval-{number}", turn_id=f"t{number}", recalled=ids
                )
                got = ids.index(proposal["memory_id"]) + 1 if proposal else 0
                text = proposal["proposed_text"] if proposal else ""
                if expected == 0:
                    ok, verdict = got == 0, "none"
                else:
                    ok = got == expected and word in text.lower()
                    verdict = "missed" if got == 0 else ("right" if ok else "wrong")
                if ok:
                    right += 1
                elif got == 0:
                    missed += 1
                else:
                    wrong += 1
                mark = "✓" if ok else "✗"
                print(f"{mark} {said[:48]:<48} → {verdict:<6} {text}", flush=True)
        finally:
            await container.stop()
    return right, wrong, missed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default=load_config().llm.utility)
    parser.add_argument("--held-out", action="store_true", help="Run only the held-out cases.")
    args = parser.parse_args()
    cases = HELD_OUT if args.held_out else CASES + HELD_OUT
    right, wrong, missed = asyncio.run(run(args.model, cases))
    print(f"\n{args.model}: {right}/{len(cases)} right · {wrong} wrong · {missed} missed")
    sys.exit(1 if wrong else 0)


if __name__ == "__main__":
    main()
