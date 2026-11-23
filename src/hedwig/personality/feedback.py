"""Reactions to how HEDWIG talks (docs/10 §5.1, §13).

"That was a wall of text", "you're hilarious", "you sound like a corporate memo": the user is not
asking for a change (that is `instructions.py`, the fast path) but saying how a reply landed.
docs/10 §5.1 calls it repeated explicit feedback, weight 0.8 — and drift needs three of them
over two days before anything moves.

**Rules, not a model.** Measured live on the default utility model: asked which messages react
to HEDWIG's manner, it labelled nearly every message with some trait ("thank you so much" →
more warmth, "you mentioned the moon earlier" → more curiosity) — 7 of 15 right, the same on
every run. So, like explicit instructions, reactions are read by strict patterns:

* the message must speak to HEDWIG ("you", "your") — "that's harsh" is usually about the world —
  unless the phrase can only be about text ("a lot to read", "wall of text"), and must not be a
  question: questions ask, they do not react;
* the words must be ones that are about manner, each tied to one trait and one direction.

The asymmetry decides it, as for instructions: a missed reaction costs nothing, a false one
moves who HEDWIG is. Pure; the tests pin both directions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from hedwig.core.ports.personality import Trait

_TO_HEDWIG = re.compile(r"\b(?:you|your|you'?re|yours|u|ur)\b", re.IGNORECASE)
_SOMEONE_ELSE = re.compile(
    r"\b(?:he|she|they)(?:'s|'re| is| was| are| were)\b"
    r"|\b(?:my|his|her|their|our) \w+(?:'s| is| was| are| were)\b",
    re.IGNORECASE,
)
"""A description of someone else ("you know my brother, he's so pushy", "my sister is
hilarious") is not about HEDWIG, whatever else the message says — found on fresh phrasings."""


@dataclass(frozen=True, slots=True)
class Reaction:
    trait: Trait
    direction: int
    """+1: the user wants, or welcomes, more of it; -1: less."""
    phrase: str


def _p(words: str) -> re.Pattern[str]:
    return re.compile(r"(?<!\w)(?:" + words + r")(?!\w)", re.IGNORECASE)


_ABOUT_TEXT = _p(r"a lot to read|wall of text|tl;?dr|too much text")
"""Phrases that can only be about something written — the reply — so they need no "you"."""

# One trait, one direction, phrases about *manner*, each only counted when the message speaks to
# HEDWIG. Direction is what the user wants: "you're a bit cold" asks for more warmth.
_RULES: tuple[tuple[Trait, int, re.Pattern[str]], ...] = (
    (
        Trait.VERBOSITY,
        -1,
        _p(
            r"too wordy|so wordy|long[- ]winded|rambl\w+|an essay|a novel"
            r"|way more than i (?:needed|asked for|wanted)|too much (?:detail|info(?:rmation)?)"
            r"|(?:answers?|repl(?:y|ies)|responses?|messages?) (?:is|are|was|were|get|getting)"
            r" (?:\w+ ){0,2}long"
        ),
    ),
    (Trait.VERBOSITY, +1, _p(r"too short|too brief|too terse|so terse")),
    (
        Trait.HUMOUR,
        +1,
        _p(
            r"(?:so |really )?(?:funny|hilarious)|made me laugh|cracked me up|good one"
            r"|love your (?:jokes|humou?r|sense of humou?r)"
        ),
    ),
    (
        Trait.HUMOUR,
        -1,
        _p(r"jokes are getting old|not funny|enough (?:with the )?jokes|too jokey|stop joking"),
    ),
    (
        Trait.FORMALITY,
        -1,
        _p(
            r"(?:so|too|bit|very|really) (?:stiff|formal)|less stiff|corporate|like a robot|robotic"
            r"|like a (?:memo|lawyer|textbook)|uptight"
        ),
    ),
    (Trait.FORMALITY, +1, _p(r"too casual|unprofessional")),
    (
        Trait.WARMTH,
        +1,
        _p(r"(?:sweet|kind|lovely|thoughtful) of you|(?:bit|so|very|too) cold|(?:being )?harsh"),
    ),
    (Trait.WARMTH, -1, _p(r"too (?:soppy|sappy|gushy|cheesy|sugary)|over the top")),
    (
        Trait.ASSERTIVENESS,
        +1,
        _p(
            r"wishy[- ]washy|make up your mind|(?:so|too|very) vague|noncommittal"
            r"|sitting on the fence"
        ),
    ),
    (Trait.ASSERTIVENESS, -1, _p(r"pushy|bossy|preachy|lecturing me")),
    (Trait.CURIOSITY, +1, _p(r"love (?:it )?(?:how|when) you ask|(?:good|great|best) questions")),
    (Trait.CURIOSITY, -1, _p(r"(?:so|too) many questions|stop asking")),
    (Trait.CONSCIENTIOUSNESS, +1, _p(r"(?:really|so|very) thorough")),
    (Trait.CONSCIENTIOUSNESS, -1, _p(r"overkill|pedantic|nitpick\w*")),
    (Trait.PATIENCE, +1, _p(r"(?:bit|so|very|too) impatient|rushing me")),
    (Trait.PLAYFULNESS, +1, _p(r"(?:so|too) serious|lighten up")),
    (Trait.PLAYFULNESS, -1, _p(r"too silly|so silly")),
    (Trait.EMPATHY, +1, _p(r"insensitive|don'?t get it|tone[- ]deaf")),
)


def detect_feedback(text: str) -> list[Reaction]:
    """Every reaction to HEDWIG's manner in `text`, one per trait at most."""
    stripped = text.strip()
    if not stripped or stripped.endswith("?"):
        return []  # a question asks; it does not react
    if _SOMEONE_ELSE.search(stripped):
        return []
    found: dict[Trait, Reaction] = {}
    about_text = _ABOUT_TEXT.search(stripped)
    if about_text:
        found[Trait.VERBOSITY] = Reaction(Trait.VERBOSITY, -1, about_text.group(0))
    if _TO_HEDWIG.search(stripped):
        for trait, direction, pattern in _RULES:
            match = pattern.search(stripped)
            if match and trait not in found:
                found[trait] = Reaction(trait, direction, match.group(0))
    return list(found.values())
