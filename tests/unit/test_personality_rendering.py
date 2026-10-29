"""Traits to behaviour (docs/10 §4), and explicit instructions (docs/10 §5.4).

Rendering is a pure band lookup, so the exact directive set for a profile is asserted rather
than eyeballed. Detection is tested for *precision* as hard as for recall: a false positive
silently changes who HEDWIG is.
"""

from __future__ import annotations

import pytest

from hedwig.core.ports.brain import TurnPolicy
from hedwig.core.ports.personality import (
    TRAITS,
    IdentityCore,
    PersonalityProfile,
    RelationshipState,
    Trait,
    TraitState,
)
from hedwig.personality.instructions import acknowledgement, detect_instructions
from hedwig.personality.rendering import (
    BANDS,
    MAX_DIRECTIVES,
    generation_policy,
    merge,
    relationship_directives,
    trait_directives,
)
from hedwig.personality.traits import SPECS


def _profile(**values: float) -> PersonalityProfile:
    traits = {}
    for trait in TRAITS:
        spec = SPECS[trait]
        value = values.get(trait.value, spec.anchor)
        traits[trait] = TraitState(
            trait=trait, value=value, anchor=spec.anchor, floor=0.0, ceiling=1.0
        )
    return PersonalityProfile(traits=traits)


# =========================================================================
# INV-2: every trait renders to something
# =========================================================================


@pytest.mark.parametrize("trait", TRAITS, ids=lambda t: t.value)
def test_every_trait_changes_the_rendering_or_the_generation_parameters(trait: Trait) -> None:
    """A trait that changes nothing observable is deleted (docs/10 §3). This is that check."""
    low = _profile(**{trait.value: 0.05})
    high = _profile(**{trait.value: 0.95})

    rendered_differs = trait_directives(low) != trait_directives(high)
    params_differ = generation_policy(low, TurnPolicy()) != generation_policy(high, TurnPolicy())

    assert rendered_differs or params_differ, f"{trait.value} changes nothing"


def _drifted(trait: Trait, value: float) -> PersonalityProfile:
    """A profile with real anchors and bounds, one trait moved."""
    traits = {
        t: TraitState(
            trait=t,
            value=value if t is trait else SPECS[t].anchor,
            anchor=SPECS[t].anchor,
            floor=SPECS[t].floor,
            ceiling=SPECS[t].ceiling,
        )
        for t in TRAITS
    }
    return PersonalityProfile(traits=traits)


@pytest.mark.parametrize("direction", [+1, -1], ids=["up", "down"])
@pytest.mark.parametrize("trait", TRAITS, ids=lambda t: t.value)
def test_no_single_instruction_is_invisible(trait: Trait, direction: int) -> None:
    """Regression: with bands alone, 16 of 20 single instructions from the bootstrap profile
    crossed no band and changed nothing in later turns — HEDWIG said "I'll be more formal",
    did it once, and reverted. Every one must now change what the model is told."""
    spec = SPECS[trait]
    moved = max(spec.floor, min(spec.ceiling, spec.anchor + direction * 0.06))
    if moved == spec.anchor:
        pytest.skip("already at its bound in this direction")

    assert trait_directives(_drifted(trait, moved)) != trait_directives(
        _drifted(trait, spec.anchor)
    )


def test_a_lean_gives_way_to_the_band_once_a_band_is_crossed() -> None:
    """Formality from 0.35 to 0.70 crosses into "formal"; saying "a little more formal" as well
    would be repeating itself."""
    directives = trait_directives(_drifted(Trait.FORMALITY, 0.70))

    assert "Use precise, formal phrasing." in directives
    assert "Be a little more formal than usual." not in directives


def test_the_bootstrap_profile_has_no_leans() -> None:
    from hedwig.personality.rendering import LEANS

    leans = {sentence for pair in LEANS.values() for sentence in pair}
    assert not leans & set(trait_directives(_drifted(Trait.WARMTH, SPECS[Trait.WARMTH].anchor)))


def test_every_trait_has_three_bands() -> None:
    assert set(BANDS) == set(TRAITS)
    assert all(len(bands) == 3 for bands in BANDS.values())


def test_the_bootstrap_personality_renders_a_short_list() -> None:
    """The anchors should produce a character, not a manifesto."""
    directives = trait_directives(_profile())

    assert 3 <= len(directives) <= 8


def test_rendering_is_deterministic() -> None:
    assert trait_directives(_profile()) == trait_directives(_profile())


def test_directives_are_instructions_not_descriptions_of_a_feeling() -> None:
    """A model told "you are warm" narrates it; a model told what to do does it."""
    banned = ("you feel", "you are feeling", "i feel", "your mood")
    for trait in TRAITS:
        for sentence in BANDS[trait]:
            if sentence:
                assert not any(b in sentence.lower() for b in banned), sentence


# =========================================================================
# Generation parameters
# =========================================================================


def test_verbosity_scales_the_reply_budget() -> None:
    """max_tokens = base * (0.6 + 0.8 * verbosity) (docs/10 §4)."""
    terse = generation_policy(_profile(verbosity=0.0), TurnPolicy(max_tokens=1000))
    wordy = generation_policy(_profile(verbosity=1.0), TurnPolicy(max_tokens=1000))

    assert terse.max_tokens == 600
    assert wordy.max_tokens == 1400


def test_playfulness_sets_the_temperature() -> None:
    assert generation_policy(_profile(playfulness=0.0), TurnPolicy()).temperature == 0.5
    assert generation_policy(_profile(playfulness=1.0), TurnPolicy()).temperature == 0.8


def test_personality_never_turns_tools_on_or_off() -> None:
    for value in (0.0, 1.0):
        profile = _profile(assertiveness=value, conscientiousness=value)
        assert generation_policy(profile, TurnPolicy(allow_tools=False)).allow_tools is False


# =========================================================================
# The cap of twelve
# =========================================================================


def test_merging_respects_the_cap() -> None:
    many = [f"directive {i}" for i in range(30)]

    assert len(merge(many)) == MAX_DIRECTIVES


def test_merging_keeps_the_highest_priority_group_when_the_cap_bites() -> None:
    """Character first, the mood of the moment last: when something has to go, the mood goes."""
    character = [f"character {i}" for i in range(10)]
    mood = [f"mood {i}" for i in range(10)]
    merged = merge(character, mood)

    assert merged[:10] == tuple(character)
    assert len([d for d in merged if d.startswith("mood")]) == 2


def test_merging_drops_duplicates_across_groups() -> None:
    assert merge(["a", "b"], ["b", "c"]) == ("a", "b", "c")


# =========================================================================
# The relationship, and the two trusts (ADR-0020)
# =========================================================================


def test_a_stranger_is_not_presumed_upon() -> None:
    directives = relationship_directives(RelationshipState(entity_id="e", familiarity=0.05))

    assert any("getting to know" in d for d in directives)


def test_familiarity_permits_drawing_on_shared_history() -> None:
    directives = relationship_directives(RelationshipState(entity_id="e", familiarity=0.8))

    assert any("shared" in d for d in directives)


def test_relationship_trust_is_about_claims_not_rapport() -> None:
    """ADR-0020: relationship trust governs how claims are weighed; emotional trust governs
    rapport. They must never say the same thing."""
    from hedwig.core.ports.emotion import EmotionState
    from hedwig.emotion.bindings import style_directives

    relational = relationship_directives(RelationshipState(entity_id="e", trust=0.1))
    emotional = style_directives(EmotionState(trust=0.26))

    assert any("conflicts with what you remember" in d for d in relational)
    assert not set(relational) & set(emotional)


def test_no_relationship_means_no_relationship_directives() -> None:
    assert relationship_directives(None) == ()


# =========================================================================
# Identity
# =========================================================================


def test_the_identity_core_renders_its_prohibitions() -> None:
    lines = IdentityCore().lines()

    assert any("Never claim feelings are real" in line for line in lines)
    assert any("HEDWIG" in line for line in lines)


# =========================================================================
# Explicit instructions — precision first
# =========================================================================


@pytest.mark.parametrize(
    ("text", "trait", "direction"),
    [
        ("Can you be more concise?", Trait.VERBOSITY, -1),
        ("please keep it short", Trait.VERBOSITY, -1),
        ("your answers are too long", Trait.VERBOSITY, -1),
        ("you're way too verbose", Trait.VERBOSITY, -1),
        ("be more detailed please", Trait.VERBOSITY, +1),
        ("you are being too formal", Trait.FORMALITY, -1),
        ("could you be more professional", Trait.FORMALITY, +1),
        ("stop joking around", Trait.HUMOUR, -1),
        ("just tell me what to do", Trait.ASSERTIVENESS, +1),
        ("don't tell me what to do", Trait.ASSERTIVENESS, -1),
        ("you're too robotic", Trait.WARMTH, +1),
        ("stay on topic", Trait.PLAYFULNESS, -1),
    ],
)
def test_explicit_instructions_are_recognised(text: str, trait: Trait, direction: int) -> None:
    found = detect_instructions(text)

    assert [(i.trait, i.direction) for i in found] == [(trait, direction)]


@pytest.mark.parametrize(
    "text",
    [
        "that report was too long",
        "the meeting was very formal",
        "I was being too brief in my notes",
        "my manager is too pushy",
        "the movie had too many jokes",
        "keep it short and sweet is a saying",
        "she told me to be more concise in my essays",
        "what does verbose mean?",
    ],
)
def test_descriptions_of_the_world_are_not_instructions(text: str) -> None:
    """A false positive silently changes who HEDWIG is; a false negative means the user asks
    again. The asymmetry decides the trade (docs/27 §4.3)."""
    assert detect_instructions(text) == ()


def test_contradictory_instructions_cancel() -> None:
    """ "Be more concise, and be more detailed" is a request to be better, not a trait change."""
    assert detect_instructions("be more concise, and be more detailed") == ()


def test_a_repeated_instruction_is_one_instruction() -> None:
    assert len(detect_instructions("be more concise. Seriously, be concise.")) == 1


def test_several_traits_in_one_message_are_all_recognised() -> None:
    found = {i.trait for i in detect_instructions("be more concise and stop joking")}

    assert found == {Trait.VERBOSITY, Trait.HUMOUR}


def test_the_acknowledgement_names_the_change_without_scripting_the_words() -> None:
    ack = acknowledgement(detect_instructions("can you be more concise?"))

    assert ack is not None
    assert "more concise" in ack
    assert ack.startswith("They just asked you to")


def test_no_instruction_means_no_acknowledgement() -> None:
    assert acknowledgement(detect_instructions("what's the weather like?")) is None
