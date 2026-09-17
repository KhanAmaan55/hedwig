"""One definition of "a token".

Retrieval packs a working set to a token budget; the brain accounts for the window it adds
outside that budget. If those two used different arithmetic, the number reported as the
context size would not be the number anything actually enforced — so the estimate lives
here, in one place, and both import it.

It is an *estimate*. The real count depends on the tokeniser of whichever model the gateway
routes to, which is not known at retrieval time and differs between models anyway. Four
characters per token is the usual English approximation and errs slightly conservative,
which is the direction to err in when the consequence is an overrun prompt.
"""

from __future__ import annotations

CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Approximate token count. Never zero for non-empty text, so nothing is free."""
    if not text:
        return 0
    return max(1, len(text) // CHARS_PER_TOKEN)
