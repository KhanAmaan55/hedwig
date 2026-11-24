"""Deterministic cues about a piece of text (docs/13 §13, docs/11 §13).

Shared by the planner (should this turn research?) and curiosity (may this become a gap?), so
"what counts as a question about now" and "what counts as being about the user" are defined
once. Listed, so they are tested.
"""

from __future__ import annotations

import re

WEB_PATTERN = re.compile(
    r"\b(?:latest|newest|most recent|recently released|currently|nowadays|news|"
    r"this (?:week|month|year)|right now|price of|release date|new version|which version|"
    r"who won|weather|look (?:it|that|this) up|search (?:the web|online|for)|google it)\b"
    r"|\b20(?:2[5-9]|[3-9]\d)\b",
    re.I,
)
"""Cues that a question needs current information (docs/13 §13). Listed, so they are tested."""
PERSONAL_PATTERN = re.compile(r"\b(?:my|mine|our|ours|i|i'm|i've|me|we|us)\b", re.I)
"""A message about the user never goes to a search engine, cue or not: "my current project"
is not a web question, and sending it out would leak it (docs/13 §13)."""

UNANSWERED_PATTERN = re.compile(
    r"\b(?:i (?:don'?t|do not) know|i'?m not sure|i am not sure|i (?:can'?t|cannot) look "
    r"(?:it|that|this) up|i (?:don'?t|do not) have (?:any )?(?:information|access|data|details)|"
    r"i'?m unable to (?:find|look|check)|i am unable to (?:find|look|check)|"
    r"web search is (?:switched )?off)\b",
    re.I,
)
"""A reply that admits it could not answer: the strongest offline signal of a gap (docs/11 §3)."""

QUESTION_START = re.compile(
    r"^\s*(?:what|who|when|where|why|how|which|is|are|does|do|can|could|will|should)\b", re.I
)
GREETING = re.compile(r"^\s*(?:hi|hey|hello|good (?:morning|afternoon|evening)|morning|yo)\b", re.I)


def is_personal(text: str) -> bool:
    """Whether text talks about the user — and so must never leave the machine."""
    return bool(PERSONAL_PATTERN.search(text))


def needs_web(text: str) -> bool:
    """Whether a question asks for current information, and is not about the user."""
    return bool(WEB_PATTERN.search(text)) and not is_personal(text)


def is_question(text: str) -> bool:
    stripped = text.strip()
    return stripped.endswith("?") or bool(QUESTION_START.match(stripped))


def admits_not_knowing(reply: str) -> bool:
    return bool(UNANSWERED_PATTERN.search(reply))


AIMS = re.compile(
    r"\b(?:on track|working (?:towards?|on)|my (?:project|goal|plan|aim|target)s?|"
    r"how(?:'s| is| are) (?:it|things) going|where am i (?:with|at))\b",
    re.I,
)
"""A message about the user's aims that may name none of them (docs/06 §20): "Am I on track?"."""
AIM_WORDS = re.compile(r"\b(?:progress|practi[cs](?:e|ing)|training|update on)\b", re.I)
"""Words that are about the user's aims only when the user is in the sentence: "How's my
training going?" is; "Explain neural network training." is not."""
ABOUT_THE_USER = re.compile(
    r"\b(?:i|i'm|i've|me|my|mine|we|we're|our)\b|\bhow(?:'s| is| are) \w+(?: \w+)? going\b",
    re.I,
)


def refers_to_aims(text: str) -> bool:
    return bool(AIMS.search(text)) or bool(AIM_WORDS.search(text) and ABOUT_THE_USER.search(text))


def is_greeting(text: str) -> bool:
    return bool(GREETING.match(text)) and len(text.split()) <= 6
