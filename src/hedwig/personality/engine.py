"""The personality engine (docs/10, docs/27).

Owns who HEDWIG is: ten traits that shape expression, evolve slowly and only on evidence, and
never evolve past recognition — plus an identity core that does not evolve at all.

Everything that *decides* lives in the pure modules beside this one (`drift.py`,
`rendering.py`, `instructions.py`). This file is the part that talks to the database, the
clock and the bus, and records every decision it makes, including the refusals.

**Reflection proposes, personality decides** (docs/10 §2). `propose` is the slow path, guarded
by all seven rules, and its intended caller — reflection — does not exist yet. `instruct` is
the fast path for an explicit request, and it is live today.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from hedwig.core.logging import fields, get_logger
from hedwig.core.ports import Clock, EventBus, Health, HealthStatus
from hedwig.core.ports.event_bus import Event
from hedwig.core.ports.personality import (
    TRAITS,
    DriftDecision,
    DriftOutcome,
    Evidence,
    IdentityCore,
    PersonalityProfile,
    Trait,
)
from hedwig.personality.drift import evaluate
from hedwig.personality.instructions import detect_instructions
from hedwig.personality.store import PersonalityStore
from hedwig.personality.traits import DriftLimits, validate_anchors

logger = get_logger(__name__)

SUBSCRIPTION = "personality-instructions"
IMMUTABLE_IDENTITY = frozenset({"prohibitions"})
"""docs/10 §3.2: bootstrap only. Not even with consent — a HEDWIG that can be talked out of
"never manipulate" does not have that commitment."""


class IdentityChangeRefusedError(PermissionError):
    """An attempt to change the identity core without consent, or to change what never changes."""


class PersonalityEngine:
    """The service."""

    def __init__(
        self,
        store: PersonalityStore,
        *,
        clock: Clock,
        bus: EventBus | None = None,
        limits: DriftLimits | None = None,
        anchors: Mapping[Trait, float] | None = None,
        identity: IdentityCore | None = None,
    ) -> None:
        self._store = store
        self._clock = clock
        self._bus = bus
        self._limits = limits or DriftLimits()
        self._specs = validate_anchors(dict(anchors or {}))
        self._identity = identity or IdentityCore()
        self._subscribed = False
        self._store.bootstrap(self._specs, self._identity, now=clock.now())
        self._profile = self._store.profile()

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        """Listen for explicit instructions, and tell emotion where its baselines are.

        The initial `profile.updated` is what makes the trait projection of docs/09 §3 live:
        a curious personality idles curious from the first minute, not from the first drift.
        """
        if self._bus is not None and not self._subscribed:
            self._bus.subscribe("conversation.turn.completed", self._on_turn, name=SUBSCRIPTION)
            self._subscribed = True
        await self._announce_profile(reason="startup")

    async def stop(self) -> None:
        return None

    # -- reading -------------------------------------------------------------

    async def profile(self) -> PersonalityProfile:
        return self._profile

    async def history(
        self, *, trait: Trait | None = None, limit: int = 100
    ) -> list[dict[str, object]]:
        return self._store.history(trait=trait, limit=limit)

    async def proposals(self, *, limit: int = 100) -> list[dict[str, object]]:
        return self._store.proposals(limit=limit)

    # -- the slow path ---------------------------------------------------------

    async def propose(
        self,
        trait: Trait | str,
        delta: float,
        *,
        evidence: Sequence[Evidence],
        rationale: str = "",
        correlation_id: str | None = None,
    ) -> DriftDecision:
        """Inferred drift, guarded by all seven rules (docs/10 §5.3).

        The intended caller is reflection (T2/T3), which analyses accumulated evidence. It is
        complete and tested; nothing calls it yet (docs/27 §4.1).
        """
        return await self._decide(
            str(trait.value if isinstance(trait, Trait) else trait),
            delta,
            source="inferred",
            evidence=evidence,
            rationale=rationale,
            correlation_id=correlation_id,
        )

    # -- the fast path ---------------------------------------------------------

    async def instruct(
        self,
        trait: Trait,
        direction: int,
        *,
        phrase: str = "",
        correlation_id: str | None = None,
    ) -> DriftDecision:
        """An explicit request, applied now (docs/10 §5.4).

        Up to `instruction_step` at once, still bounded by the identity core, the floor and
        ceiling, and the lifetime cap — rules 1, 5 and 6. A companion that ignores a direct
        request for a month is infuriating; one that can be talked into anything is worse.
        """
        return await self._decide(
            trait.value,
            (1 if direction > 0 else -1) * self._limits.instruction_step,
            source="instruction",
            evidence=(),
            rationale=f"explicit instruction: {phrase}" if phrase else "explicit instruction",
            correlation_id=correlation_id,
        )

    async def _decide(
        self,
        trait_name: str,
        delta: float,
        *,
        source: str,
        evidence: Sequence[Evidence],
        rationale: str,
        correlation_id: str | None,
    ) -> DriftDecision:
        now = self._clock.now()
        trait = Trait(trait_name) if trait_name in {t.value for t in TRAITS} else None
        state = self._profile.traits.get(trait) if trait else None
        recent = (
            self._store.recent_changes(
                trait,
                since=now - max(self._limits.oscillation_window, self._limits.budget_window),
            )
            if trait
            else []
        )

        verdict = evaluate(
            trait=trait_name,
            delta=delta,
            state=state,
            recent=recent,
            evidence=evidence,
            limits=self._limits,
            now=now,
            instruction=source == "instruction",
        )

        proposal_id = self._store.record_proposal(
            trait=trait_name,
            delta=delta,
            source=source,
            status=verdict.outcome.value,
            rule=verdict.rule,
            rationale=rationale,
            evidence=evidence,
            now=now,
        )

        if verdict.outcome is DriftOutcome.REJECTED or trait is None:
            logger.info(
                "drift rejected",
                extra=fields(trait=trait_name, delta=delta, rule=verdict.rule, source=source),
            )
            await self._emit(
                "personality.drift.rejected",
                {"trait": trait_name, "delta": delta, "rule": verdict.rule, "source": source},
                correlation_id=correlation_id,
            )
            return DriftDecision(
                trait=trait_name,
                outcome=verdict.outcome,
                requested=delta,
                rule=verdict.rule,
                proposal_id=proposal_id,
            )

        history_id = self._store.apply(
            trait, verdict.applied, source=source, proposal_id=proposal_id, now=now
        )
        self._profile = self._store.profile()
        logger.info(
            "trait moved",
            extra=fields(
                trait=trait.value,
                applied=verdict.applied,
                value=self._profile.value(trait),
                source=source,
                clamped_by=verdict.rule,
            ),
        )
        await self._announce_profile(
            reason=f"{source}:{trait.value}", correlation_id=correlation_id
        )
        return DriftDecision(
            trait=trait,
            outcome=verdict.outcome,
            requested=delta,
            applied=verdict.applied,
            rule=verdict.rule,
            proposal_id=proposal_id,
            history_id=history_id,
        )

    # -- reversal (docs/10 §5.5) ---------------------------------------------

    async def revert(self, history_id: str) -> bool:
        """Undo one change. False if it was already undone, or is not a change."""
        reversal = self._store.revert(history_id, now=self._clock.now())
        if reversal is None:
            return False
        self._profile = self._store.profile()
        await self._announce_profile(reason=f"revert:{history_id}")
        return True

    async def reset(self, trait: Trait) -> None:
        self._store.reset(trait, now=self._clock.now())
        self._profile = self._store.profile()
        await self._announce_profile(reason=f"reset:{trait.value}")

    async def reset_all(self) -> None:
        """Every trait back to its anchor. Memory is untouched — this resets who HEDWIG is,
        not what it knows."""
        now = self._clock.now()
        for trait in TRAITS:
            self._store.reset(trait, now=now)
        self._profile = self._store.profile()
        await self._announce_profile(reason="reset:all")

    # -- the identity core -------------------------------------------------------

    async def amend_identity(self, key: str, value: str, *, consent: bool) -> int:
        """Change one identity key. Requires explicit user consent; `prohibitions` never moves.

        The caller is expected to have shown the user a diff (docs/10 §3.2). This method
        cannot check that, so it requires the flag that says it happened — and refuses loudly
        without it, rather than defaulting to permissive.
        """
        if key in IMMUTABLE_IDENTITY:
            raise IdentityChangeRefusedError(f"{key!r} is fixed at bootstrap and never changes")
        if not consent:
            raise IdentityChangeRefusedError(f"changing {key!r} requires explicit user consent")
        if key not in {"name", "pronouns", "self_description", "values"}:
            raise IdentityChangeRefusedError(f"{key!r} is not part of the identity core")

        revision = self._store.amend_identity(key, value, now=self._clock.now())
        self._profile = self._store.profile()
        await self._announce_profile(reason=f"identity:{key}")
        return revision

    # -- events in -----------------------------------------------------------

    async def _on_turn(self, event: Event) -> None:
        """Make an explicit instruction durable.

        On `turn.completed` rather than `message.received`, for two reasons: the turn's status
        is known, so an instruction inside a *refused* input changes nothing; and the reply
        that acknowledged it has already been composed, so the durable change lands from the
        next turn, exactly as the acknowledgement said it would (docs/27 §4.3).
        """
        payload = event.payload
        if str(payload.get("status", "")) == "refused":
            return
        for instruction in detect_instructions(str(payload.get("input", ""))):
            await self.instruct(
                instruction.trait,
                instruction.direction,
                phrase=instruction.phrase,
                correlation_id=event.correlation_id,
            )

    # -- events out ----------------------------------------------------------

    async def _announce_profile(self, *, reason: str, correlation_id: str | None = None) -> None:
        """A full snapshot, never a delta (docs/02 §4.2). Emotion moves its baselines on it."""
        await self._emit(
            "personality.profile.updated",
            {"traits": self._profile.values(), "version": self._profile.version, "reason": reason},
            correlation_id=correlation_id,
        )

    async def _emit(
        self, type_: str, payload: dict[str, Any], *, correlation_id: str | None = None
    ) -> None:
        if self._bus is None:
            return
        try:
            await self._bus.emit(
                type_, payload, source="personality", correlation_id=correlation_id
            )
        except Exception as error:  # a character must not be able to break the process
            logger.warning("could not announce", extra=fields(type=type_, error=str(error)))

    # -- diagnostics ---------------------------------------------------------

    async def health(self) -> Health:
        """Degraded when a trait has spent most of its lifetime budget.

        Not an error — it may be exactly what the user wanted — but it is the signal for
        docs/10 §9's "drift to unrecognisable", and it should be visible before it is final.
        """
        nearly_spent = [
            trait.value
            for trait, state in self._profile.traits.items()
            if state.lifetime_drift >= 0.9 * self._limits.lifetime_cap
        ]
        return Health(
            status=HealthStatus.DEGRADED if nearly_spent else HealthStatus.OK,
            message=f"lifetime drift nearly spent: {', '.join(nearly_spent)}"
            if nearly_spent
            else "",
            detail={**self._profile.values(), **self._store.counts()},
        )
