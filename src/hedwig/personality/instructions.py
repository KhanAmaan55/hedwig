"""Explicit instructions about how HEDWIG should be (docs/10 §5.1, §5.4).

"Please be more concise" is the strongest evidence there is (weight 1.0), and the only kind a
deterministic rule can read honestly. Everything else that would move a trait — patterns in
feedback, consistent behaviour, outcomes — needs reflection.

**Strict on purpose.** A pattern matches a request *addressed to HEDWIG* ("be more concise",
"you're too formal", "just give me a recommendation"), never a description of the world ("that
report was too long", "the meeting was very formal"). The asymmetry decides it: a false
positive silently changes who HEDWIG is; a false negative means the user asks again.

Pure. Called from two places — the mind reader, to honour the request in the reply that hears
it, and the engine's subscriber, to make the change durable — and because both call this one
function, they cannot disagree about what was asked (docs/27 §4.3).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from hedwig.core.ports.personality import Trait

# Who the sentence is addressed to. Every pattern below is anchored to one of these, which is
# what separates "be shorter" (an instruction) from "it was shorter" (a description).
_ASK = r"(?:please\s+|can you\s+|could you\s+|would you\s+|try to\s+|i'?d like you to\s+|i want you to\s+)?"
_YOU_ARE = r"(?:you(?:'re| are)\s+(?:being\s+)?)"
_TOO = r"(?:too|so|way too|a bit too|rather)"


@dataclass(frozen=True, slots=True)
class Instruction:
    trait: Trait
    direction: int
    """+1 raises the trait, -1 lowers it."""
    phrase: str
    """The words that matched, for the history row and the acknowledgement."""


_GUARD = r"(?<!n't )(?<!not )(?<!never )(?<!me to )(?<!him to )(?<!her to )(?<!them to )(?<!us to )"
"""Not negated, not reported. "Don't be more concise" is the opposite request, and "she told
me to be more concise" is not a request to HEDWIG at all. Both were misread before this guard
existed; tests pin them."""

_END = (
    r"(?=\s*(?:[.!?,;:)]|$|please\b|now\b|from now on\b|for me\b|with me\b"
    r"|(?:and|but|also) (?:be|stop|keep|don'?t|give|stay|just|quit)\b"
    r"|in (?:your |these |the )?(?:answers|replies|responses)\b))"
)
"""The instruction must end its clause. "Keep it short." is a request; "keep it short and
sweet is a saying" mentions one, and "be more concise in my essays" is about something else.

A conjunction counts as an end only when another instruction follows it — "be concise and stop
joking" is two requests, "keep it short and sweet" is an idiom."""


def _rule(pattern: str) -> re.Pattern[str]:
    return re.compile(_GUARD + pattern + _END, re.IGNORECASE)


RULES: tuple[tuple[re.Pattern[str], Trait, int], ...] = (
    # -- verbosity -------------------------------------------------------------------
    (
        _rule(rf"\b{_ASK}be (?:more )?(?:concise|brief|briefer|terse|succinct)\b"),
        Trait.VERBOSITY,
        -1,
    ),
    (
        _rule(rf"\b{_ASK}(?:keep it|keep (?:your )?(?:answers|replies|responses)) (?:short|brief)"),
        Trait.VERBOSITY,
        -1,
    ),
    (
        _rule(rf"\b{_ASK}(?:give|write) (?:me )?shorter (?:answers|replies|responses)\b"),
        Trait.VERBOSITY,
        -1,
    ),
    (_rule(rf"\b{_YOU_ARE}{_TOO} (?:long[- ]winded|verbose|wordy)\b"), Trait.VERBOSITY, -1),
    (_rule(rf"\b(?:your )?(?:answers|replies|responses) are {_TOO} long\b"), Trait.VERBOSITY, -1),
    (
        _rule(
            rf"\b{_ASK}(?:give|write) (?:me )?(?:more detail(?:ed)?|longer) (?:answers|replies|responses)\b"
        ),
        Trait.VERBOSITY,
        +1,
    ),
    (_rule(rf"\b{_ASK}be more (?:detailed|thorough)\b"), Trait.VERBOSITY, +1),
    (_rule(rf"\b{_YOU_ARE}{_TOO} (?:terse|brief|curt)\b"), Trait.VERBOSITY, +1),
    # -- formality -------------------------------------------------------------------
    (_rule(rf"\b{_ASK}be (?:more )?(?:formal|professional)\b"), Trait.FORMALITY, +1),
    (_rule(rf"\b{_ASK}be (?:less formal|more casual|more relaxed)\b"), Trait.FORMALITY, -1),
    (_rule(rf"\b{_YOU_ARE}{_TOO} (?:formal|stiff)\b"), Trait.FORMALITY, -1),
    (_rule(rf"\b{_YOU_ARE}{_TOO} (?:casual|informal)\b"), Trait.FORMALITY, +1),
    # -- humour ----------------------------------------------------------------------
    (
        _rule(rf"\b{_ASK}(?:stop|quit) (?:joking(?: around)?|making jokes|being funny)\b"),
        Trait.HUMOUR,
        -1,
    ),
    (_rule(rf"\b{_ASK}be (?:more serious|less jokey)\b"), Trait.HUMOUR, -1),
    (_rule(rf"\b{_ASK}be (?:funnier|more funny|more playful with jokes)\b"), Trait.HUMOUR, +1),
    # -- assertiveness ---------------------------------------------------------------
    (
        _rule(
            rf"\b{_ASK}(?:just )?(?:tell me what to do|give me (?:a|your) (?:clear )?recommendation)\b"
        ),
        Trait.ASSERTIVENESS,
        +1,
    ),
    (_rule(rf"\b{_ASK}be more (?:direct|decisive)\b"), Trait.ASSERTIVENESS, +1),
    (
        _rule(rf"\b{_ASK}(?:don'?t|do not|stop) (?:tell(?:ing)? me what to do)\b"),
        Trait.ASSERTIVENESS,
        -1,
    ),
    (_rule(rf"\b{_ASK}(?:just )?give me (?:the )?options\b"), Trait.ASSERTIVENESS, -1),
    (_rule(rf"\b{_YOU_ARE}{_TOO} (?:pushy|bossy|opinionated)\b"), Trait.ASSERTIVENESS, -1),
    # -- warmth ----------------------------------------------------------------------
    (_rule(rf"\b{_ASK}be (?:warmer|friendlier|more friendly)\b"), Trait.WARMTH, +1),
    (_rule(rf"\b{_YOU_ARE}{_TOO} (?:cold|distant|robotic)\b"), Trait.WARMTH, +1),
    (_rule(rf"\b{_YOU_ARE}{_TOO} (?:gushy|effusive|saccharine|over the top)\b"), Trait.WARMTH, -1),
    # -- playfulness -----------------------------------------------------------------
    (
        _rule(rf"\b{_ASK}(?:stop|quit) (?:digressing|going off on tangents|rambling)\b"),
        Trait.PLAYFULNESS,
        -1,
    ),
    (_rule(rf"\b{_ASK}stay on (?:topic|the point)\b"), Trait.PLAYFULNESS, -1),
)

ACKNOWLEDGEMENTS: dict[tuple[Trait, int], str] = {
    (Trait.VERBOSITY, -1): "be more concise",
    (Trait.VERBOSITY, +1): "give more detail",
    (Trait.FORMALITY, +1): "be more formal",
    (Trait.FORMALITY, -1): "be more casual",
    (Trait.HUMOUR, -1): "keep it serious",
    (Trait.HUMOUR, +1): "lighten up a little",
    (Trait.ASSERTIVENESS, +1): "give clearer recommendations",
    (Trait.ASSERTIVENESS, -1): "offer options rather than verdicts",
    (Trait.WARMTH, +1): "be warmer",
    (Trait.WARMTH, -1): "tone it down",
    (Trait.PLAYFULNESS, -1): "stay on the point",
}


def instruction_directions(values: Sequence[tuple[Trait, int]]) -> dict[Trait, int]:
    """Collapse detected instructions to one direction per trait.

    "Be shorter. No really, shorter." is one instruction, not two. A contradictory pair in one
    message ("be more concise, and be more detailed") cancels rather than letting whichever
    came last win.
    """
    totals: dict[Trait, int] = {}
    for trait, direction in values:
        totals[trait] = totals.get(trait, 0) + direction
    return {trait: (1 if total > 0 else -1) for trait, total in totals.items() if total}


def detect_instructions(text: str) -> tuple[Instruction, ...]:
    """Every explicit instruction in a message, one per trait.

    Contradictory instructions in one message cancel rather than letting word order decide —
    "shorter, but more detailed" is a request to be better, not a trait change.
    """
    found: list[tuple[Trait, int, str]] = []
    for pattern, trait, direction in RULES:
        match = pattern.search(text)
        if match:
            found.append((trait, direction, match.group(0).strip()))

    directions = instruction_directions([(trait, direction) for trait, direction, _ in found])
    seen: set[Trait] = set()
    result: list[Instruction] = []
    for trait, direction, phrase in found:
        if trait in seen or directions.get(trait) != direction:
            continue
        seen.add(trait)
        result.append(Instruction(trait=trait, direction=direction, phrase=phrase))
    return tuple(result)


def acknowledgement(instructions: tuple[Instruction, ...]) -> str | None:
    """A one-turn directive that makes the change visible rather than mysterious (docs/10 §5.4).

    Directive, not script: it tells the model *that* to acknowledge, not the words to use.
    """
    phrases = [
        ACKNOWLEDGEMENTS[(i.trait, i.direction)]
        for i in instructions
        if (i.trait, i.direction) in ACKNOWLEDGEMENTS
    ]
    if not phrases:
        return None
    joined = phrases[0] if len(phrases) == 1 else ", ".join(phrases[:-1]) + " and " + phrases[-1]
    return (
        f"They just asked you to {joined}. Acknowledge it in a few words, then do it in this reply."
    )
