"""What should be said aloud, and how fast (docs/15 §13). Pure functions."""

from __future__ import annotations

import re

from hedwig.core.ports.emotion import EmotionState

MAX_CHARS = 1500
CITATION = re.compile(r"\s*\[\d+(?:\s*,\s*\d+)*\]")
URL = re.compile(r"https?://\S+")
MARKDOWN = re.compile(r"[*_`#>|~]+")


def speakable(text: str) -> str:
    """The reply without what nobody wants read aloud: citation marks, links, markup."""
    spoken = CITATION.sub("", text)
    spoken = URL.sub("a link", spoken)
    spoken = MARKDOWN.sub("", spoken)
    spoken = " ".join(spoken.split())
    if len(spoken) > MAX_CHARS:
        cut = spoken[:MAX_CHARS]
        spoken = cut[: cut.rfind(". ") + 1] or cut
    return spoken


def length_scale(state: EmotionState | None) -> float:
    """Piper's phoneme length from mood: livelier is a little quicker, tired a little slower.

    Bounded to 0.85-1.2 so the voice reflects presentation without performing a feeling.
    """
    if state is None:
        return 1.0
    scale = 1.0 - 0.25 * (state.arousal - 0.4) + 0.2 * (0.5 - state.energy)
    return round(max(0.85, min(1.2, scale)), 3)
