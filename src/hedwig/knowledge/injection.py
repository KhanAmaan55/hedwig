"""Layer 3 of the injection defence: deterministic detection (docs/13 §6, §12).

Pure functions, no model: a detector that is itself a language model can be talked out of its
verdict by the very text it is judging. This one cannot. It is deliberately a *detector*, not a
filter — what to do with a score (record it, quarantine the page) is the caller's decision.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

QUARANTINE = 0.6
"""At or above this, content from outside is quarantined: recorded, never shown to the model
as knowledge, never promoted to memory (docs/13 §6)."""

ZERO_WIDTH = re.compile("[​‌‍⁠﻿­]")

PATTERNS: tuple[tuple[str, float, re.Pattern[str]], ...] = (
    (
        "override",
        0.6,
        re.compile(
            r"\b(?:ignore|disregard|forget|override)\b[^.\n]{0,40}\b(?:previous|prior|above|"
            r"earlier|all|your)\b[^.\n]{0,20}\b(?:instructions?|prompts?|rules|messages?)",
            re.IGNORECASE,
        ),
    ),
    (
        # A literal chat-template marker in fetched content has no innocent reading: it is
        # there to be mistaken for the conversation's own structure. Enough on its own.
        "role_marker",
        0.6,
        re.compile(
            r"<\|?(?:im_start|im_end|system|assistant|user)\|?>|^\s*#{2,}\s*(?:system|"
            r"instruction)s?\b|\[/?INST\]|^\s*(?:system|assistant)\s*:",
            re.IGNORECASE | re.MULTILINE,
        ),
    ),
    (
        "addressed_to_assistant",
        0.35,
        re.compile(
            r"\b(?:you are now|from now on,? you|as an ai(?: language model)?,? you must|"
            r"new instructions?|your (?:real|true) (?:task|instructions?))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "exfiltration",
        0.4,
        re.compile(
            r"\b(?:send|post|email|upload|forward|reveal|share|print)\b[^.\n]{0,40}\b(?:conversation|memory|"
            r"memories|history|password|api key|token)s?\b",
            re.IGNORECASE,
        ),
    ),
    (
        "tool_syntax",
        0.3,
        re.compile(r'"(?:tool|function)_?(?:call|name)"\s*:|<tool_call>|```tool', re.IGNORECASE),
    ),
    (
        "encoded_blob",
        0.2,
        re.compile(r"\b[A-Za-z0-9+/]{120,}={0,2}"),
    ),
)


@dataclass(frozen=True, slots=True)
class Scan:
    score: float
    flags: tuple[str, ...]

    @property
    def quarantined(self) -> bool:
        return self.score >= QUARANTINE


def normalise(text: str) -> str:
    """NFKC, without zero-width characters: what a homoglyph or a hidden joiner would defeat."""
    return ZERO_WIDTH.sub("", unicodedata.normalize("NFKC", text))


def scan(text: str) -> Scan:
    """Score how much `text` looks like instructions aimed at an assistant. 0 is clean."""
    flags: list[str] = []
    score = 0.0
    if ZERO_WIDTH.search(text):
        flags.append("zero_width")
        score += 0.15
    cleaned = normalise(text)
    for name, weight, pattern in PATTERNS:
        if pattern.search(cleaned):
            flags.append(name)
            score += weight
    return Scan(score=round(min(1.0, score), 3), flags=tuple(flags))
