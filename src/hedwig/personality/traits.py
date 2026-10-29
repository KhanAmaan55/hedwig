"""The trait catalogue and the limits on how it may change (docs/10 §3, §5.3).

Data, not behaviour. The anchors and bounds are the specification of who HEDWIG is at
bootstrap and how far it may ever become something else; they live here, in one table, where
a change to them is a visible diff.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from hedwig.core.ports.personality import Trait


@dataclass(frozen=True, slots=True)
class TraitSpec:
    anchor: float
    floor: float
    ceiling: float


SPECS: dict[Trait, TraitSpec] = {
    # docs/10 §3. The floors and ceilings are narrower than 0..1 on purpose: a HEDWIG with
    # patience 0.05 or conscientiousness 0.2 is not a personality variant, it is a broken
    # product.
    Trait.WARMTH: TraitSpec(anchor=0.70, floor=0.40, ceiling=0.90),
    Trait.HUMOUR: TraitSpec(anchor=0.45, floor=0.10, ceiling=0.80),
    Trait.PATIENCE: TraitSpec(anchor=0.75, floor=0.50, ceiling=0.95),
    Trait.CURIOSITY: TraitSpec(anchor=0.80, floor=0.50, ceiling=0.95),
    Trait.EMPATHY: TraitSpec(anchor=0.70, floor=0.40, ceiling=0.90),
    Trait.ASSERTIVENESS: TraitSpec(anchor=0.55, floor=0.25, ceiling=0.85),
    Trait.FORMALITY: TraitSpec(anchor=0.35, floor=0.10, ceiling=0.85),
    Trait.VERBOSITY: TraitSpec(anchor=0.45, floor=0.15, ceiling=0.80),
    Trait.CONSCIENTIOUSNESS: TraitSpec(anchor=0.85, floor=0.60, ceiling=0.95),
    Trait.PLAYFULNESS: TraitSpec(anchor=0.40, floor=0.10, ceiling=0.75),
}


@dataclass(frozen=True, slots=True)
class DriftLimits:
    """The seven guardrails' numbers (docs/10 §5.3).

    Configurable, unlike emotion's constants, because docs/10 §8 says so explicitly: if users
    find adaptation too slow, relax the caps *in config, not in code*. The defaults are the
    documented ones.
    """

    per_proposal_cap: float = 0.03
    weekly_budget: float = 0.02
    lifetime_cap: float = 0.25
    instruction_step: float = 0.06
    """The explicit-instruction fast path (docs/10 §5.4): a direct request may move a trait
    this far at once, because a companion that ignores a direct request for a month is
    infuriating."""
    min_evidence_items: int = 3
    min_evidence_weight: float = 1.2
    min_evidence_days: int = 2
    oscillation_window: timedelta = timedelta(days=14)
    budget_window: timedelta = timedelta(days=7)


EPSILON = 1e-9
"""Below this a change is not a change. Floating-point dust must not register as drift, nor
as a budget that "remains"."""


def validate_anchors(overrides: dict[Trait, float]) -> dict[Trait, TraitSpec]:
    """Apply configured anchor overrides (docs/10 §6), refusing any outside a trait's bounds.

    An anchor below the floor would make the bootstrap profile itself a violation of the
    guardrails — the first thing drift would do is correct it.
    """
    specs = dict(SPECS)
    for trait, anchor in overrides.items():
        spec = SPECS[trait]
        if not spec.floor <= anchor <= spec.ceiling:
            raise ValueError(
                f"anchor for {trait.value} must be within [{spec.floor}, {spec.ceiling}], "
                f"got {anchor}"
            )
        specs[trait] = TraitSpec(anchor=anchor, floor=spec.floor, ceiling=spec.ceiling)
    return specs
