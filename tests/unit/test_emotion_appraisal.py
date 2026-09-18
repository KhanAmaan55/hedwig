"""The rule table and the mapping matrix (docs/09 §4).

One case per rule, deterministic, no clock. The most important test in the file is the last
one: an event nobody wrote a rule for changes nothing, which is the whole of the no-model
policy in §4.2 made checkable.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from hedwig.core.ports.emotion import NEUTRAL, Appraisal, Dimension
from hedwig.core.ports.event_bus import Event
from hedwig.emotion.appraisal import RULES, Appraiser
from hedwig.emotion.mapping import MATRIX, combine, deltas_for

AT = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def _event(type_: str, **payload: Any) -> Event:
    return Event(
        id=f"evt_{type_}",
        type=type_,
        occurred_at=AT,
        source="test",
        correlation_id="corr_1",
        payload=payload,
    )


def _appraise(type_: str, **payload: Any) -> Appraisal:
    return Appraiser().appraise(_event(type_, **payload))


# =========================================================================
# Conversation
# =========================================================================


def test_a_session_starting_is_social_and_a_little_new() -> None:
    appraisal = _appraise("conversation.session.started", session_id="s1", channel="cli")

    assert appraisal.social_valence > 0
    assert appraisal.novelty > 0


def test_thanks_is_the_strongest_social_signal_available_without_a_model() -> None:
    thanked = _appraise("conversation.message.received", text="thanks, that was exactly right")
    greeted = _appraise("conversation.message.received", text="hello there")
    plain = _appraise("conversation.message.received", text="the build is on line forty")

    assert thanked.social_valence > greeted.social_valence > plain.social_valence


def test_a_long_message_implies_effort() -> None:
    short = _appraise("conversation.message.received", text="ok, and then?")
    long = _appraise("conversation.message.received", text="x" * 900)

    assert long.effort > short.effort


def test_a_question_lowers_certainty() -> None:
    asked = _appraise("conversation.message.received", text="which one should we use?")
    told = _appraise("conversation.message.received", text="we should use the second one")

    assert asked.certainty < 0
    assert told.certainty == 0


def test_an_empty_message_appraises_to_nothing() -> None:
    assert _appraise("conversation.message.received", text="   ").is_neutral


def test_a_completed_turn_reads_as_things_going_well() -> None:
    appraisal = _appraise("conversation.turn.completed", status="completed", tool_calls=0)

    assert appraisal.goal_congruence > 0
    assert appraisal.certainty > 0


def test_a_failed_turn_costs_confidence_and_agency() -> None:
    appraisal = _appraise("conversation.turn.completed", status="failed")

    assert appraisal.goal_congruence < 0
    assert appraisal.certainty < 0
    assert appraisal.agency < 0


def test_a_truncated_turn_reads_as_work_rather_than_failure() -> None:
    truncated = _appraise("conversation.turn.completed", status="truncated")
    failed = _appraise("conversation.turn.completed", status="failed")

    assert truncated.effort > failed.effort
    assert truncated.goal_congruence > failed.goal_congruence


def test_a_refusal_is_consistent_with_the_identity_core() -> None:
    """The subtlest rule in the table, and the one most worth protecting: declining
    something correctly must not read to the emotion engine as failure, or HEDWIG learns to
    dread its own safety behaviour (docs/09 §6.1)."""
    refused = _appraise("conversation.turn.completed", status="refused")

    assert refused.norm_fit > 0
    assert (
        refused.goal_congruence
        > _appraise("conversation.turn.completed", status="failed").goal_congruence
    )


def test_tool_calls_add_effort_but_are_bounded() -> None:
    none = _appraise("conversation.turn.completed", status="completed", tool_calls=0)
    some = _appraise("conversation.turn.completed", status="completed", tool_calls=3)
    absurd = _appraise("conversation.turn.completed", status="completed", tool_calls=500)

    assert none.effort < some.effort <= absurd.effort <= 0.4


# =========================================================================
# Memory and system
# =========================================================================


def test_a_new_entity_is_the_most_novel_thing_that_happens() -> None:
    entity = _appraise("memory.entity.discovered", entity_id="e1", kind="place", name="Porto")
    belief = _appraise("memory.belief.formed", memory_id="b1", statement="x", confidence=0.4)

    assert entity.novelty > belief.novelty > 0


def test_storing_something_trivial_is_not_an_event() -> None:
    """Scaled by salience: every recall would otherwise be a small excitement."""
    trivial = _appraise("memory.episode.stored", memory_id="m1", salience=0.05)
    notable = _appraise("memory.episode.stored", memory_id="m2", salience=0.9)

    assert deltas_for(trivial)[Dimension.CURIOSITY] < deltas_for(notable)[Dimension.CURIOSITY]


def test_a_service_failing_is_read_as_something_being_wrong() -> None:
    appraisal = _appraise("system.service.failed", service="llm", error="boom")

    assert appraisal.goal_congruence < 0
    assert appraisal.certainty < 0


def test_a_succeeding_background_task_is_not_news() -> None:
    assert _appraise(
        "scheduler.task.completed", task="t", run_id="r", status="completed"
    ).is_neutral


# =========================================================================
# Escalation — the one piece of state the appraiser keeps
# =========================================================================


def test_the_third_consecutive_failure_reads_worse_than_the_first() -> None:
    appraiser = Appraiser()
    first = appraiser.appraise(_event("conversation.turn.completed", status="failed"))
    appraiser.appraise(_event("conversation.turn.completed", status="failed"))
    third = appraiser.appraise(_event("conversation.turn.completed", status="failed"))

    assert third.agency < first.agency
    assert third.goal_congruence < first.goal_congruence
    assert "in a row" in third.rationale


def test_one_success_resets_the_streak() -> None:
    appraiser = Appraiser()
    for _ in range(5):
        appraiser.appraise(_event("conversation.turn.completed", status="failed"))
    appraiser.appraise(_event("conversation.turn.completed", status="completed"))

    assert appraiser.consecutive_failures == 0


def test_an_unrelated_event_does_not_reset_the_streak() -> None:
    """Otherwise a memory write between two failures would hide the pattern."""
    appraiser = Appraiser()
    appraiser.appraise(_event("conversation.turn.completed", status="failed"))
    appraiser.appraise(_event("memory.entity.discovered", entity_id="e", kind="k", name="n"))
    appraiser.appraise(_event("conversation.turn.completed", status="failed"))

    assert appraiser.consecutive_failures == 2


# =========================================================================
# The no-model policy, made checkable
# =========================================================================


def test_an_event_with_no_rule_changes_nothing() -> None:
    """docs/09 §4.2. There is no fallback path: an unmatched event is neutral, full stop."""
    appraisal = _appraise("system.startup.completed", version="1.0")

    assert appraisal is NEUTRAL
    assert all(value == 0.0 for value in deltas_for(appraisal).values())


def test_appraisal_is_a_pure_function_of_the_payload() -> None:
    """Determinism is the property the model path was given up to keep."""
    payload = {"status": "completed", "tool_calls": 2}
    assert _appraise("conversation.turn.completed", **payload) == _appraise(
        "conversation.turn.completed", **payload
    )


def test_every_rule_is_for_an_event_that_actually_exists() -> None:
    """A rule for an unpublished event type is a rule that has never run."""
    from hedwig.core.bus import platform_catalogue

    catalogue = platform_catalogue()
    for event_type in RULES:
        assert event_type in catalogue, f"{event_type} is not a registered event type"


# =========================================================================
# The mapping matrix
# =========================================================================


def test_every_appraisal_dimension_has_a_row() -> None:
    from hedwig.core.ports.emotion import AppraisalDimension

    assert set(MATRIX) == set(AppraisalDimension)


def test_every_row_covers_every_dimension() -> None:
    """A missing cell is an unintended zero, which is invisible until it is a bug."""
    for row in MATRIX.values():
        assert set(row) == set(Dimension)


def test_effort_is_what_raises_stress_and_lowers_energy() -> None:
    from hedwig.core.ports.emotion import AppraisalDimension

    stress_column = {a: MATRIX[a][Dimension.STRESS] for a in AppraisalDimension}
    energy_column = {a: MATRIX[a][Dimension.ENERGY] for a in AppraisalDimension}

    assert max(stress_column, key=lambda a: stress_column[a]) is AppraisalDimension.EFFORT
    assert min(energy_column, key=lambda a: energy_column[a]) is AppraisalDimension.EFFORT


def test_novelty_drives_curiosity_above_everything_else() -> None:
    from hedwig.core.ports.emotion import AppraisalDimension

    curiosity_column = {a: MATRIX[a][Dimension.CURIOSITY] for a in AppraisalDimension}
    assert max(curiosity_column, key=lambda a: curiosity_column[a]) is AppraisalDimension.NOVELTY


def test_social_valence_is_trusts_strongest_input() -> None:
    """The role `warmth` held before ADR-0019 folded the two together."""
    from hedwig.core.ports.emotion import AppraisalDimension

    trust_column = {a: MATRIX[a][Dimension.TRUST] for a in AppraisalDimension}
    assert max(trust_column, key=lambda a: trust_column[a]) is AppraisalDimension.SOCIAL_VALENCE


def test_deltas_scale_linearly_with_significance() -> None:
    """What lets a tick coalesce many events by simply summing their deltas."""
    full = deltas_for(Appraisal(goal_congruence=1.0))
    half = deltas_for(Appraisal(goal_congruence=1.0, significance=0.5))

    for dimension in Dimension:
        assert half[dimension] == pytest.approx(full[dimension] / 2)


def test_combining_deltas_sums_them() -> None:
    total = combine([{Dimension.STRESS: 0.1}, {Dimension.STRESS: 0.2}])

    assert total[Dimension.STRESS] == pytest.approx(0.3)


def test_a_neutral_appraisal_produces_no_deltas() -> None:
    assert all(value == 0.0 for value in deltas_for(NEUTRAL).values())
