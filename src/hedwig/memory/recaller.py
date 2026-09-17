"""The brain's `Recaller`, backed by real memory (docs/07 §2).

This is the Milestone-4 claim being cashed in: the brain's nodes were written against a
narrow `Recaller` port with a stub behind it, and switching to real memory is *this file*
plus one line in `wiring.py`. `nodes.py` does not change.

It also does the translation the brain should not have to know about: a `WorkingSet` of
`RetrievedItem`s becomes the `RecalledContext` of `ContextItem`s the graph speaks in.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from hedwig.core.logging import fields, get_logger
from hedwig.core.ports.brain import ContextItem, RecalledContext, TurnPolicy
from hedwig.core.ports.brain import TrustTier as BrainTrust
from hedwig.core.ports.memory import RetrievalPolicy
from hedwig.core.types import TrustTier
from hedwig.memory.retrieval import HybridRetrieval
from hedwig.memory.store import SqliteMemoryStore

logger = get_logger(__name__)

_TRUST_MAP = {
    TrustTier.USER: BrainTrust.USER,
    TrustTier.SELF: BrainTrust.SELF,
    TrustTier.TOOL: BrainTrust.TOOL,
    TrustTier.CURATED: BrainTrust.SELF,
    TrustTier.UNTRUSTED: BrainTrust.UNTRUSTED,
}


@dataclass(slots=True)
class MemoryRecaller:
    """Adapts `RetrievalEngine` to the brain's `Recaller` port."""

    retrieval: HybridRetrieval
    store: SqliteMemoryStore | None = None
    """Optional, and only for the access log. Retrieval is a read; *recording that a
    retrieval happened* is a write, and it is the weakest of the four reinforcement signals
    (docs/06 §7.2). Without a store, recall still works and nothing is reinforced."""

    async def recall(
        self, queries: Sequence[str], *, policy: TurnPolicy, turn_id: str | None = None
    ) -> RecalledContext:
        working_set = await self.retrieval.search(queries, policy=self._translate(policy))

        items = tuple(
            ContextItem(
                text=item.text,
                source=item.provenance.source_ref or item.kind.value,
                score=round(item.score, 6),
                trust=_TRUST_MAP.get(item.provenance.tier, BrainTrust.SELF),
                memory_id=str(item.memory_id),
            )
            for item in working_set.items
        )

        if self.store is not None and working_set.items:
            # `used_in_reply=False`: these were surfaced, not yet shown to work. The turn
            # decides that later, and the subscriber records it (docs/26 §6.2).
            await self.store.record_access(working_set.items, used_in_reply=False, turn_id=turn_id)

        logger.debug(
            "recall complete",
            extra=fields(queries=len(queries), items=len(items), dropped=working_set.dropped),
        )
        return RecalledContext(
            items=items,
            token_count=working_set.token_count,
            dropped=working_set.dropped,
        )

    @staticmethod
    def _translate(policy: TurnPolicy) -> RetrievalPolicy:
        """Turn-level policy into retrieval-level policy.

        The only place the two vocabularies meet. `diversity` carries straight across
        because both mean MMR λ; the token budget is the turn's, so retrieval cannot
        overrun what the turn allowed.
        """
        return RetrievalPolicy(
            token_budget=policy.token_budget,
            diversity=policy.diversity,
        )
