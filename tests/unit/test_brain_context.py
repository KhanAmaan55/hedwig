"""Generating the response context (docs/26 §5).

A pure function, so these are pure tests: no graph, no database, no clock. That is the
whole argument for assembly being a function rather than a node — the decisions it makes
are the ones that matter, and they are checkable one at a time.
"""

from __future__ import annotations

from hedwig.brain.context import assemble_context, citations, window_tokens
from hedwig.core.ports.brain import (
    ContextItem,
    RecalledContext,
    ToolOutcome,
    TrustTier,
)
from hedwig.core.ports.sessions import WindowMessage


def _window(count: int = 2) -> tuple[WindowMessage, ...]:
    return tuple(
        WindowMessage(role="user" if index % 2 == 0 else "hedwig", text=f"line {index}", at="")
        for index in range(count)
    )


def _item(text: str = "a memory", **overrides: object) -> ContextItem:
    defaults: dict[str, object] = {
        "text": text,
        "source": "episode",
        "score": 0.5,
        "memory_id": "ep_1",
    }
    defaults.update(overrides)
    return ContextItem(**defaults)  # type: ignore[arg-type]


# =========================================================================
# What is present
# =========================================================================


def test_an_empty_turn_assembles_an_empty_context() -> None:
    """The first message of the first conversation. A normal state, not an error."""
    context = assemble_context()

    assert context.is_empty
    assert context.token_count == 0
    assert context.cited_memory_ids == ()


def test_every_source_survives_assembly() -> None:
    context = assemble_context(
        window=_window(),
        recalled=RecalledContext(items=(_item(),)),
        outcomes=(ToolOutcome(name="clock", ok=True, output="12:00"),),
    )

    assert len(context.window) == 2
    assert len(context.recalled) == 1
    assert len(context.outcomes) == 1


def test_the_window_survives_even_when_nothing_was_recalled() -> None:
    """Retrieval returning nothing must not cost the model the current conversation
    (docs/06 §5.1)."""
    context = assemble_context(window=_window(4))

    assert len(context.window) == 4
    assert context.recalled == ()
    assert not context.is_empty


# =========================================================================
# The budget
# =========================================================================


def test_the_window_is_counted_but_not_charged_to_the_retrieval_budget() -> None:
    """docs/06 §5.1. An unbudgeted item that is also uncounted is how a context silently
    doubles in size."""
    recalled = RecalledContext(items=(_item(),), token_count=100)
    context = assemble_context(window=_window(4), recalled=recalled)

    assert context.recalled_tokens == 100, "the window must not inflate the retrieval count"
    assert context.window_tokens > 0, "the window must not be free"
    assert context.token_count == context.recalled_tokens + context.window_tokens


def test_retrievals_own_token_count_is_trusted() -> None:
    """Two pieces of arithmetic that can disagree are one piece of arithmetic too many."""
    recalled = RecalledContext(items=(_item("x" * 400),), token_count=7)

    assert assemble_context(recalled=recalled).recalled_tokens == 7


def test_tokens_are_estimated_when_retrieval_did_not_count() -> None:
    recalled = RecalledContext(items=(_item("x" * 400),))

    assert assemble_context(recalled=recalled).recalled_tokens == 100


def test_starvation_is_carried_forward_rather_than_hidden() -> None:
    context = assemble_context(recalled=RecalledContext(items=(_item(),), dropped=9))

    assert context.dropped == 9


def test_window_tokens_include_the_speaker() -> None:
    """The role is part of the prompt, so it is part of the cost."""
    assert window_tokens((WindowMessage(role="user", text="hello there", at=""),)) > 0


# =========================================================================
# Trust
# =========================================================================


def test_untrusted_material_is_flagged_at_the_top() -> None:
    """A responder that has to scan for untrusted content will one day forget
    (docs/13 §6)."""
    context = assemble_context(
        recalled=RecalledContext(items=(_item(trust=TrustTier.UNTRUSTED),)),
    )

    assert context.has_untrusted is True


def test_trusted_material_does_not_raise_the_flag() -> None:
    context = assemble_context(recalled=RecalledContext(items=(_item(trust=TrustTier.USER),)))

    assert context.has_untrusted is False


def test_one_untrusted_item_among_many_still_raises_the_flag() -> None:
    items = (
        _item("first", memory_id="ep_1"),
        _item("second", memory_id="ep_2", trust=TrustTier.UNTRUSTED),
        _item("third", memory_id="ep_3"),
    )
    assert assemble_context(recalled=RecalledContext(items=items)).has_untrusted is True


def test_per_item_trust_is_preserved() -> None:
    items = (_item("a", memory_id="ep_1", trust=TrustTier.UNTRUSTED),)
    context = assemble_context(recalled=RecalledContext(items=items))

    assert context.recalled[0].trust is TrustTier.UNTRUSTED


# =========================================================================
# Citations — the input to reinforcement
# =========================================================================


def test_citations_are_what_was_shown() -> None:
    items = (_item("a", memory_id="ep_1"), _item("b", memory_id="ep_2"))

    assert assemble_context(recalled=RecalledContext(items=items)).cited_memory_ids == (
        "ep_1",
        "ep_2",
    )


def test_citations_keep_order_and_drop_repeats() -> None:
    """Reinforcing the same memory twice for one turn would over-reward it (docs/06 §7.2)."""
    items = (
        _item("a", memory_id="ep_1"),
        _item("b", memory_id="ep_2"),
        _item("c", memory_id="ep_1"),
    )

    assert citations(items) == ("ep_1", "ep_2")


def test_an_item_with_no_memory_id_is_not_cited() -> None:
    """Nothing to reinforce, so nothing is claimed."""
    assert citations((_item("a", memory_id=""),)) == ()
