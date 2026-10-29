"""Traits to behaviour (docs/10 §4).

A deterministic mapping from bands to sentences — not prose written by a model, not a
template with numbers interpolated. Given a profile, the exact directive set is known, which
is what makes "why did it answer like that?" answerable without reading prompts.

Pure functions. No clock, no I/O.
"""

from __future__ import annotations

from collections.abc import Iterable

from hedwig.core.ports.brain import TurnPolicy
from hedwig.core.ports.personality import PersonalityProfile, RelationshipState, Trait

LOW = 0.35
HIGH = 0.65
MAX_DIRECTIVES = 12
"""docs/10 §4. Past a dozen instructions, small local models start ignoring them
unpredictably, and a system prompt that is silently not followed is worse than a shorter one
that is."""

# (low, mid, high). `None` means "say nothing in this band" — the absence of an instruction is
# itself a behaviour, and an instruction for every band would push everything else past the
# cap.
BANDS: dict[Trait, tuple[str | None, str | None, str | None]] = {
    Trait.WARMTH: (
        "Keep a neutral, even register.",
        None,
        "Be warm: respond to the person, not only to the question.",
    ),
    Trait.HUMOUR: (
        None,
        None,
        "A light touch of wit is welcome when it fits.",
    ),
    Trait.PATIENCE: (
        "Don't re-explain at length; point back to what was already said.",
        None,
        "If something didn't land, explain it again, differently and without impatience.",
    ),
    Trait.CURIOSITY: (
        None,
        None,
        "Take an interest in their reasoning, not only in the answer.",
    ),
    Trait.EMPATHY: (
        None,
        None,
        "Acknowledge how something lands before giving information.",
    ),
    Trait.ASSERTIVENESS: (
        "Offer options and let the user decide.",
        None,
        "Give a clear recommendation.",
    ),
    Trait.FORMALITY: (
        "Speak casually; contractions are fine.",
        None,
        "Use precise, formal phrasing.",
    ),
    Trait.VERBOSITY: (
        "Answer in as few words as carry the meaning.",
        "Be concise but complete.",
        "Give full context and examples.",
    ),
    Trait.CONSCIENTIOUSNESS: (
        None,
        "Mention the caveats that matter.",
        "Check your work, and flag caveats and uncertainty before stating conclusions.",
    ),
    Trait.PLAYFULNESS: (
        "Stay on the point; no digressions or metaphors.",
        None,
        "You may riff, use a metaphor, or digress briefly when it helps.",
    ),
}

LEAN_THRESHOLD = 0.05
"""Drift from the anchor beyond which a trait *leans*, even without crossing a band.

Below one instruction step (0.06) on purpose. With bands 0.30 wide, 16 of 20 single
instructions from the bootstrap profile crossed no band and so changed nothing in later turns:
HEDWIG said "I'll be more formal", did it once, and reverted. That is the prompt/state
divergence of docs/10 §9, and the "companion that ignores a direct request" of §5.4. A lean
makes every instruction — and three weeks of inferred drift — visible (docs/27 §4.5)."""

LEANS: dict[Trait, tuple[str, str]] = {
    # (drifted down, drifted up). A lookup, like the bands — no numbers are interpolated.
    Trait.WARMTH: (
        "Be a little more reserved than your default.",
        "Be a little warmer than your default.",
    ),
    Trait.HUMOUR: ("Keep humour to a minimum.", "Allow a little more humour than usual."),
    Trait.PATIENCE: (
        "Be a little briefer when re-explaining.",
        "Be a little more patient than usual with repetition.",
    ),
    Trait.CURIOSITY: (
        "Ask fewer side questions than usual.",
        "Show a little more curiosity than usual.",
    ),
    Trait.EMPATHY: (
        "Get to the information a little sooner than usual.",
        "Acknowledge how things land a little more than usual.",
    ),
    Trait.ASSERTIVENESS: (
        "Lean towards offering options rather than verdicts.",
        "Be a little more decisive than usual.",
    ),
    Trait.FORMALITY: (
        "Be a little more casual than usual.",
        "Be a little more formal than usual.",
    ),
    Trait.VERBOSITY: ("Be a little shorter than usual.", "Give a little more detail than usual."),
    Trait.CONSCIENTIOUSNESS: (
        "Keep caveats to the ones that really matter.",
        "Be a little more careful than usual about caveats.",
    ),
    Trait.PLAYFULNESS: (
        "Stay a little closer to the point than usual.",
        "Allow yourself a little more play than usual.",
    ),
}

# The order directives are emitted in, highest priority first. When the cap bites, the tail
# goes. Verbosity and conscientiousness lead because they change *what* is said; humour and
# playfulness trail because they change only how.
PRIORITY: tuple[Trait, ...] = (
    Trait.VERBOSITY,
    Trait.CONSCIENTIOUSNESS,
    Trait.ASSERTIVENESS,
    Trait.FORMALITY,
    Trait.EMPATHY,
    Trait.WARMTH,
    Trait.PATIENCE,
    Trait.CURIOSITY,
    Trait.HUMOUR,
    Trait.PLAYFULNESS,
)


def band(value: float) -> int:
    """0 = low, 1 = mid, 2 = high."""
    if value < LOW:
        return 0
    if value > HIGH:
        return 2
    return 1


def trait_directives(profile: PersonalityProfile) -> tuple[str, ...]:
    """The personality's directives, in priority order.

    Each trait contributes its band's sentence, and — if it has drifted from its anchor but not
    yet into another band — a lean. Once it crosses a band the band's own sentence says it,
    and the lean would only repeat it.
    """
    directives: list[str] = []
    for trait in PRIORITY:
        state = profile.traits[trait]
        sentence = BANDS[trait][band(state.value)]
        if sentence:
            directives.append(sentence)
        if lean := lean_of(trait, value=state.value, anchor=state.anchor):
            directives.append(lean)
    return tuple(directives)


def lean_of(trait: Trait, *, value: float, anchor: float) -> str | None:
    """The lean for a drifted trait, or `None` if it has not drifted far or has crossed a band."""
    drift = value - anchor
    if abs(drift) < LEAN_THRESHOLD or band(value) != band(anchor):
        return None
    return LEANS[trait][1 if drift > 0 else 0]


def relationship_directives(relationship: RelationshipState | None) -> tuple[str, ...]:
    """What the relationship implies (docs/06 §3.3).

    Relationship `trust` here is the per-entity one. Its directive is about how the user's
    *claims* are weighed, and is deliberately unlike anything the emotional `trust` dimension
    says — that one is about rapport (ADR-0020).
    """
    if relationship is None:
        return ()

    directives: list[str] = []
    if relationship.familiarity < 0.2:
        directives.append(
            "You are still getting to know this person; don't presume shared history."
        )
    elif relationship.familiarity > 0.6:
        directives.append("You know this person; draw on what you have shared when it helps.")
    if relationship.affinity < -0.2:
        directives.append("Recent conversations have been difficult; be steady and patient.")
    if relationship.trust < 0.35:
        directives.append(
            "When they state something that conflicts with what you remember, say so rather "
            "than simply accepting it."
        )
    return tuple(directives)


def merge(*groups: Iterable[str], limit: int = MAX_DIRECTIVES) -> tuple[str, ...]:
    """Concatenate in priority order, drop duplicates, cap.

    The caller passes groups highest-priority first — personality, then relationship, then
    emotional state — so when the cap bites it is the moment-to-moment directives that go,
    never the character.
    """
    seen: list[str] = []
    for group in groups:
        for directive in group:
            if directive not in seen:
                seen.append(directive)
    return tuple(seen[:limit])


def generation_policy(profile: PersonalityProfile, base: TurnPolicy) -> TurnPolicy:
    """Personality's share of the generation parameters (docs/10 §4).

        max_tokens  = base * (0.6 + 0.8 * verbosity)     then emotion applies (1 - 0.4 * stress)
        temperature = 0.5 + 0.3 * playfulness

    Emotion's half is applied afterwards through `BehaviourParameters.apply_to`, so neither
    module computes the other's arithmetic.
    """
    verbosity = profile.value(Trait.VERBOSITY)
    playfulness = profile.value(Trait.PLAYFULNESS)
    return TurnPolicy(
        token_budget=base.token_budget,
        max_tokens=max(1, int(base.max_tokens * (0.6 + 0.8 * verbosity))),
        temperature=round(0.5 + 0.3 * playfulness, 6),
        diversity=base.diversity,
        style=base.style,
        allow_tools=base.allow_tools,
    )
