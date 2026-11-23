"""Reactions to how HEDWIG talks (docs/10 §13). Precision is the point: a false reaction moves
who HEDWIG is, a missed one costs nothing. The second half was written after the rules."""

from __future__ import annotations

import pytest

from hedwig.personality.feedback import detect_feedback

REACTIONS = [
    ("lol you're hilarious today", "humour", 1),
    ("that was a LOT to read", "verbosity", -1),
    ("you can be less stiff with me, we're friends", "formality", -1),
    ("honestly just pick one for me, you're too wishy-washy", "assertiveness", 1),
    ("aww that's sweet of you", "warmth", 1),
    ("haha your jokes are getting old", "humour", -1),
    ("you sound like a corporate memo", "formality", -1),
    # written after the rules
    ("you write like a lawyer, relax", "formality", -1),
    ("that's way too much text for a yes or no", "verbosity", -1),
    ("you're being so vague, just tell me", "assertiveness", 1),
    ("ha, you cracked me up there", "humour", 1),
    ("you're a bit cold today", "warmth", 1),
    ("you always ramble on", "verbosity", -1),
    ("wow you're preachy", "assertiveness", -1),
    ("you ask the best questions", "curiosity", 1),
    ("your answers are getting really long", "verbosity", -1),
    ("you're too serious, loosen up", "playfulness", 1),
]

NOT_REACTIONS = [
    "ok",
    "thanks",
    "my boss writes such long emails, it's exhausting",
    "the lecture was way too formal for me",
    "I'm going to the beach tomorrow",
    "my sister is hilarious, she should do standup",
    "I need to write a shorter report for work",
    "that documentary was too long",
    "you're right, I forgot about that",
    "thank you so much",
    "you mentioned the moon earlier",
    "I told you my sister lives in Porto",
    "you know I hate mornings",
    "why are you so formal?",
    # written after the rules
    "that was really helpful, thanks",
    "you said the meeting was at 3",
    "you're the best",
    "your reminder worked",
    "that's too long a drive for me",
    "that's harsh, my boss fired him",
    "my dad is so serious all the time",
    "the film was hilarious",
    "you won't believe how stiff my neck is",
    "you know my brother, he's so pushy",
    "you were right, the essay was due today",
    "thank you, that was kind",
]


@pytest.mark.parametrize(("text", "trait", "direction"), REACTIONS)
def test_a_reaction_to_hedwigs_manner_is_read(text: str, trait: str, direction: int) -> None:
    assert [(r.trait.value, r.direction) for r in detect_feedback(text)] == [(trait, direction)]


@pytest.mark.parametrize("text", NOT_REACTIONS)
def test_everything_else_is_not(text: str) -> None:
    assert detect_feedback(text) == []
