"""Generating the response context (docs/26 §5).

The stage between *what was recalled* and *what is said*: window, memories and tool results
become one ordered, trust-labelled, token-accounted object.

A pure function rather than a node, for the reason in docs/26 §2 — the composition stage is
reached from three routing decisions that all return the literal `"compose"`, and inserting
a node would mean either rewriting those literals or sending a reader somewhere other than
where the edge said. Being pure, it is also exhaustively testable without a graph, which is
the same trade `routing.py` makes.

**It produces structure, not prose.** docs/07 §1 is explicit that the brain owns no prompt
content. What is decided here is what is present, in what order, and under whose trust.
Rendering belongs to the responder.
"""

from __future__ import annotations

from collections.abc import Sequence

from hedwig.core.ports.brain import (
    ContextItem,
    MindSnapshot,
    RecalledContext,
    ResponseContext,
    ToolOutcome,
    TrustTier,
)
from hedwig.core.ports.sessions import WindowMessage
from hedwig.core.text import estimate_tokens


def assemble_context(
    *,
    window: Sequence[WindowMessage] = (),
    recalled: RecalledContext | None = None,
    outcomes: Sequence[ToolOutcome] = (),
    mind: MindSnapshot | None = None,
) -> ResponseContext:
    """Assemble everything the responder may see.

    Four decisions, each of which has a bug attached to getting it wrong:

    1. **Order.** Memories first (oldest first, as retrieval returned them), then tool
       results, then the window last — closest to the question. Putting retrieval last is
       how a model loses the thread of the current conversation because a two-year-old
       memory outranked the previous sentence (docs/06 §5.1).
    2. **The window is not charged to the retrieval budget**, but its tokens *are* counted.
       An unbudgeted item that is also uncounted is how a context silently doubles.
    3. **Trust travels with each item and is hoisted to the top.** A responder that has to
       scan for untrusted material is a responder that will one day forget (docs/13 §6).
    4. **Citations are what was shown**, which is the set that gets reinforced afterwards
       (docs/06 §7.2).
    5. **Cognition travels with the context.** The directives are produced by emotion and
       rendered by the responder; carrying them here means the responder reads one object,
       and means a test can assert they arrived (docs/07 §14.4).
    """
    recalled = recalled or RecalledContext()
    mind = mind or MindSnapshot()

    items = tuple(recalled.items)
    window_messages = tuple(window)
    tool_outcomes = tuple(outcomes)

    return ResponseContext(
        window=window_messages,
        recalled=items,
        outcomes=tool_outcomes,
        window_tokens=window_tokens(window_messages),
        # Retrieval already counted and enforced its own budget; trusting its number keeps
        # one arithmetic, not two that can disagree (docs/26 §5).
        recalled_tokens=recalled.token_count or sum(estimate_tokens(item.text) for item in items),
        dropped=recalled.dropped,
        cited_memory_ids=citations(items),
        has_untrusted=any(item.trust is TrustTier.UNTRUSTED for item in items),
        directives=mind.directives,
        mood=dict(mind.mood),
    )


def window_tokens(window: Sequence[WindowMessage]) -> int:
    """What the verbatim window costs. Counted, never budgeted."""
    return sum(estimate_tokens(f"{message.role}: {message.text}") for message in window)


def citations(items: Sequence[ContextItem]) -> tuple[str, ...]:
    """The memories actually shown to the model, in order, without duplicates.

    "Shown" is not "used" — the honest version needs the model to declare its citations in
    structured output. Stated here rather than left for someone to over-read the access log
    (docs/26 §6.2).
    """
    seen: list[str] = []
    for item in items:
        if item.memory_id and item.memory_id not in seen:
            seen.append(item.memory_id)
    return tuple(seen)
