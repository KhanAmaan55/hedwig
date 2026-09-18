"""State to behaviour (docs/09 §6).

The rule this file exists to satisfy: **every dimension must change at least one measurable
behaviour.** A dimension nothing reads is deleted (INV-3). Emotion that only appears in
prose about how HEDWIG feels is decoration, and decoration is worse than nothing because it
invites the user to believe something false.

`tests/architecture/test_conventions.py` parses the table in docs/09 §6 and asserts every
dimension appears here as a source, so the rule is checked rather than intended.

Pure functions of the state. No clock, no I/O, no randomness — the same vector always
produces the same policy, which is what makes the prohibition tests in §6.1 meaningful.
"""

from __future__ import annotations

from hedwig.core.ports.brain import TurnPolicy
from hedwig.core.ports.emotion import BehaviourParameters, EmotionState

# -- thresholds ------------------------------------------------------------
CURIOUS = 0.60
CONFIDENT = 0.70
HEDGING = 0.40
STRESSED = 0.60
TIRED = 0.40
EXHAUSTED = 0.30
PLAYFUL = 0.65
FAMILIAR = 0.60
GUARDED = 0.35


def behaviour(state: EmotionState) -> BehaviourParameters:
    """Everything the state implies, in one object."""
    return BehaviourParameters(
        diversity=round(0.2 + 0.5 * state.curiosity, 6),
        # Low confidence buys more evidence before speaking, rather than more hedging in
        # front of the same evidence.
        token_budget_multiplier=1.3 if state.confidence < HEDGING else 1.0,
        max_tokens_multiplier=round(1.0 - 0.4 * state.stress, 6),
        style=style_directives(state),
        # The governor's switch for non-urgent background work. Nothing reads it yet; it is
        # here because `energy` would otherwise have only a style binding, and a dimension
        # whose sole effect is phrasing is one step from decoration.
        admits_background_work=state.energy >= EXHAUSTED,
    )


def style_directives(state: EmotionState) -> tuple[str, ...]:
    """Behavioural directives for composition (docs/10 §4).

    Directives, not descriptions. "Be concise" is a directive; "you are feeling stressed" is
    a description, and a model handed the second one will narrate a mood at the user, which
    §9 lists as a failure mode by name.
    """
    directives: list[str] = []

    if state.curiosity >= CURIOUS:
        directives.append("Ask a follow-up question when it would genuinely help.")
    if state.confidence >= CONFIDENT:
        directives.append("State conclusions directly; do not hedge what you know.")
    elif state.confidence < HEDGING:
        directives.append("Say plainly where you are unsure.")
    if state.stress >= STRESSED:
        directives.append("Use short, simple sentences.")
    if state.energy < TIRED:
        directives.append("Stay on the point; skip digressions.")
    if state.happiness >= PLAYFUL:
        directives.append("Lightness is welcome where it fits.")
    if state.trust >= FAMILIAR:
        directives.append("Speak personally and directly, as to someone you know well.")
    elif state.trust < GUARDED:
        directives.append("Prefer asking over assuming; check before acting on an inference.")

    return tuple(directives)


def turn_policy(state: EmotionState, *, base: TurnPolicy | None = None) -> TurnPolicy:
    """Project the state onto the only surface the brain reads.

    `base` is the configured default, so this scales what the system was already going to
    do rather than inventing absolute numbers. Emotion modulates; it does not decide.

    What this deliberately does **not** touch (docs/09 §6.1): `allow_tools`. Approval
    requirements are policy, never mood — a confident HEDWIG does not get to skip approval,
    and a stressed one does not get to stop using tools.
    """
    base = base or TurnPolicy()
    params = behaviour(state)

    return TurnPolicy(
        token_budget=int(base.token_budget * params.token_budget_multiplier),
        max_tokens=max(1, int(base.max_tokens * params.max_tokens_multiplier)),
        temperature=base.temperature,
        diversity=params.diversity,
        style=params.style,
        allow_tools=base.allow_tools,
    )
